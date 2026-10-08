"""Metadata port stage 4: SQLite saves write only the rows that changed, in one transaction.

``SQLiteMetadataBackend.save`` and the shadow write's per-container upsert share one
implementation that pairs stored children with the new ones and updates, inserts, or deletes
only what differs, so untouched nodes keep their row ids and a failure leaves every row as it was.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import tempfile
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import nornir_buildmanager.volumemanager as vm
from nornir_buildmanager.metadata import feature_flags, sqlite_backend, sqlite_journal
from nornir_buildmanager.metadata.migrate import compare_trees
from nornir_buildmanager.metadata.sqlite_backend import (
    DEFAULT_DB_FILENAME,
    SQLiteMetadataBackend,
)
from nornir_buildmanager.metadata.volume_metadata import MetadataNode

from .metadata_port_characterize_data import build_volume

Rows = dict[int, tuple]

_names = st.text(alphabet='abcXYZ09', min_size=1, max_size=4)
_tags = st.sampled_from(['Block', 'Section', 'Channel', 'Filter'])


def _node(tag: str, name: str, notes: str, children: list[MetadataNode] | None = None) -> MetadataNode:
    return MetadataNode(tag, {'Name': name, 'Notes': notes}, children=children)


def _tree_strategy(distinct_siblings: bool) -> st.SearchStrategy[MetadataNode]:
    unique_by = (lambda n: n.attribs['Name']) if distinct_siblings else None
    nodes = st.recursive(
        st.builds(_node, _tags, _names, _names),
        lambda kids: st.builds(_node, _tags, _names, _names, st.lists(kids, max_size=3, unique_by=unique_by)),
        max_leaves=8)
    return st.builds(_node, st.just('Volume'), _names, _names,
                     st.lists(nodes, min_size=1, max_size=3, unique_by=unique_by))


_trees = _tree_strategy(distinct_siblings=False)
_distinct_trees = _tree_strategy(distinct_siblings=True)


class _Crash(Exception):
    pass


def _is_row_write(sql: str) -> bool:
    return sql.split(None, 1)[0].upper() in ('INSERT', 'UPDATE', 'DELETE') and 'schema_info' not in sql


class _CountingConnection(sqlite3.Connection):
    """Counts row writes (each executemany row is one) and raises _Crash in place of write ``owner.crash_at``."""
    owner: _CountingBackend

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        if _is_row_write(sql) and self.owner.take(1) == 0:
            raise _Crash()
        return super().execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Any, /) -> sqlite3.Cursor:
        rows = list(seq_of_parameters)
        if not _is_row_write(sql):
            return super().executemany(sql, rows)
        allowed = self.owner.take(len(rows))
        cursor = super().executemany(sql, rows[:allowed])
        if allowed < len(rows):
            raise _Crash()
        return cursor


class _CountingBackend(SQLiteMetadataBackend):
    crash_at: int | None
    writes: int

    def __init__(self, volume_path: str, crash_at: int | None = None) -> None:
        super().__init__(volume_path)
        self.crash_at = crash_at
        self.writes = 0

    def take(self, count: int) -> int:
        """Record up to *count* writes and return how many may run before the crash point."""
        allowed = count if self.crash_at is None else max(0, min(count, self.crash_at - self.writes))
        self.writes += allowed
        return allowed

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=sqlite_backend.BUSY_TIMEOUT_SECONDS, factory=_CountingConnection)
        conn.owner = self
        sqlite_journal.apply_journal_mode(conn, self.db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


def _test_dir() -> str:
    root = os.path.join(os.environ.get('TESTOUTPUTPATH', tempfile.gettempdir()), 'test_metadata_sqlite_dirty_save')
    os.makedirs(root, exist_ok=True)
    return root


def _rows(db_path: str) -> Rows:
    """Every node row as ``id -> (parent_id, tag, text, sort_order, ((attrib id, key, value), ...))``."""
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        nodes = {row[0]: (*row[1:], []) for row in conn.execute(
            "SELECT id, parent_id, tag, text, sort_order FROM nodes")}
        for attrib_id, node_id, key, value in conn.execute(
                "SELECT id, node_id, key, value FROM node_attribs ORDER BY id"):
            nodes[node_id][4].append((attrib_id, key, value))
    return {i: (*row[:4], tuple(row[4])) for i, row in nodes.items()}


def _changes(before: Rows, after: Rows) -> tuple[set[int], set[int], set[int]]:
    """Return the ids of rows that were changed, removed, and added."""
    return ({i for i in before.keys() & after.keys() if before[i] != after[i]},
            before.keys() - after.keys(), after.keys() - before.keys())


def _db_id(node: MetadataNode) -> int:
    assert node._db_id is not None
    return node._db_id


def _loaded(backend: SQLiteMetadataBackend) -> MetadataNode:
    tree = backend.load()
    assert tree is not None
    return tree


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(tree=_trees)
def test_saving_an_unchanged_tree_writes_no_rows(tree: MetadataNode):
    with tempfile.TemporaryDirectory(dir=_test_dir()) as volume:
        SQLiteMetadataBackend(volume).save(tree)
        before = _rows(os.path.join(volume, DEFAULT_DB_FILENAME))
        counter = _CountingBackend(volume)
        counter.save(tree)
        assert counter.writes == 0
        assert _rows(counter.db_path) == before


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(tree=_distinct_trees, kind=st.sampled_from(['set_value', 'add_key', 'text', 'remove_child', 'insert_child']),
       data=st.data())
def test_one_edit_writes_only_the_rows_it_changes(tree: MetadataNode, kind: str, data: st.DataObject):
    """Siblings have distinct Names, as in a real volume, so every unchanged node pairs with its own row."""
    with tempfile.TemporaryDirectory(dir=_test_dir()) as volume:
        backend = SQLiteMetadataBackend(volume)
        backend.save(tree)
        before = _rows(backend.db_path)
        edited = _loaded(backend)
        nodes = [n for _, n in edited.walk()]
        removed: set[int] = set()
        added = 0
        if kind in ('set_value', 'add_key', 'text'):
            node = data.draw(st.sampled_from(nodes), label='node')
            changed = {_db_id(node)}
            if kind == 'set_value':
                node.attribs['Notes'] += '~'
            elif kind == 'add_key':
                node.attribs['Extra'] = ''
            else:
                node.text = 'edited'
        elif kind == 'remove_child':
            parent = data.draw(st.sampled_from([n for n in nodes if n.children]), label='parent')
            index = data.draw(st.integers(0, len(parent.children) - 1), label='index')
            removed = {_db_id(n) for _, n in parent.children.pop(index).walk()}
            changed = {_db_id(n) for n in parent.children[index:]}
        else:
            parent = data.draw(st.sampled_from(nodes), label='parent')
            index = data.draw(st.integers(0, len(parent.children)), label='index')
            changed = {_db_id(n) for n in parent.children[index:]}
            # Same tag as the sibling it displaces, so pairing has to go by Name, not position.
            tag = parent.children[index].tag if index < len(parent.children) else 'Section'
            parent.children.insert(index, _node(tag, '~new', '', [_node('Channel', 'TEM', '')]))
            added = 2

        backend.save(edited)
        after = _rows(backend.db_path)
        assert compare_trees(edited, _loaded(backend)) == []
        actual_changed, actual_removed, actual_added = _changes(before, after)
        assert (actual_changed, actual_removed, len(actual_added)) == (changed, removed, added)
        if kind == 'set_value':
            (node_id,) = changed
            assert [a[0] for a in after[node_id][4]] == [a[0] for a in before[node_id][4]]


def test_editing_one_block_leaves_its_siblings_rows_untouched(tmp_path):
    """Containers pair by Path: a renamed Block keeps its row, and Sections shifted by an insert keep theirs."""
    def block(name: str) -> MetadataNode:
        return MetadataNode('Block', {'Name': name, 'Path': name}, children=[
            MetadataNode('Section', {'Name': f'{i:04d}', 'Path': f'{i:04d}', 'Notes': ''}) for i in range(1, 4)])

    backend = SQLiteMetadataBackend(str(tmp_path))
    backend.save(MetadataNode('Volume', {'Name': 'V', 'Path': '.'}, children=[block(n) for n in 'ABC']))
    before = _rows(backend.db_path)
    tree = _loaded(backend)
    renamed, edited_section, shifted = tree.children[1], tree.children[1].children[1], tree.children[2].children
    renamed.attribs['Name'] = 'B2'
    edited_section.attribs['Notes'] = 'changed'
    expected = {_db_id(renamed), _db_id(edited_section)} | {_db_id(s) for s in shifted}
    shifted.insert(0, MetadataNode('Section', {'Name': '0001', 'Path': '0000', 'Notes': ''}))
    backend.save(tree)
    after = _rows(backend.db_path)
    changed, removed, added = _changes(before, after)
    assert (changed, removed, len(added)) == (expected, set(), 1)
    for node in (renamed, edited_section):
        assert [a[0] for a in after[_db_id(node)][4]] == [a[0] for a in before[_db_id(node)][4]]
    assert compare_trees(tree, _loaded(backend)) == []


def test_pathless_siblings_pair_by_name(tmp_path):
    backend = SQLiteMetadataBackend(str(tmp_path))
    backend.save(MetadataNode('Volume', {'Name': 'V'}, children=[MetadataNode('Block', {'Name': n}) for n in 'AB']))
    before = _rows(backend.db_path)
    tree = _loaded(backend)
    tree.children.insert(0, MetadataNode('Block', {'Name': 'Z'}))
    backend.save(tree)
    after = _rows(backend.db_path)
    assert [after[_db_id(b)][4] for b in tree.children[1:]] == [before[_db_id(b)][4] for b in tree.children[1:]]
    assert compare_trees(tree, _loaded(backend)) == []


def test_save_drops_stray_root_rows(tmp_path):
    backend = SQLiteMetadataBackend(str(tmp_path))
    tree = MetadataNode('Volume', {'Name': 'V'}, children=[MetadataNode('Block', {'Name': 'A'})])
    backend.save(tree)
    with contextlib.closing(sqlite3.connect(backend.db_path)) as conn, conn:
        conn.execute("INSERT INTO nodes (parent_id, tag, sort_order) VALUES (NULL, 'Volume', 0)")
    backend.save(tree)
    assert [row[0] for row in _rows(backend.db_path).values()].count(None) == 1
    assert compare_trees(tree, _loaded(backend)) == []


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(old=_trees, new=_trees, data=st.data())
def test_failed_save_keeps_every_previous_row(old: MetadataNode, new: MetadataNode, data: st.DataObject):
    """A crash before any one write statement of the save rolls back to the previous rows, ids included."""
    with tempfile.TemporaryDirectory(dir=_test_dir()) as tmp:
        counted, crashed = (os.path.join(tmp, name) for name in ('counted', 'crashed'))
        for volume in (counted, crashed):
            SQLiteMetadataBackend(volume).save(old)
        counter = _CountingBackend(counted)
        counter.save(new)
        before = _rows(os.path.join(crashed, DEFAULT_DB_FILENAME))
        if counter.writes:
            crash_at = data.draw(st.integers(0, counter.writes - 1), label='crash_at')
            with pytest.raises(_Crash):
                _CountingBackend(crashed, crash_at).save(new)
            assert _rows(os.path.join(crashed, DEFAULT_DB_FILENAME)) == before
        SQLiteMetadataBackend(crashed).save(new)
        assert _rows(os.path.join(crashed, DEFAULT_DB_FILENAME)) == _rows(counter.db_path)
        assert compare_trees(new, _loaded(counter)) == []


@pytest.mark.parametrize('flag', ['AttributesChanged', 'ChildrenChanged'])
def test_shadow_write_of_unchanged_containers_keeps_every_row(monkeypatch, tmp_path, flag):
    """Embedded (non-link) children are updated in place instead of deleted and reinserted."""
    monkeypatch.setenv(feature_flags.SHADOW_SQLITE_ENV, '1')
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root, extras=True)
    before = _rows(os.path.join(root, DEFAULT_DB_FILENAME))
    volume: Any = vm.VolumeManager.Load(root)
    volume.LoadAllLinkedNodes()
    for element in volume.iter():
        if isinstance(element, vm.XContainerElementWrapper):
            setattr(element, flag, True)
    vm.VolumeManager.Save(volume)
    assert _rows(os.path.join(root, DEFAULT_DB_FILENAME)) == before
