"""
Migration tool for converting volume metadata from XML to SQLite.

Usage as a module:
    from nornir_buildmanager.metadata.migrate import migrate_volume
    migrate_volume('/path/to/volume')

Usage from command line:
    python -m nornir_buildmanager.metadata.migrate /path/to/volume [--merge-first] [--db-name VolumeData.db]
"""

import argparse
import collections
import dataclasses
import enum
import logging
import os
import sys
from collections.abc import Iterator
from typing import Optional

from .volume_metadata import MetadataNode
from .xml_backend import XMLMetadataBackend, save_single_xml
from .sqlite_backend import SQLiteMetadataBackend, DEFAULT_DB_FILENAME

logger = logging.getLogger(__name__)


class MigrationError(Exception):
    """Raised when migration encounters an unrecoverable error."""


class MigrationResult:
    """Result of a migration operation."""
    __slots__ = ('success', 'volume_path', 'db_path', 'merged_xml_path',
                 'node_count', 'attrib_count', 'message')

    def __init__(self):
        self.success = False
        self.volume_path = ''
        self.db_path = ''
        self.merged_xml_path = None
        self.node_count = 0
        self.attrib_count = 0
        self.message = ''

    def __repr__(self):
        status = 'OK' if self.success else 'FAILED'
        return (f"MigrationResult({status}, nodes={self.node_count}, "
                f"attribs={self.attrib_count}, msg='{self.message}')")


def count_tree(node: MetadataNode) -> tuple:
    """Count nodes and attributes in the metadata tree.
    Returns (node_count, attrib_count)."""
    nodes = 0
    attribs = 0
    for _, n in node.walk():
        nodes += 1
        attribs += len(n.attribs)
    return nodes, attribs


def merge_xml_to_single_file(volume_path: str,
                              output_filename: str = 'VolumeData_merged.xml') -> Optional[str]:
    """Load sharded XML (with _Link nodes) and save as a single merged file.
    Returns the path to the merged file, or None on failure."""
    xml_backend = XMLMetadataBackend(volume_path, single_file=False)
    if not xml_backend.exists():
        logger.error("No XML metadata found at %s", volume_path)
        return None

    root = xml_backend.load()
    if root is None:
        logger.error("Failed to load XML metadata from %s", volume_path)
        return None

    merged_path = save_single_xml(root, volume_path, output_filename)
    logger.info("Merged XML saved to %s", merged_path)
    return merged_path


def migrate_volume(volume_path: str,
                   merge_first: bool = True,
                   db_filename: str = DEFAULT_DB_FILENAME,
                   merged_xml_filename: str = 'VolumeData_merged.xml',
                   force: bool = False) -> MigrationResult:
    """
    Migrate volume metadata from XML to SQLite.

    :param volume_path: Path to the volume root directory.
    :param merge_first: If True, merge sharded XML into a single file before migrating.
    :param db_filename: Name of the SQLite database file to create.
    :param merged_xml_filename: Name for the merged XML file (if merge_first=True).
    :param force: If True, overwrite existing SQLite database.
    :returns: MigrationResult with details of the operation.
    """
    result = MigrationResult()
    result.volume_path = volume_path

    sqlite_backend = SQLiteMetadataBackend(volume_path, db_filename)
    result.db_path = sqlite_backend.db_path

    if sqlite_backend.exists() and not force:
        result.message = (f"SQLite database already exists at {sqlite_backend.db_path}. "
                          "Use force=True to overwrite.")
        logger.warning(result.message)
        return result

    xml_backend = XMLMetadataBackend(volume_path, single_file=False)
    if not xml_backend.exists():
        result.message = f"No XML metadata found at {volume_path}"
        logger.error(result.message)
        return result

    if merge_first:
        merged_path = merge_xml_to_single_file(volume_path, merged_xml_filename)
        result.merged_xml_path = merged_path

    logger.info("Loading XML metadata from %s...", volume_path)
    root = xml_backend.load()
    if root is None:
        result.message = "Failed to load XML metadata"
        logger.error(result.message)
        return result

    node_count, attrib_count = count_tree(root)
    result.node_count = node_count
    result.attrib_count = attrib_count
    logger.info("Loaded %d nodes with %d attributes", node_count, attrib_count)

    if sqlite_backend.exists() and force:
        logger.info("Removing existing database at %s", sqlite_backend.db_path)
        os.remove(sqlite_backend.db_path)

    logger.info("Writing SQLite database to %s...", sqlite_backend.db_path)
    sqlite_backend.save(root)

    # Verify round-trip integrity
    logger.info("Verifying round-trip integrity...")
    reloaded = sqlite_backend.load()
    if reloaded is None:
        result.message = "Failed to reload from SQLite after writing"
        logger.error(result.message)
        return result

    rt_nodes, rt_attribs = count_tree(reloaded)
    if rt_nodes != node_count:
        result.message = (f"Round-trip node count mismatch: "
                          f"XML={node_count}, SQLite={rt_nodes}")
        logger.error(result.message)
        return result

    if rt_attribs != attrib_count:
        result.message = (f"Round-trip attribute count mismatch: "
                          f"XML={attrib_count}, SQLite={rt_attribs}")
        logger.error(result.message)
        return result

    result.success = True
    result.message = (f"Successfully migrated {node_count} nodes and "
                      f"{attrib_count} attributes to {sqlite_backend.db_path}")
    logger.info(result.message)
    return result


