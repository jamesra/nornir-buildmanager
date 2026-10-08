"""
SQLite backend for volume metadata storage.

Stores the metadata tree in a single SQLite file at the volume root.
The schema uses a recursive nodes table with a parent_id foreign key,
plus a separate table for node attributes. This flexibly handles
arbitrary XML-like trees without requiring a fixed schema per node type.

Schema versioning is built in so older volumes can be migrated forward.
"""

import collections
import contextlib
import dataclasses
import logging
import os
import sqlite3
from collections.abc import Iterator, Mapping
from typing import Optional, Dict, List

from . import sqlite_journal, sqlite_write_lock
from .volume_metadata import MetadataNode, VolumeMetadataBackend

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

DEFAULT_DB_FILENAME = 'VolumeData.db'

# How long a connection waits for another process's lock before raising
# "database is locked"; rollback-journal writes on a network share hold it longer.
BUSY_TIMEOUT_SECONDS = 30.0

LINK_SUFFIX = '_Link'

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


@dataclasses.dataclass
class StoredNode:
    """One ``nodes`` row with its ``node_attribs`` rows as ``(attrib id, key, value)`` in load order."""
    id: int
    tag: str
    text: str | None
    sort_order: int
    attribs: list[tuple[int, str, str]]


def pairing_key(tag: str, attribs: Mapping[str, str]) -> tuple[str, str | None, str | None]:
    """Identify a child among its siblings: by ``Path`` when it has one (a linked stub and its
    container share it), otherwise by ``Name``; the ``_Link`` suffix is ignored."""
    path = attribs.get('Path')
    return tag.removesuffix(LINK_SUFFIX), path, attribs.get('Name') if path is None else None


def stored_nodes(conn: sqlite3.Connection, where: str, params: tuple = ()) -> list[StoredNode]:
    """Return the rows matching the ``nodes`` filter *where*, ordered by ``sort_order``, with their attributes."""
    nodes = {row[0]: StoredNode(*row, attribs=[]) for row in conn.execute(
        f"SELECT id, tag, text, sort_order FROM nodes WHERE {where} ORDER BY sort_order, id", params)}
    for node_id, attrib_id, key, value in conn.execute(
            f"SELECT node_id, id, key, value FROM node_attribs "
            f"WHERE node_id IN (SELECT id FROM nodes WHERE {where}) ORDER BY id", params):
        nodes[node_id].attribs.append((attrib_id, key, value))
    return list(nodes.values())


def insert_node(conn: sqlite3.Connection, node: MetadataNode, parent_id: int | None, sort_order: int) -> int:
    """Insert *node* and its whole subtree under *parent_id*; return the new row id."""
    cur = conn.execute("INSERT INTO nodes (parent_id, tag, text, sort_order) VALUES (?, ?, ?, ?)",
                       (parent_id, node.tag, node.text, sort_order))
    node_id = cur.lastrowid
    assert node_id is not None
    if node.attribs:
        conn.executemany("INSERT INTO node_attribs (node_id, key, value) VALUES (?, ?, ?)",
                         [(node_id, k, v) for k, v in node.attribs.items()])
    for i, child in enumerate(node.children):
        insert_node(conn, child, node_id, i)
    return node_id


def delete_nodes(conn: sqlite3.Connection, node_ids: list[int]) -> None:
    """Delete the rows *node_ids*; their attributes and descendants go by ``ON DELETE CASCADE``."""
    conn.executemany("DELETE FROM nodes WHERE id = ?", [(i,) for i in node_ids])


def update_node(conn: sqlite3.Connection, stored: StoredNode, node: MetadataNode, sort_order: int) -> None:
    """Rewrite only the parts of the *stored* row and its attributes that differ from *node*; children are left alone."""
    if (stored.tag, stored.text, stored.sort_order) != (node.tag, node.text, sort_order):
        conn.execute("UPDATE nodes SET tag = ?, text = ?, sort_order = ? WHERE id = ?",
                     (node.tag, node.text, sort_order, stored.id))
    if [(k, v) for _, k, v in stored.attribs] == list(node.attribs.items()):
        return
    if [k for _, k, _ in stored.attribs] == list(node.attribs):
        conn.executemany("UPDATE node_attribs SET value = ? WHERE id = ?",
                         [(node.attribs[k], i) for i, k, v in stored.attribs if node.attribs[k] != v])
    else:
        # Load returns attributes in row id order, so a changed key list is rewritten whole to keep XML order.
        conn.execute("DELETE FROM node_attribs WHERE node_id = ?", (stored.id,))
        conn.executemany("INSERT INTO node_attribs (node_id, key, value) VALUES (?, ?, ?)",
                         [(stored.id, k, v) for k, v in node.attribs.items()])


def pair_children(stored: list[StoredNode], children: list[MetadataNode]
                  ) -> tuple[list[tuple[int, StoredNode, MetadataNode]], list[tuple[int, MetadataNode]], list[int]]:
    """Pair each child, in order, with the first unclaimed stored sibling of the same pairing_key.

    Returns ``(sort order, row, child)`` for each pair, ``(sort order, child)`` for children with
    no row, and the ids of rows no child claimed.
    """
    unclaimed: dict[tuple, collections.deque[StoredNode]] = {}
    for row in stored:
        unclaimed.setdefault(pairing_key(row.tag, {k: v for _, k, v in row.attribs}),
                             collections.deque()).append(row)
    paired: list[tuple[int, StoredNode, MetadataNode]] = []
    new: list[tuple[int, MetadataNode]] = []
    for order, child in enumerate(children):
        rows = unclaimed.get(pairing_key(child.tag, child.attribs))
        if rows:
            paired.append((order, rows.popleft(), child))
        else:
            new.append((order, child))
    return paired, new, [row.id for rows in unclaimed.values() for row in rows]


def sync_node(conn: sqlite3.Connection, stored: StoredNode, node: MetadataNode, sort_order: int) -> None:
    """Make the stored subtree at *stored* equal *node*, writing only the rows that differ."""
    update_node(conn, stored, node, sort_order)
    paired, new, gone = pair_children(stored_nodes(conn, 'parent_id = ?', (stored.id,)), node.children)
    delete_nodes(conn, gone)
    for order, row, child in paired:
        sync_node(conn, row, child, order)
    for order, child in new:
        insert_node(conn, child, stored.id, order)


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
        """Make the stored tree equal *root* in one transaction under the write lock.

        Only rows that differ are written, so unchanged nodes keep their row ids; an empty
        database gets one full insert. A failure part way leaves the previous tree, and
        readers never see it half written.
        """
        os.makedirs(self._volume_path, exist_ok=True)

        with self.write_lock():
            conn = self._get_connection()
            try:
                self._ensure_schema(conn)
                with immediate_transaction(conn):
                    roots = stored_nodes(conn, 'parent_id IS NULL')
                    if roots:
                        sync_node(conn, roots[0], root, 0)
                        delete_nodes(conn, [r.id for r in roots[1:]])
                    else:
                        insert_node(conn, root, None, 0)
            finally:
                conn.close()

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
