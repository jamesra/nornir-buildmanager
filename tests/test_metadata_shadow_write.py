"""Metadata port stage 3: opt-in SQLite shadow of VolumeData.xml container saves.

With ``NORNIR_VOLUME_METADATA_SHADOW_SQLITE`` off nothing but XML is written; with it
on every container save keeps ``VolumeData.db`` in full parity with the sharded XML,
and a broken database only produces a warning naming the container.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from typing import Any, cast

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import nornir_buildmanager.volumemanager as vm
from nornir_buildmanager.metadata import feature_flags, shadow_write
from nornir_buildmanager.metadata.migrate import compare_trees, verify_migration
from nornir_buildmanager.metadata.sqlite_backend import (
    DEFAULT_DB_FILENAME,
    SQLiteMetadataBackend,
)
from nornir_buildmanager.metadata.volume_metadata import MetadataNode
from nornir_buildmanager.metadata.xml_backend import XMLMetadataBackend
from nornir_buildmanager.volumemanager.xelementwrapper import XElementWrapper

from .metadata_port_characterize_data import build_volume, normalized_sha, snapshot

FLAG = feature_flags.SHADOW_SQLITE_ENV


def _load(root: str) -> vm.XContainerElementWrapper:
    volume = vm.VolumeManager.Load(root)
    assert volume is not None
    return cast(vm.XContainerElementWrapper, volume)


def _find(element: Any, xpath: str) -> Any:
    found = element.find(xpath)
    assert found is not None, xpath
    return found


def _present(node: MetadataNode | None) -> MetadataNode:
    assert node is not None
    return node


def _force_save(root: str, flag: str) -> None:
    volume = _load(root)
    volume.LoadAllLinkedNodes()
    for element in volume.iter():
        if isinstance(element, vm.XContainerElementWrapper):
            setattr(element, flag, True)
    vm.VolumeManager.Save(volume)


def _differences(root: str) -> list:
    return compare_trees(_present(XMLMetadataBackend(root).load()), _present(SQLiteMetadataBackend(root).load()))


def _db_path(root: str) -> str:
    return os.path.join(root, DEFAULT_DB_FILENAME)


class _WarningRecords(logging.Handler):
    records: list[logging.LogRecord]

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextlib.contextmanager
def _no_shadow_warnings() -> Iterator[None]:
    """Fail if the shadow logs a warning (an error or a parity difference) inside the block."""
    handler = _WarningRecords()
    shadow_logger = logging.getLogger(shadow_write.__name__)
    shadow_logger.addHandler(handler)
    try:
        yield
    finally:
        shadow_logger.removeHandler(handler)
    assert [r.getMessage() for r in handler.records] == []


@pytest.mark.parametrize('value, expected', [
    (None, False), ('', False), ('0', False), ('false', False), ('no', False), ('off', False),
    ('1', True), ('true', True), (' TRUE ', True), ('yes', True), ('On', True),
])
def test_flag_parsing(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, value)
    assert feature_flags.shadow_sqlite_enabled() is expected


@pytest.mark.parametrize('extras', [False, True])
def test_flag_off_writes_no_database_and_flag_on_keeps_xml_bytes(monkeypatch, tmp_path, extras):
    """The shadow adds VolumeData.db only; every VolumeData.xml is byte-identical either way."""
    hashes = {}
    for enabled in ('0', '1'):
        monkeypatch.setenv(FLAG, enabled)
        root = os.path.join(str(tmp_path), enabled, 'vol')
        build_volume(root, extras=extras)
        for flag in ('AttributesChanged', 'ChildrenChanged'):
            _force_save(root, flag)
        assert os.path.exists(_db_path(root)) is (enabled == '1')
        hashes[enabled] = {rel: normalized_sha(root, raw) for rel, (_, _, raw) in snapshot(root).items()}
    assert hashes['0'] == hashes['1']


@pytest.mark.parametrize('extras', [False, True])
@pytest.mark.parametrize('flag', ['AttributesChanged', 'ChildrenChanged'])
def test_shadow_database_passes_full_parity_on_fixture_volumes(monkeypatch, tmp_path, extras, flag):
    monkeypatch.setenv(FLAG, '1')
    root = os.path.join(str(tmp_path), 'vol')
    with _no_shadow_warnings():
        build_volume(root, extras=extras)
        assert verify_migration(root)
        _force_save(root, flag)
    assert _differences(root) == []


def _container_row_ids(root: str, tag: str) -> list[int]:
    with contextlib.closing(sqlite3.connect(_db_path(root))) as conn:
        return [row[0] for row in conn.execute("SELECT id FROM nodes WHERE tag = ? ORDER BY id", (tag,))]


def test_container_behind_pathless_sibling_is_upserted(monkeypatch, tmp_path):
    """The extras Block lists NonStosSectionNumbers (no Path) before its Section links."""
    monkeypatch.setenv(FLAG, '1')
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root, extras=True)
    volume = _load(root)
    section = _find(volume, 'Block/Section')
    section.attrib['Notes'] = 'changed'
    section.AttributesChanged = True
    with _no_shadow_warnings():
        vm.VolumeManager.Save(volume)
    assert _differences(root) == []


def test_parent_save_keeps_child_container_rows(monkeypatch, tmp_path):
    """A container save rewrites only its own rows; the linked child containers keep theirs."""
    monkeypatch.setenv(FLAG, '1')
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root)
    sections, filters = _container_row_ids(root, 'Section'), _container_row_ids(root, 'Filter')
    volume = _load(root)
    block = _find(volume, 'Block')
    block.attrib['Notes'] = 'changed'
    block.AttributesChanged = True
    with _no_shadow_warnings():
        vm.VolumeManager.Save(volume)
    assert (_container_row_ids(root, 'Section'), _container_row_ids(root, 'Filter')) == (sections, filters)
    assert _differences(root) == []


def test_flag_turned_on_for_existing_volume_bootstraps_from_xml(monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, '0')
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root, extras=True)
    assert not os.path.exists(_db_path(root))
    monkeypatch.setenv(FLAG, '1')
    volume = _load(root)
    section = _find(volume, 'Block/Section')
    section.attrib['Notes'] = 'changed'
    section.AttributesChanged = True
    with _no_shadow_warnings():
        vm.VolumeManager.Save(volume)
    assert _differences(root) == []


_EDITS = st.lists(st.one_of(
    st.tuples(st.just('add_section'), st.integers(3, 6)),
    st.tuples(st.just('remove_section'), st.integers(0, 3)),
    st.tuples(st.just('filter_attribute'), st.text('abc \u00b5', max_size=5)),
    st.tuples(st.just('notes_text'), st.text('xyz \u00b5\n', max_size=8)),
), min_size=1, max_size=5)


def _apply(volume: vm.XContainerElementWrapper, edit: tuple) -> None:
    kind, value = edit
    block = _find(volume, 'Block')
    if kind == 'add_section':
        _, section = block.UpdateOrAddChildByAttrib(vm.SectionNode.Create(value), 'Number')
        section.UpdateOrAddChildByAttrib(vm.ChannelNode.Create('TEM'), 'Name')
    elif kind == 'remove_section':
        sections = list(block.findall('Section'))
        if sections:
            block.remove(sections[value % len(sections)])
    elif kind == 'filter_attribute':
        for filter_node in volume.findall('Block/Section/Channel/Filter'):
            filter_node.attrib['Note'] = value
            filter_node.AttributesChanged = True
    else:
        for notes in volume.findall('Block/Section/Channel/Notes'):
            notes.text = value
            notes.AttributesChanged = True


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(edits=_EDITS)
def test_shadow_stays_in_parity_through_edit_sequences(edits):
    """Added, removed, and edited containers keep the shadow equal to the sharded XML after every save."""
    with pytest.MonkeyPatch.context() as monkeypatch, tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv(FLAG, '1')
        root = os.path.join(tmp, 'vol')
        build_volume(root)
        volume = _load(root)
        for edit in edits:
            _apply(volume, edit)
            with _no_shadow_warnings():
                vm.VolumeManager.Save(volume)
            assert _differences(root) == [], edit


def test_unresolvable_link_stays_a_stub(monkeypatch, tmp_path):
    """A link whose folder has no VolumeData.xml is stored as the stub, as the sharded XML load reports it."""
    monkeypatch.setenv(FLAG, '1')
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root)
    volume = _load(root)
    block = _find(volume, 'Block')
    block.append(XElementWrapper('Section_Link', attrib={'Path': '0099', 'Name': '0099', 'Number': '99'}))
    block.ChildrenChanged = True
    with _no_shadow_warnings():
        vm.VolumeManager.Save(volume)
    stored_block = _present(SQLiteMetadataBackend(root).load()).find_child('Block')
    assert stored_block is not None
    assert stored_block.find_child('Section_Link', 'Path', '0099') is not None
    assert _differences(root) == []


def test_sqlite_error_warns_with_container_path_and_xml_save_succeeds(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv(FLAG, '1')
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root)
    os.remove(_db_path(root))
    os.mkdir(_db_path(root))
    volume = _load(root)
    block = _find(volume, 'Block')
    block.attrib['Notes'] = 'after'
    block.AttributesChanged = True
    with caplog.at_level(logging.WARNING, logger=shadow_write.__name__):
        vm.VolumeManager.Save(volume)
    block_dir = os.path.join(root, 'TEM')
    assert any(r.levelno == logging.WARNING and block_dir in r.getMessage() for r in caplog.records)
    assert _present(XMLMetadataBackend(block_dir, single_file=True).load()).attribs['Notes'] == 'after'


def test_parity_mismatch_warns_with_container_path(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv(FLAG, '1')
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root)
    real_upsert = shadow_write._upsert_container

    def drop_attributes(conn, node_id, saved, container_dir):
        real_upsert(conn, node_id, saved, container_dir)
        conn.execute("DELETE FROM node_attribs WHERE node_id = ? AND key = 'Notes'", (node_id,))

    monkeypatch.setattr(shadow_write, '_upsert_container', drop_attributes)
    volume = _load(root)
    block = _find(volume, 'Block')
    block.attrib['Notes'] = 'after'
    block.AttributesChanged = True
    with caplog.at_level(logging.WARNING, logger=shadow_write.__name__):
        vm.VolumeManager.Save(volume)
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any('parity difference (attribute)' in m and os.path.join(root, 'TEM') in m
               and '@Notes' in m and "XML='after'" in m for m in messages)


def test_find_volume_root_walks_up_to_the_volume(monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, '0')
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root)
    assert shadow_write.find_volume_root(os.path.join(root, 'TEM', '0001', 'TEM')) == os.path.abspath(root)
    assert shadow_write.find_volume_root(str(tmp_path)) is None