def verify_migration(volume_path: str,
                     db_filename: str = DEFAULT_DB_FILENAME) -> bool:
    """Verify that the SQLite database matches the XML source.
    Logs each difference from compare_trees and returns True only if there are none."""
    xml_backend = XMLMetadataBackend(volume_path, single_file=False)
    sqlite_backend = SQLiteMetadataBackend(volume_path, db_filename)

    if not xml_backend.exists():
        logger.error("No XML found for verification")
        return False

    if not sqlite_backend.exists():
        logger.error("No SQLite database found for verification")
        return False

    xml_root = xml_backend.load()
    sql_root = sqlite_backend.load()

    if xml_root is None or sql_root is None:
        return False

    differences = compare_trees(xml_root, sql_root)
    for diff in differences:
        logger.error("Parity difference (%s) at %s: XML=%r SQLite=%r",
                     diff.kind.value, diff.path, diff.expected, diff.actual)
    return not differences


_LINK_SUFFIX = '_Link'


class DifferenceKind(enum.Enum):
    """Kind of a difference reported by compare_trees."""
    TAG = 'tag'
    LINK = 'link'
    ATTRIBUTE = 'attribute'
    ATTRIBUTE_ORDER = 'attribute_order'
    TEXT = 'text'
    WHITESPACE_TEXT = 'whitespace_text'
    MISSING_CHILD = 'missing_child'
    EXTRA_CHILD = 'extra_child'
    CHILD_ORDER = 'child_order'


@dataclasses.dataclass(frozen=True)
class TreeDifference:
    """One difference between the expected (XML) tree and the actual tree.

    ``path`` names the node as ``/Tag[index]/...`` using indices in the expected
    tree; attribute differences append ``@Name``, and child differences
    (missing, extra, order) name the parent.  ``expected`` and ``actual`` are
    ``None`` on the side where the attribute or child is absent."""
    path: str
    kind: DifferenceKind
    expected: object
    actual: object


def compare_trees(expected: MetadataNode, actual: MetadataNode) -> list[TreeDifference]:
    """Return every difference between two metadata trees; empty when they match.

    Text is compared exactly; a difference only in whitespace (including
    ``None`` versus whitespace-only text) is reported as WHITESPACE_TEXT.
    Children are paired by (tag without ``_Link``, Name, Path) in document
    order, so an inserted or reordered sibling does not misalign the rest."""
    return list(_iter_differences(expected, actual, f"/{expected.tag}"))


def _base_tag(tag: str) -> str:
    return tag.removesuffix(_LINK_SUFFIX)


def _child_key(node: MetadataNode) -> tuple[str, str | None, str | None]:
    return _base_tag(node.tag), node.attribs.get('Name'), node.attribs.get('Path')


