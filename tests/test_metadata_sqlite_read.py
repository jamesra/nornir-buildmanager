"""Metadata port stage 5: opt-in container reads from SQLite.

With ``NORNIR_VOLUME_METADATA_READ_SQLITE`` off the loaded tree is the parsed
VolumeData.xml, untouched. With it on (and the shadow write keeping VolumeData.db
current) every container is built from its SQLite rows, so the tree, the pipeline
XPath results, and the saved bytes match the XML path. Whenever the database cannot
stand in for the file, the parsed file is used with a warning.

The ``sources`` fixture records which source each container load used. A tree built
from rows also has no element tails (the indentation whitespace the parser keeps).
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from typing import Any, cast
from xml.etree import ElementTree

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import nornir_buildmanager.volumemanager as vm
from nornir_buildmanager.metadata import feature_flags, sqlite_read
from nornir_buildmanager.metadata.migrate import migrate_volume, verify_migration
from nornir_buildmanager.metadata.sqlite_backend import (
    DEFAULT_DB_FILENAME,
    SQLiteMetadataBackend,
)
from nornir_buildmanager.volumemanager.container_storage import XmlContainerStorage

from .metadata_port_characterize_data import (
    XPATH_CASES,
    ancestry_key,
    build_volume,
    canonical,
    merged_oracle,
    normalized_sha,
    snapshot,
)

READ = feature_flags.READ_SQLITE_ENV
SHADOW = feature_flags.SHADOW_SQLITE_ENV


def _load_all(root: str) -> vm.XContainerElementWrapper:
    volume = cast(vm.XContainerElementWrapper, vm.VolumeManager.Load(root))
    assert volume is not None
    volume.LoadAllLinkedNodes()
    return volume


def _no_tails(element: Any) -> bool:
    return all(e.tail is None for e in element.iter())


@pytest.fixture
def sources(monkeypatch) -> dict[str, bool]:
    """Container directory -> whether its latest load built the tree from SQLite."""
    used: dict[str, bool] = {}
    real = sqlite_read.load_container_element

    def recording(container_dir: str, xml_root: ElementTree.Element) -> ElementTree.Element:
        element = real(container_dir, xml_root)
        used[os.path.abspath(container_dir)] = element is not xml_root
        return element

    monkeypatch.setattr(sqlite_read, 'load_container_element', recording)
    return used


def _db(root: str) -> contextlib.closing[sqlite3.Connection]:
    return contextlib.closing(sqlite3.connect(os.path.join(root, DEFAULT_DB_FILENAME)))


class _Records(logging.Handler):
    records: list[logging.LogRecord]

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def warnings(self) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno >= logging.WARNING]


@contextlib.contextmanager
def _read_log() -> Iterator[_Records]:
    handler = _Records()
    read_logger = logging.getLogger(sqlite_read.__name__)
    level = read_logger.level
    read_logger.addHandler(handler)
    read_logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        read_logger.removeHandler(handler)
        read_logger.setLevel(level)


def _shadowed_volume(root: str, extras: bool = True) -> str:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(SHADOW, '1')
        mp.delenv(READ, raising=False)
        build_volume(root, extras=extras)
    return root


@pytest.fixture(scope='module')
def query_root(tmp_path_factory) -> str:
    return _shadowed_volume(os.path.join(str(tmp_path_factory.mktemp('read')), 'vol'))


@pytest.mark.parametrize('value, expected', [
    (None, False), ('', False), ('0', False), ('off', False), ('1', True), (' TRUE ', True), ('yes', True),
])
def test_read_flag_parsing(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(READ, raising=False)
    else:
        monkeypatch.setenv(READ, value)
    assert feature_flags.read_sqlite_enabled() is expected


def test_flag_off_never_opens_the_database(monkeypatch, query_root, sources):
    def fail(*_: Any) -> None:
        raise AssertionError('SQLite consulted with the read flag off')

    monkeypatch.setenv(READ, '0')
    monkeypatch.setattr(sqlite_read, '_load_from_sqlite', fail)
    with _read_log() as log:
        _load_all(query_root)
    assert sources and not any(sources.values())
    assert log.records == []


def test_other_file_names_are_never_read_from_sqlite(monkeypatch, query_root, sources):
    monkeypatch.setenv(READ, '1')
    shutil.copy(os.path.join(query_root, 'VolumeData.xml'), os.path.join(query_root, 'Other.xml'))
    try:
        assert not _no_tails(XmlContainerStorage('Other.xml').load_container(query_root))
        assert sources == {}
    finally:
        os.remove(os.path.join(query_root, 'Other.xml'))


@pytest.mark.parametrize('extras', [False, True])
def test_reads_switched_load_the_same_tree(monkeypatch, tmp_path, sources, extras):
    root = _shadowed_volume(os.path.join(str(tmp_path), 'vol'), extras)
    monkeypatch.setenv(READ, '0')
    from_xml = canonical(_load_all(root))
    monkeypatch.setenv(READ, '1')
    with _read_log() as log:
        volume = _load_all(root)
    assert log.warnings() == []
    assert sources and all(sources.values()) and _no_tails(volume)
    assert canonical(volume) == from_xml == canonical(merged_oracle(root))


@pytest.mark.parametrize('pattern,root_xpath,xpath,expected', XPATH_CASES,
                         ids=[f'{root or "Volume"}|{xpath}' for _, root, xpath, _ in XPATH_CASES])
def test_xpath_results_match_with_reads_switched(monkeypatch, query_root, sources, pattern, root_xpath, xpath,
                                                 expected):
    """The stage 0 XPath table, with every container built from SQLite."""
    monkeypatch.setenv(READ, '1')
    oracle = merged_oracle(query_root)
    parents = {child: parent for parent in oracle.iter() for child in parent}
    volume = vm.VolumeManager.Load(query_root)
    assert volume is not None
    roots = list(volume.findall(root_xpath)) if root_xpath else [volume]
    oracle_roots = oracle.findall(root_xpath) if root_xpath else [oracle]
    assert [ancestry_key(r) for r in roots] == [ancestry_key(r, parents) for r in oracle_roots]
    total = 0
    for found_root, oracle_root in zip(roots, oracle_roots):
        expected_keys = [ancestry_key(e, parents) for e in oracle_root.findall(xpath)]
        assert [ancestry_key(e) for e in found_root.findall(xpath)] == expected_keys
        total += len(expected_keys)
    assert total == expected
    assert sources and all(sources.values())


def test_saves_after_a_sqlite_read_write_the_same_bytes(monkeypatch, tmp_path, sources):
    hashes = {}
    for enabled in ('0', '1'):
        root = _shadowed_volume(os.path.join(str(tmp_path), enabled, 'vol'))
        monkeypatch.setenv(SHADOW, '1')
        monkeypatch.setenv(READ, enabled)
        for flag in ('AttributesChanged', 'ChildrenChanged'):
            sources.clear()
            volume = _load_all(root)
            assert sources and all(used is (enabled == '1') for used in sources.values())
            for element in volume.iter():
                if isinstance(element, vm.XContainerElementWrapper):
                    setattr(element, flag, True)
            vm.VolumeManager.Save(volume)
        assert verify_migration(root)
        hashes[enabled] = {rel: normalized_sha(root, raw) for rel, (_, _, raw) in snapshot(root).items()}
    assert hashes['0'] == hashes['1']


def _set_schema_version(root: str, version: str | None) -> None:
    with _db(root) as conn:
        conn.execute("DELETE FROM schema_info WHERE key = 'schema_version'")
        if version is not None:
            conn.execute("INSERT INTO schema_info (key, value) VALUES ('schema_version', ?)", (version,))
        conn.commit()


def _stale_section(root: str) -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(SHADOW, '0')
        mp.setenv(READ, '0')
        volume = vm.VolumeManager.Load(root)
        assert volume is not None
        section = volume.find('Block/Section')
        assert section is not None
        section.attrib['Notes'] = 'saved without the shadow'
        section.AttributesChanged = True
        vm.VolumeManager.Save(volume)


def _drop_section_rows(root: str) -> None:
    with _db(root) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM nodes WHERE tag = 'Section'")
        conn.commit()


def _text_after_element(root: str) -> None:
    path = os.path.join(root, 'TEM', 'VolumeData.xml')
    with open(path, 'rb') as handle:
        raw = handle.read()
    with open(path, 'wb') as handle:
        handle.write(raw.replace(b'/>', b'/>stray', 1))


def _garbage(root: str) -> None:
    with open(os.path.join(root, DEFAULT_DB_FILENAME), 'wb') as handle:
        handle.write(b'not a database' * 100)


FALLBACKS: list[tuple[str, Callable[[str], None], str]] = [
    ('missing', lambda root: os.remove(os.path.join(root, DEFAULT_DB_FILENAME)), 'does not exist'),
    ('older schema', lambda root: _set_schema_version(root, None), 'schema version 0 was older than 1; migrated'),
    ('newer schema', lambda root: _set_schema_version(root, '99'), 'schema version 99 is newer'),
    ('stale', _stale_section, "parity difference(s) with the database, first attribute at /Section@Notes"),
    ('no rows', _drop_section_rows, 'it has no rows in the database'),
    ('text after element', _text_after_element, 'text after an element'),
    ('not a database', _garbage, 'SQLite read failed'),
]


@pytest.mark.parametrize('name, damage, reason', FALLBACKS, ids=[f[0] for f in FALLBACKS])
def test_unusable_database_falls_back_to_xml_with_one_warning(monkeypatch, tmp_path, sources, name, damage,
                                                              reason):
    root = _shadowed_volume(os.path.join(str(tmp_path), 'vol'))
    damage(root)
    monkeypatch.setenv(READ, '0')
    from_xml = canonical(_load_all(root))
    monkeypatch.setenv(READ, '1')
    with _read_log() as log:
        first = _load_all(root)
        first_sources = dict(sources)
        second = _load_all(root)
    assert canonical(first) == canonical(second) == from_xml
    assert not all(first_sources.values())
    warnings = log.warnings()
    assert any(reason in w for w in warnings), warnings
    assert len(warnings) == len(set(warnings)), 'a fallback warned twice for the same scope'
    if name in ('missing', 'older schema', 'newer schema'):
        assert len(warnings) == 1, 'a volume-wide fallback warned once per container'
    assert name != 'missing' or not os.path.exists(os.path.join(root, DEFAULT_DB_FILENAME))


def test_older_schema_is_migrated_then_used(monkeypatch, tmp_path, sources):
    root = _shadowed_volume(os.path.join(str(tmp_path), 'vol'))
    _set_schema_version(root, None)
    monkeypatch.setenv(READ, '1')
    _load_all(root)
    assert sources[os.path.abspath(root)] is False
    assert SQLiteMetadataBackend(root).get_schema_version() == 1
    _load_all(root)
    assert all(sources.values())


def test_newer_schema_is_left_alone(monkeypatch, tmp_path):
    root = _shadowed_volume(os.path.join(str(tmp_path), 'vol'))
    _set_schema_version(root, '99')
    monkeypatch.setenv(READ, '1')
    _load_all(root)
    assert SQLiteMetadataBackend(root).get_schema_version() == 99


def test_stale_container_falls_back_alone(monkeypatch, tmp_path, sources):
    """Only the containers whose rows differ are read from XML; the rest still come from SQLite."""
    root = _shadowed_volume(os.path.join(str(tmp_path), 'vol'))
    _stale_section(root)
    monkeypatch.setenv(READ, '1')
    with _read_log() as log:
        _load_all(root)
    stale = os.path.join(os.path.abspath(root), 'TEM', '0001')
    assert [d for d, used in sources.items() if not used] == [stale]
    assert len(log.warnings()) == 1 and stale in log.warnings()[0]


# Each statement binds ?1 to the drawn value and ?2 to the target row id.
_DAMAGE = st.one_of(
    st.tuples(st.just("UPDATE node_attribs SET value = ?1 WHERE id = ?2"), st.text(max_size=6)),
    st.tuples(st.just("DELETE FROM node_attribs WHERE id = ?2"), st.none()),
    st.tuples(st.just("UPDATE nodes SET text = ?1 WHERE id = ?2"), st.one_of(st.none(), st.text(max_size=6))),
    st.tuples(st.just("UPDATE nodes SET tag = ?1 WHERE id = ?2"), st.sampled_from(['Section', 'Filter', 'X'])),
    st.tuples(st.just("UPDATE nodes SET sort_order = ?1 WHERE id = ?2"), st.integers(-2, 40)),
    st.tuples(st.just("DELETE FROM nodes WHERE id = ?2"), st.none()),
)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow,
                                                                 HealthCheck.function_scoped_fixture])
@given(damage=_DAMAGE, row=st.integers(0, 10_000))
def test_any_one_row_change_never_changes_the_loaded_tree(monkeypatch, query_root, sources, damage, row):
    """Whatever single row of VolumeData.db differs, the loaded tree equals the XML; SQLite is used only when no load warned."""
    sql, value = damage
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, 'vol')
        shutil.copytree(query_root, root)
        monkeypatch.setenv(READ, '0')
        from_xml = canonical(_load_all(root))
        table = 'node_attribs' if 'node_attribs' in sql else 'nodes'
        with _db(root) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            ids = [r[0] for r in conn.execute(f"SELECT id FROM {table} ORDER BY id")]
            target = ids[row % len(ids)]
            conn.execute(sql, (value, target))
            conn.commit()
        monkeypatch.setenv(READ, '1')
        sources.clear()
        with _read_log() as log:
            volume = _load_all(root)
        assert canonical(volume) == from_xml
        assert sources and all(sources.values()) is (log.warnings() == [])


_LEGACY_VOLUME_XML = """<?xml version='1.0' encoding='utf-8'?>
<Volume Name="LegacyVolume" Path="." CreationDate="2020-06-01" Version="1.0">
  <Block Name="TEM" Path="TEM" CreationDate="2020-06-01" Version="1.0">
    <Section Name="0001" Path="0001" SectionNumber="1" CreationDate="2020-06-01" Version="1.0">
      <Channel Name="TEM" Path="TEM" CreationDate="2020-06-01" Version="1.0">
        <Filter FilterName="Raw" Path="Raw" CreationDate="2020-06-01" Version="1.0"/>
      </Channel>
    </Section>
  </Block>
</Volume>"""


def test_legacy_section_and_filter_aliases_read_from_sqlite(monkeypatch, tmp_path):
    """On-disk SectionNumber/FilterName must match normalized SQLite rows for parity (migration-style)."""
    root = os.path.join(str(tmp_path), 'LegacyVolume')
    os.makedirs(root, exist_ok=True)
    xml_path = os.path.join(root, 'VolumeData.xml')
    with open(xml_path, 'w', encoding='utf-8') as handle:
        handle.write(_LEGACY_VOLUME_XML)
    result = migrate_volume(root, merge_first=False, force=True)
    assert result.success
    monkeypatch.setenv(READ, '1')
    parsed = ElementTree.parse(xml_path).getroot()
    assert 'SectionNumber' in parsed.find('.//Section').attrib
    loaded = sqlite_read.load_container_element(root, parsed)
    assert loaded is not parsed
    section = loaded.find('.//Section')
    filt = loaded.find('.//Filter')
    assert section.attrib.get('Number') == '1'
    assert 'SectionNumber' not in section.attrib
    assert filt.attrib.get('Name') == 'Raw'
    assert 'FilterName' not in filt.attrib
