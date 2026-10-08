"""Opt-in read of container meta-data from SQLite (metadata port stage 5).

XML stays the source of truth. With ``NORNIR_VOLUME_METADATA_READ_SQLITE`` on, a
container's ``VolumeData.xml`` is still parsed; then its rows in the volume's
``VolumeData.db`` (kept current by the stage 3 shadow write) are loaded, with linked
child containers taken as the file's ``*_Link`` stubs, and compared with the parsed
file. The element handed to the wrapper is built from those rows only when nothing
differs. A missing database, a schema version other than this code's (an older one
is migrated first), a container with no rows, or any difference falls back to the
parsed XML with a warning naming the container.

The database does not record which children are linked shards, so the parsed file
supplies the stubs as well as the check. Pipeline XPath still runs on the returned
element tree; nothing is pushed down into SQL.
"""

import logging
import os
import sqlite3
import threading
from xml.etree import ElementTree

from . import feature_flags, shadow_write
from .migrate import compare_trees
from .sqlite_backend import SCHEMA_VERSION, SQLiteMetadataBackend
from .volume_metadata import MetadataNode
from .xml_backend import XMLMetadataBackend

logger = logging.getLogger(__name__)

_warned: set[tuple[str, str]] = set()
_warned_lock = threading.Lock()


class _Fallback(Exception):
    """Why a container is read from VolumeData.xml; repeats of the warning are suppressed per *scope* directory."""
    scope: str

    def __init__(self, scope: str, reason: str) -> None:
        super().__init__(reason)
        self.scope = scope


def load_container_element(container_dir: str, xml_root: ElementTree.Element) -> ElementTree.Element:
    """Return the container's tree built from SQLite when the flag is on and its rows match *xml_root*.

    *xml_root* is the parsed ``<container_dir>/VolumeData.xml`` and is returned unchanged
    when the flag is off or the database cannot be used. Never raises.
    """
    if not feature_flags.read_sqlite_enabled():
        return xml_root
    container_dir = os.path.abspath(container_dir)
    try:
        return _load_from_sqlite(container_dir, xml_root)
    except _Fallback as fallback:
        logger.log(_level(fallback.scope, str(fallback)), "Reading VolumeData.xml for container %s: %s",
                   container_dir, fallback)
    except Exception as e:
        logger.log(_level(container_dir, repr(e)), "Reading VolumeData.xml for container %s: SQLite read failed: %r",
                   container_dir, e, exc_info=True)
    return xml_root


def _level(scope: str, reason: str) -> int:
    """Return WARNING the first time *reason* applies to *scope*, and DEBUG after that.

    Every container load of a volume without a usable database falls back, so an
    unthrottled warning would repeat once per container.
    """
    with _warned_lock:
        first = (scope, reason) not in _warned
        _warned.add((scope, reason))
    return logging.WARNING if first else logging.DEBUG


def _as_node(element: ElementTree.Element) -> MetadataNode:
    """Return *element* as a MetadataNode exactly as parsed, without the XML backend's legacy attribute renames."""
    return MetadataNode(tag=element.tag, attribs=dict(element.attrib), text=element.text,
                        children=[_as_node(child) for child in element])


def _schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM schema_info WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def _load_from_sqlite(container_dir: str, xml_root: ElementTree.Element) -> ElementTree.Element:
    """Build the container's element from its SQLite rows, or raise _Fallback when they cannot stand in for *xml_root*."""
    volume_root = shadow_write.find_volume_root(container_dir)
    if volume_root is None:
        raise _Fallback(container_dir, "no Volume VolumeData.xml at or above it")
    backend = SQLiteMetadataBackend(volume_root)
    if not backend.exists():
        raise _Fallback(volume_root, f"{backend.db_path} does not exist")
    if any(element.tail and element.tail.strip() for element in xml_root.iter()):
        raise _Fallback(container_dir, "it has text after an element, which the database does not store")

    expected = _as_node(xml_root)
    conn = backend._get_connection()
    try:
        version = _schema_version(conn)
        if version < SCHEMA_VERSION:
            with backend.write_lock():
                backend._maybe_migrate_schema(conn)
            raise _Fallback(volume_root, f"schema version {version} was older than {SCHEMA_VERSION}; migrated")
        if version > SCHEMA_VERSION:
            raise _Fallback(volume_root, f"schema version {version} is newer than this code's {SCHEMA_VERSION}")

        # One read transaction, so the container lookup and its rows see the same commit of a concurrent writer.
        conn.execute('BEGIN')
        node_id = shadow_write.find_container(conn, shadow_write.root_id(conn), volume_root, container_dir)
        if node_id is None:
            raise _Fallback(container_dir, "it has no rows in the database")
        stored = shadow_write.load_container_level(conn, node_id, expected)
    finally:
        conn.close()

    differences = compare_trees(expected, stored)
    if differences:
        first = differences[0]
        raise _Fallback(container_dir, f"{len(differences)} parity difference(s) with the database, first "
                                       f"{first.kind.value} at {first.path}: XML={first.expected!r} "
                                       f"SQLite={first.actual!r}")
    return XMLMetadataBackend(container_dir)._node_to_element(stored)