def _iter_differences(expected: MetadataNode, actual: MetadataNode,
                      path: str) -> Iterator[TreeDifference]:
    if expected.tag != actual.tag:
        is_link = _base_tag(expected.tag) == _base_tag(actual.tag)
        kind = DifferenceKind.LINK if is_link else DifferenceKind.TAG
        # A link stub and its resolved container hold different attributes and
        # children by design, so comparing below this point is only noise.
        yield TreeDifference(path, kind, expected.tag, actual.tag)
        return

    yield from _iter_attribute_differences(expected, actual, path)

    if expected.text != actual.text:
        whitespace_only = (expected.text or '').split() == (actual.text or '').split()
        kind = DifferenceKind.WHITESPACE_TEXT if whitespace_only else DifferenceKind.TEXT
        yield TreeDifference(path, kind, expected.text, actual.text)

    yield from _iter_child_differences(expected, actual, path)


def _iter_attribute_differences(expected: MetadataNode, actual: MetadataNode,
                                path: str) -> Iterator[TreeDifference]:
    keys = list(expected.attribs) + [k for k in actual.attribs if k not in expected.attribs]
    for key in keys:
        expected_value = expected.attribs.get(key)
        actual_value = actual.attribs.get(key)
        if expected_value != actual_value:
            yield TreeDifference(f"{path}@{key}", DifferenceKind.ATTRIBUTE,
                                 expected_value, actual_value)

    expected_order = tuple(k for k in expected.attribs if k in actual.attribs)
    actual_order = tuple(k for k in actual.attribs if k in expected.attribs)
    if expected_order != actual_order:
        yield TreeDifference(path, DifferenceKind.ATTRIBUTE_ORDER,
                             expected_order, actual_order)


def _iter_child_differences(expected: MetadataNode, actual: MetadataNode,
                            path: str) -> Iterator[TreeDifference]:
    unmatched: dict[tuple, collections.deque[int]] = {}
    for j, child in enumerate(actual.children):
        unmatched.setdefault(_child_key(child), collections.deque()).append(j)

    pairs: list[tuple[int, int]] = []
    for i, child in enumerate(expected.children):
        candidates = unmatched.get(_child_key(child))
        if candidates:
            pairs.append((i, candidates.popleft()))
        else:
            yield TreeDifference(path, DifferenceKind.MISSING_CHILD, _child_key(child), None)

    for j in sorted(j for candidates in unmatched.values() for j in candidates):
        yield TreeDifference(path, DifferenceKind.EXTRA_CHILD,
                             None, _child_key(actual.children[j]))

    actual_indices = [j for _, j in pairs]
    if actual_indices != sorted(actual_indices):
        yield TreeDifference(
            path, DifferenceKind.CHILD_ORDER,
            tuple(_child_key(expected.children[i]) for i, _ in pairs),
            tuple(_child_key(actual.children[j]) for j in sorted(actual_indices)))

    for i, j in pairs:
        child = expected.children[i]
        yield from _iter_differences(child, actual.children[j], f"{path}/{child.tag}[{i}]")


def main():
    parser = argparse.ArgumentParser(
        description='Migrate nornir volume metadata from XML to SQLite'
    )
    parser.add_argument('volume_path', help='Path to the volume root directory')
    parser.add_argument('--merge-first', action='store_true', default=True,
                        help='Merge sharded XML into a single file before migration (default: True)')
    parser.add_argument('--no-merge', action='store_true', default=False,
                        help='Skip the XML merge step')
    parser.add_argument('--db-name', default=DEFAULT_DB_FILENAME,
                        help=f'Name of the SQLite database file (default: {DEFAULT_DB_FILENAME})')
    parser.add_argument('--force', action='store_true', default=False,
                        help='Overwrite existing SQLite database')
    parser.add_argument('--verify', action='store_true', default=False,
                        help='Verify existing migration instead of performing one')
    parser.add_argument('-v', '--verbose', action='store_true', default=False,
                        help='Enable verbose logging')

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format='%(levelname)s: %(message)s')

    if not os.path.isdir(args.volume_path):
        logger.error("Volume path does not exist: %s", args.volume_path)
        sys.exit(1)

    if args.verify:
        ok = verify_migration(args.volume_path, args.db_name)
        sys.exit(0 if ok else 1)

    merge_first = args.merge_first and not args.no_merge
    result = migrate_volume(
        args.volume_path,
        merge_first=merge_first,
        db_filename=args.db_name,
        force=args.force
    )

    if result.success:
        print(f"Migration complete: {result.message}")
    else:
        print(f"Migration failed: {result.message}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
