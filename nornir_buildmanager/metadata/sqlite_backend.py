"""
SQLite backend for volume metadata storage.

Stores the metadata tree in a single SQLite file at the volume root.
The schema uses a recursive nodes table with a parent_id foreign key,
plus a separate table for node attributes. This flexibly handles
arbitrary XML-like trees without requiring a fixed schema per node type.

Schema versioning is built in so older volumes can be migrated forward.
"""

import contextlib
import logging
import os
import sqlite3
from collections.abc import Iterator
from typing import Optional, Dict, List

from . import sqlite_journal, sqlite_write_lock
from .volume_metadata import MetadataNode, VolumeMetadataBackend

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

DEFAULT_DB_FILENAME = 'VolumeData.db'

# How long a connection waits for another process's lock before raising
# "database is locked"; rollback-journal writes on a network share hold it longer.
BUSY_TIMEOUT_SECONDS = 30.0

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES nodes(id) ON DELETE CASCADE,
    tag       TEXT NOT NULL,
    text      TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS node_attribs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_nodes_tag ON nodes(tag);
CREATE INDEX IF NOT EXISTS idx_attribs_node ON node_attribs(node_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_attribs_node_key ON node_attribs(node_id, key);
"""


@contextlib.contextmanager
def immediate_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Run the body in one ``BEGIN IMMEDIATE`` transaction: committed on success, rolled back on any exception."""
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


class SQLiteMetadataBackend(VolumeMetadataBackend):
    """Read/write volume metadata from/to a SQLite database file."""

    def __init__(self, volume_path: str, db_filename: str = DEFAULT_DB_FILENAME):
        self._volume_path = volume_path
        self._db_filename = db_filename
        self._db_path = os.path.join(volume_path, db_filename)

    @property
    def db_path(self) -> str:
        return self._db_path

    def exists(self) -> bool:
        return os.path.isfile(self._db_path)

    def get_backend_type(self) -> str:
        return 'sqlite'

    def get_schema_version(self) -> int:
        """Return the stored schema version, or 0 if there is no readable database."""
        if not self.exists():
            return 0
        try:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT value FROM schema_info WHERE key='schema_version'").fetchone()
            finally:
                conn.close()
            if row:
                return int(row[0])
        except Exception:
            pass
        return 0

    def write_lock(self) -> contextlib.AbstractContextManager[None]:
        """Return the cross-process lock a multi-statement write to this database must hold."""
        return sqlite_write_lock.write_lock(self._db_path, BUSY_TIMEOUT_SECONDS)

    def _get_connection(self) -> sqlite3.Connection:
        """Open the database with a busy timeout and the journal mode its filesystem allows."""
        conn = sqlite3.connect(self._db_path, timeout=BUSY_TIMEOUT_SECONDS)
        sqlite_journal.apply_journal_mode(conn, self._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO schema_info (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),)
        )
        conn.commit()

    def load(self) -> Optional[MetadataNode]:
        if not self.exists():
            return None

        conn = self._get_connection()
        try:
            self._maybe_migrate_schema(conn)
            # One read transaction, so both queries see the same commit of a concurrent writer.
            conn.execute('BEGIN')

            cur = conn.execute(
                "SELECT id, parent_id, tag, text, sort_order FROM nodes ORDER BY sort_order"
            )
            rows = cur.fetchall()
            if not rows:
                return None

            cur_attribs = conn.execute(
                "SELECT node_id, key, value FROM node_attribs ORDER BY id"
            )
            attrib_map: Dict[int, Dict[str, str]] = {}
            for node_id, key, value in cur_attribs.fetchall():
                attrib_map.setdefault(node_id, {})[key] = value

            node_map: Dict[int, MetadataNode] = {}
            children_map: Dict[int, List[MetadataNode]] = {}
            root_node = None

            for row_id, parent_id, tag, text, sort_order in rows:
                node = MetadataNode(
                    tag=tag,
                    attribs=attrib_map.get(row_id, {}),
                    text=text,
                    db_id=row_id
                )
                node_map[row_id] = node

                if parent_id is None:
                    root_node = node
                else:
                    children_map.setdefault(parent_id, []).append(node)

            for nid, node in node_map.items():
                node.children = children_map.get(nid, [])

            return root_node
        finally:
            conn.close()

    def save(self, root: MetadataNode) -> None:
        """Replace the whole stored tree with *root* in one transaction under the write lock.

        A failure part way leaves the previous tree; readers never see it half written.
        """
        os.makedirs(self._volume_path, exist_ok=True)

        with self.write_lock():
            conn = self._get_connection()
            try:
                self._ensure_schema(conn)
                with immediate_transaction(conn):
                    conn.execute("DELETE FROM node_attribs")
                    conn.execute("DELETE FROM nodes")
                    self._insert_node(conn, root, parent_id=None, sort_order=0)
            finally:
                conn.close()

    def _insert_node(self, conn: sqlite3.Connection, node: MetadataNode,
                     parent_id: Optional[int], sort_order: int) -> int:
        cur = conn.execute(
            "INSERT INTO nodes (parent_id, tag, text, sort_order) VALUES (?, ?, ?, ?)",
            (parent_id, node.tag, node.text, sort_order)
        )
        node_id = cur.lastrowid

        if node.attribs:
            conn.executemany(
                "INSERT INTO node_attribs (node_id, key, value) VALUES (?, ?, ?)",
                [(node_id, k, v) for k, v in node.attribs.items()]
            )

        for i, child in enumerate(node.children):
            self._insert_node(conn, child, parent_id=node_id, sort_order=i)

        return node_id

    def _maybe_migrate_schema(self, conn: sqlite3.Connection) -> None:
        """Migrate the database schema if it is older than the current version.
        Designed to be extended as new schema versions are added."""
        try:
            cur = conn.execute("SELECT value FROM schema_info WHERE key='schema_version'")
            row = cur.fetchone()
            current_version = int(row[0]) if row else 0
        except sqlite3.OperationalError:
            self._ensure_schema(conn)
            return

        if current_version < SCHEMA_VERSION:
            logger.info("Migrating SQLite schema from version %d to %d",
                        current_version, SCHEMA_VERSION)
            self._ensure_schema(conn)
