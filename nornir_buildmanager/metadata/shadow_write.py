"""Opt-in SQLite shadow of ``VolumeData.xml`` container saves (metadata port stage 3).

XML stays the source of truth. After a container's ``VolumeData.xml`` is replaced,
the same container is upserted into ``VolumeData.db`` at the volume root and the
rows just written are compared with the saved file. Child containers, which the
XML holds as ``*_Link`` stubs, keep their existing rows; a link with no rows yet is
loaded from its own folder. A failure or mismatch is logged as a warning naming the
container and never reaches the XML save.
"""

import logging
import os
import sqlite3
from xml.etree import ElementTree

from . import feature_flags
from .migrate import compare_trees
from .sqlite_backend import (
    LINK_SUFFIX,
    SQLiteMetadataBackend,
    delete_nodes,
    immediate_transaction,
    insert_node,
    pair_children,
    stored_nodes,
    sync_node,
    update_node,
)
from .volume_metadata import MetadataNode
from .xml_backend import XMLMetadataBackend

logger = logging.getLogger(__name__)

_VOLUME_DATA_FILENAME = 'VolumeData.xml'
_VOLUME_TAG = 'Volume'

_CHILDREN_SQL = ("SELECT n.id, n.tag, a.value FROM nodes n "
                 "LEFT JOIN node_attribs a ON a.node_id = n.id AND a.key = 'Path' "
                 "WHERE n.parent_id = ? ORDER BY n.sort_order")


def after_container_saved(container_dir: str) -> None:
    """Mirror ``<container_dir>/VolumeData.xml`` into the volume's SQLite shadow when the flag is on.

    Never raises: the XML save has already succeeded.
    """
    if not feature_flags.shadow_sqlite_enabled():
        return
    try:
        _shadow_container(os.path.abspath(container_dir))
    except Exception as e:
        logger.warning("SQLite shadow write failed for container %s: %s", container_dir, e, exc_info=True)


def find_volume_root(container_dir: str) -> str | None:
    """Return the nearest directory at or above *container_dir* whose VolumeData.xml root is a Volume."""
    current = os.path.abspath(container_dir)
    while True:
        if _root_tag(os.path.join(current, _VOLUME_DATA_FILENAME)) == _VOLUME_TAG:
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _root_tag(xml_path: str) -> str | None:
    try:
        with open(xml_path, 'rb') as handle:
            for _, element in ElementTree.iterparse(handle, events=('start',)):
                return element.tag
    except (OSError, ElementTree.ParseError):
        pass
    return None


def _shadow_container(container_dir: str) -> None:
    volume_root = find_volume_root(container_dir)
    if volume_root is None:
        logger.debug("No Volume at or above %s yet; SQLite shadow skipped", container_dir)
        return

    backend = SQLiteMetadataBackend(volume_root)
    # Held through the parity read so no other writer changes the container between upsert and compare.
    with backend.write_lock():
        # Read under the lock: a copy read before it could predate another process's later save of this
        # container, whose shadow may then run first and be overwritten with the older content.
        saved = XMLMetadataBackend(container_dir, single_file=True, xml_filename=_VOLUME_DATA_FILENAME).load()
        if saved is None:
            raise ValueError("the saved VolumeData.xml could not be read back")

        conn = backend._get_connection()
        try:
            backend._maybe_migrate_schema(conn)
            if root_id(conn) is None:
                _bootstrap(conn, volume_root)

            with immediate_transaction(conn):
                node_id = find_container(conn, root_id(conn), volume_root, container_dir)
                if node_id is not None:
                    _upsert_container(conn, node_id, saved, container_dir)

            if node_id is None:
                # Children save before their parent, so a new container is added by the parent's save.
                logger.debug("%s is not linked into the SQLite shadow yet", container_dir)
                return

            for diff in compare_trees(saved, load_container_level(conn, node_id, saved)):
                logger.warning("SQLite shadow parity difference (%s) in container %s at %s: XML=%r SQLite=%r",
                               diff.kind.value, container_dir, diff.path, diff.expected, diff.actual)
        finally:
            conn.close()


