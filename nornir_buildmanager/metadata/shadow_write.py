"""SQLite shadow of ``VolumeData.xml`` container saves (metadata port stage 3; on by default since
stage 6, switched off with ``NORNIR_VOLUME_METADATA_SHADOW_SQLITE=0``).

XML stays the source of truth. After a container's ``VolumeData.xml`` is replaced,
the same container is upserted into ``VolumeData.db`` at the volume root. With
``NORNIR_VOLUME_METADATA_SHADOW_VERIFY`` on, the rows just written are also reloaded
and compared with the saved file. Child containers, which the
XML holds as ``*_Link`` stubs, keep their existing rows; a link with no rows yet is
loaded from its own folder. A failure or mismatch is logged as a warning naming the
container and never reaches the XML save.

Each sync also records the file's ``(size, mtime_ns)``. When a container is loaded,
:func:`after_container_loaded` compares the file it just read with that signature and
re-syncs the container from the bytes read when they differ, so a hand-edited
``VolumeData.xml`` reaches the database on its next load instead of never.
"""

import contextlib
import logging
import os
import sqlite3
import threading
from collections.abc import Iterator
from xml.etree import ElementTree

from . import feature_flags
from .migrate import compare_trees
from .sqlite_backend import (
    LINK_SUFFIX,
    SIGNATURES_TABLE_SQL,
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

# Finding the volume root was 41% of the shadow cost: every container save walked up and re-parsed
# each ancestor's VolumeData.xml. Each container folder's volume root is remembered and re-checked
# alone, and root tags are cached by path, size and mtime, so a rewritten file is parsed again once.
_root_tags: dict[str, tuple[tuple[int, int], str | None]] = {}
_volume_roots: dict[str, str] = {}
_known_volume_roots: set[str] = set()

# Signatures this process knows for each volume's containers, read from the database once per
# volume and kept current by this process's own syncs, so a load whose file matches costs a
# dictionary lookup and no database access. None marks a volume with no database yet.
_known_signatures: dict[str, dict[str, tuple[int, int]] | None] = {}
_known_signatures_lock = threading.Lock()


class _Session(threading.local):
    """Per-thread state of :func:`save_session`: nesting depth and one open connection per database."""

    depth: int = 0
    connections: dict[str, sqlite3.Connection]

    def __init__(self) -> None:
        self.connections = {}


_session = _Session()


@contextlib.contextmanager
def save_session() -> Iterator[None]:
    """Reuse one SQLite connection per database for the container saves of one ``Save`` call.

    Opening a connection and setting its journal mode for every container was 30% of the shadow
    cost. Connections are opened lazily, used only by this thread, and closed when the outermost
    session ends, so none stays open after the save. Nested sessions join the outer one.
    """
    _session.depth += 1
    try:
        yield
    finally:
        _session.depth -= 1
        if _session.depth == 0:
            connections, _session.connections = _session.connections, {}
            for conn in connections.values():
                conn.close()

_CHILDREN_SQL = ("SELECT n.id, n.tag, a.value FROM nodes n "
                 "LEFT JOIN node_attribs a ON a.node_id = n.id AND a.key = 'Path' "
                 "WHERE n.parent_id = ? ORDER BY n.sort_order")


FileSignature = tuple[int, int]


def file_signature(path: str) -> FileSignature | None:
    """``(size, mtime_ns)`` of *path*, or None when it cannot be stat'ed."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def container_key(volume_root: str, container_dir: str) -> str:
    """The ``container_signatures`` key of *container_dir*: its path below *volume_root* with '/' separators."""
    rel = os.path.relpath(container_dir, volume_root)
    return rel.replace(os.sep, '/')


def after_container_loaded(container_dir: str, loaded_xml: bytes,
                           loaded_signature: FileSignature | None) -> None:
    """Re-sync a container whose ``VolumeData.xml`` changed since its rows were last synced.

    *loaded_xml* is the file's bytes as just read and *loaded_signature* its ``(size, mtime_ns)``
    from the open handle. A file whose signature matches the one recorded at the last sync costs
    a dictionary lookup. Otherwise the stored row is read once, and only when it differs too (a
    hand edit, an older build that saved without signatures, or a container never signed) is the
    container upserted from *loaded_xml*. Does nothing when the shadow is off or the volume has no
    database yet. Never raises: the load has already succeeded.
    """
    if not feature_flags.shadow_sqlite_enabled() or loaded_signature is None:
        return
    try:
        _check_loaded_container(os.path.abspath(container_dir), loaded_xml, loaded_signature)
    except Exception as e:
        logger.warning("SQLite shadow check failed for loaded container %s: %s", container_dir, e, exc_info=True)


def _cached_volume_root(container_dir: str) -> str | None:
    """Volume root of *container_dir* from roots already found, without touching the share; else search."""
    known = _volume_roots.get(container_dir)
    if known is not None:
        return known
    best = None
    for root in _known_volume_roots:
        if _is_same_or_inside(container_dir, root) and (best is None or len(root) > len(best)):
            best = root
    return best if best is not None else find_volume_root(container_dir)


def _volume_signatures(volume_root: str) -> dict[str, tuple[int, int]] | None:
    """This process's signature map for *volume_root*, read from its database the first time."""
    with _known_signatures_lock:
        if volume_root in _known_signatures:
            return _known_signatures[volume_root]
    backend = SQLiteMetadataBackend(volume_root)
    signatures: dict[str, tuple[int, int]] | None = None
    if backend.exists():
        conn = backend._get_connection()
        try:
            signatures = _read_signatures(conn)
        finally:
            conn.close()
    with _known_signatures_lock:
        return _known_signatures.setdefault(volume_root, signatures)


def _read_signatures(conn: sqlite3.Connection, key: str | None = None) -> dict[str, tuple[int, int]]:
    """Stored signatures, all or only *key*'s; empty when the table does not exist yet."""
    sql = "SELECT path, size, mtime_ns FROM container_signatures"
    try:
        rows = (conn.execute(sql + " WHERE path = ?", (key,)) if key is not None else conn.execute(sql)).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {path: (size, mtime_ns) for path, size, mtime_ns in rows}


def _store_signatures(conn: sqlite3.Connection, volume_root: str, signatures: dict[str, FileSignature]) -> None:
    """Record the signatures of container files whose rows were just written from them, in the caller's transaction."""
    rows = [(container_key(volume_root, container_dir), *signature) for container_dir, signature in signatures.items()]
    conn.executemany("INSERT OR REPLACE INTO container_signatures (path, size, mtime_ns) VALUES (?, ?, ?)", rows)
    for key, size, mtime_ns in rows:
        _remember_signature(volume_root, key, (size, mtime_ns))


def _remember_signature(volume_root: str, key: str, signature: FileSignature) -> None:
    with _known_signatures_lock:
        known = _known_signatures.get(volume_root)
        if known is None:
            known = _known_signatures[volume_root] = {}
        known[key] = signature


def _check_loaded_container(container_dir: str, loaded_xml: bytes, signature: FileSignature) -> None:
    volume_root = _cached_volume_root(container_dir)
    if volume_root is None:
        return
    known = _volume_signatures(volume_root)
    if known is None:
        return  # No database yet; the first save bootstraps it from the files.
    key = container_key(volume_root, container_dir)
    if known.get(key) == signature:
        return

    # Another process may have synced the file since this process read the table.
    backend = SQLiteMetadataBackend(volume_root)
    conn = backend._get_connection()
    try:
        stored = _read_signatures(conn, key).get(key)
    finally:
        conn.close()
    if stored != signature:
        synced = _shadow_container(container_dir, loaded_xml, signature)
        if synced and stored is not None:
            logger.info("VolumeData.xml for %s changed outside the pipeline; SQLite copy refreshed", container_dir)
        elif synced:
            logger.debug("VolumeData.xml for %s had no recorded signature; SQLite copy synced", container_dir)
    # Remembered even when the container is not linked into the database yet, so later loads in
    # this process do not retry it; its parent's save adds it.
    _remember_signature(volume_root, key, signature)


def after_container_saved(container_dir: str,
                          saved_xml: bytes | None = None,
                          written_signature: FileSignature | None = None) -> None:
    """Mirror ``<container_dir>/VolumeData.xml`` into the volume's SQLite shadow unless the flag is off.

    *saved_xml* is the exact bytes just written and *written_signature* the file's
    signature right after the write. When the file still has that signature under the
    shadow lock, those bytes are parsed instead of reading the file back.

    Never raises: the XML save has already succeeded.
    """
    if not feature_flags.shadow_sqlite_enabled():
        return
    try:
        _shadow_container(os.path.abspath(container_dir), saved_xml, written_signature)
    except Exception as e:
        logger.warning("SQLite shadow write failed for container %s: %s", container_dir, e, exc_info=True)


def find_volume_root(container_dir: str) -> str | None:
    """Return the nearest directory at or above *container_dir* whose VolumeData.xml root is a Volume."""
    start = current = os.path.abspath(container_dir)
    known = _volume_roots.get(start)
    if known is not None and _root_tag(os.path.join(known, _VOLUME_DATA_FILENAME)) == _VOLUME_TAG:
        return known
    while True:
        if _root_tag(os.path.join(current, _VOLUME_DATA_FILENAME)) == _VOLUME_TAG:
            _volume_roots[start] = current
            _known_volume_roots.add(current)
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _root_tag(xml_path: str) -> str | None:
    """Return the root element tag of *xml_path*, or None when it is missing or unreadable."""
    try:
        stat = os.stat(xml_path)
    except OSError:
        return None
    signature = (stat.st_size, stat.st_mtime_ns)
    cached = _root_tags.get(xml_path)
    if cached is not None and cached[0] == signature:
        return cached[1]
    tag = None
    try:
        with open(xml_path, 'rb') as handle:
            for _, element in ElementTree.iterparse(handle, events=('start',)):
                tag = element.tag
                break
    except (OSError, ElementTree.ParseError):
        pass
    _root_tags[xml_path] = (signature, tag)
    return tag


def _shadow_container(container_dir: str,
                      saved_xml: bytes | None = None,
                      written_signature: FileSignature | None = None) -> bool:
    """Upsert one container and record its file signature; True when the container has rows to sync."""
    volume_root = find_volume_root(container_dir)
    if volume_root is None:
        logger.debug("No Volume at or above %s yet; SQLite shadow skipped", container_dir)
        return False

    backend = SQLiteMetadataBackend(volume_root)
    # Held through the parity read so no other writer changes the container between upsert and compare.
    with backend.write_lock():
        # Read under the lock: a copy read before it could predate another process's later save of this
        # container, whose shadow may then run first and be overwritten with the older content. The
        # bytes this process just wrote are that same content as long as the file is unchanged, so
        # they stand in for reading the file back over the share when the signature still matches.
        xml_backend = XMLMetadataBackend(container_dir, single_file=True, xml_filename=_VOLUME_DATA_FILENAME)
        # Taken before any read, so a file rewritten in between is re-synced on its next load
        # rather than recorded with a signature newer than the rows.
        current_signature = file_signature(os.path.join(container_dir, _VOLUME_DATA_FILENAME))
        if saved_xml is not None and written_signature is not None and current_signature == written_signature:
            saved = xml_backend.node_from_element(ElementTree.fromstring(saved_xml))
        else:
            saved = xml_backend.load()
        if saved is None:
            raise ValueError("the saved VolumeData.xml could not be read back")

        in_session = _session.depth > 0
        conn = _session.connections.get(backend.db_path) if in_session else None
        fresh = conn is None
        if conn is None:
            conn = backend._get_connection()
            if in_session:
                _session.connections[backend.db_path] = conn
        failed = True
        try:
            if fresh:
                backend._maybe_migrate_schema(conn)
                conn.execute(SIGNATURES_TABLE_SQL)
            if root_id(conn) is None:
                _bootstrap(conn, volume_root)

            with immediate_transaction(conn):
                node_id = find_container(conn, root_id(conn), volume_root, container_dir)
                if node_id is not None:
                    signatures = _upsert_container(conn, node_id, saved, container_dir)
                    if current_signature is not None:
                        signatures[container_dir] = current_signature
                    _store_signatures(conn, volume_root, signatures)

            if node_id is None:
                # Children save before their parent, so a new container is added by the parent's save.
                logger.debug("%s is not linked into the SQLite shadow yet", container_dir)
            elif feature_flags.shadow_verify_enabled():
                for diff in compare_trees(saved, load_container_level(conn, node_id, saved)):
                    logger.warning("SQLite shadow parity difference (%s) in container %s at %s: XML=%r SQLite=%r",
                                   diff.kind.value, container_dir, diff.path, diff.expected, diff.actual)
            failed = False
        finally:
            if failed or not in_session:
                # A connection that raised is not reused; the next container opens a fresh one.
                _session.connections.pop(backend.db_path, None)
                conn.close()
    return node_id is not None


def root_id(conn: sqlite3.Connection) -> int | None:
    """Return the row id of the stored Volume root, or None for an empty database."""
    row = conn.execute("SELECT id FROM nodes WHERE parent_id IS NULL ORDER BY id LIMIT 1").fetchone()
    return row[0] if row else None


def _bootstrap(conn: sqlite3.Connection, volume_root: str) -> None:
    """Fill an empty shadow database from the whole sharded XML volume, signing every container file read."""
    signatures: dict[str, FileSignature] = {}
    root = XMLMetadataBackend(volume_root, xml_filename=_VOLUME_DATA_FILENAME, signatures=signatures).load()
    if root is None:
        raise ValueError(f"volume XML under {volume_root} could not be loaded")
    with immediate_transaction(conn):
        if root_id(conn) is None:
            insert_node(conn, root, None, 0)
            _store_signatures(conn, volume_root, signatures)


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


def _load_linked(container_dir: str, stub: MetadataNode, signatures: dict[str, FileSignature]) -> MetadataNode:
    """Return the linked container's tree from its own folder, or the stub when it has no readable file.

    The signature of every container file read is added to *signatures*.
    """
    sub_dir = os.path.join(container_dir, stub.attribs.get('Path', ''))
    loaded = XMLMetadataBackend(sub_dir, xml_filename=_VOLUME_DATA_FILENAME, signatures=signatures).load()
    return loaded if loaded is not None else stub


def _upsert_container(conn: sqlite3.Connection, node_id: int, saved: MetadataNode,
                      container_dir: str) -> dict[str, FileSignature]:
    """Write the rows of one container that differ from *saved*, keeping the rows of child containers it links to.

    Returns the signatures of the linked container files read to insert new children.
    """
    signatures: dict[str, FileSignature] = {}
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
        insert_node(conn, _load_linked(container_dir, child, signatures) if child.tag.endswith(LINK_SUFFIX) else child,
                    node_id, order)
    return signatures


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