def root_id(conn: sqlite3.Connection) -> int | None:
    """Return the row id of the stored Volume root, or None for an empty database."""
    row = conn.execute("SELECT id FROM nodes WHERE parent_id IS NULL ORDER BY id LIMIT 1").fetchone()
    return row[0] if row else None


def _bootstrap(conn: sqlite3.Connection, volume_root: str) -> None:
    """Fill an empty shadow database from the whole sharded XML volume."""
    root = XMLMetadataBackend(volume_root, xml_filename=_VOLUME_DATA_FILENAME).load()
    if root is None:
        raise ValueError(f"volume XML under {volume_root} could not be loaded")
    with immediate_transaction(conn):
        if root_id(conn) is None:
            insert_node(conn, root, None, 0)


def _is_same_or_inside(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory + os.sep)


def find_container(conn: sqlite3.Connection, node_id: int | None, node_dir: str,
                    target_dir: str) -> int | None:
    """Return the row id of the container stored for *target_dir*, descending by ``Path`` attributes."""
    if node_id is None:
        return None
    if node_dir == target_dir:
        return node_id
    for child_id, _, path in conn.execute(_CHILDREN_SQL, (node_id,)).fetchall():
        if path is None:
            continue
        child_dir = os.path.normpath(os.path.join(node_dir, path))
        if child_dir != node_dir and _is_same_or_inside(target_dir, child_dir):
            found = find_container(conn, child_id, child_dir, target_dir)
            if found is not None:
                return found
    return None


def _link_key(tag: str, path: str | None) -> tuple[str, str | None]:
    return tag.removesuffix(LINK_SUFFIX), path


def _load_linked(container_dir: str, stub: MetadataNode) -> MetadataNode:
    """Return the linked container's tree from its own folder, or the stub when it has no readable file."""
    sub_dir = os.path.join(container_dir, stub.attribs.get('Path', ''))
    loaded = XMLMetadataBackend(sub_dir, xml_filename=_VOLUME_DATA_FILENAME).load()
    return loaded if loaded is not None else stub


def _upsert_container(conn: sqlite3.Connection, node_id: int, saved: MetadataNode, container_dir: str) -> None:
    """Write the rows of one container that differ from *saved*, keeping the rows of child containers it links to."""
    (container,) = stored_nodes(conn, 'id = ?', (node_id,))
    update_node(conn, container, saved, container.sort_order)
    paired, new, gone = pair_children(stored_nodes(conn, 'parent_id = ?', (node_id,)), saved.children)
    delete_nodes(conn, gone)
    for order, row, child in paired:
        if not child.tag.endswith(LINK_SUFFIX):
            sync_node(conn, row, child, order)
        elif row.sort_order != order:
            # A stub mirrors attributes the linked container's own save already wrote; only its place changes.
            conn.execute("UPDATE nodes SET sort_order = ? WHERE id = ?", (order, row.id))
    for order, child in new:
        insert_node(conn, _load_linked(container_dir, child) if child.tag.endswith(LINK_SUFFIX) else child,
                    node_id, order)


def _load_node(conn: sqlite3.Connection, node_id: int,
               links: dict[tuple[str, str | None], MetadataNode]) -> MetadataNode:
    tag, text = conn.execute("SELECT tag, text FROM nodes WHERE id = ?", (node_id,)).fetchone()
    attribs = dict(conn.execute("SELECT key, value FROM node_attribs WHERE node_id = ? ORDER BY id",
                                (node_id,)).fetchall())
    node = MetadataNode(tag=tag, attribs=attribs, text=text)
    for child_id, child_tag, path in conn.execute(_CHILDREN_SQL, (node_id,)).fetchall():
        stub = links.get(_link_key(child_tag, path))
        node.children.append(stub if stub is not None else _load_node(conn, child_id, {}))
    return node


def load_container_level(conn: sqlite3.Connection, node_id: int, saved: MetadataNode) -> MetadataNode:
    """Load one container from SQLite with each linked child container replaced by the saved stub.

    Only this container's own rows are read, so the check costs one container, not the volume;
    the order of the linked children is still compared.
    """
    links = {_link_key(c.tag, c.attribs.get('Path')): c for c in saved.children if c.tag.endswith(LINK_SUFFIX)}
    return _load_node(conn, node_id, links)
