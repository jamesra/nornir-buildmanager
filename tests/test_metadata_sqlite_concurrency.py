"""Metadata port stage 4: several processes saving one volume through the SQLite shadow at once.

Writer processes run ``VolumeManager.Load``/``Save`` with the shadow flag on, editing different
containers or the same one. XML has no cross-process lock, so the writers take turns only for
their XML steps (on a test lock file) and run every shadow write concurrently. Each SQLite load
taken meanwhile must be a whole tree; afterwards the database has one root and matches the XML.

The network-share test runs only when ``NORNIR_VOLUME_METADATA_NET_TEST_PATH`` names a writable
folder on a network share; it works in a new subfolder there and removes it afterwards.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from typing import Any

import pytest

import nornir_buildmanager.volumemanager as vm
from nornir_buildmanager.metadata import (
    feature_flags,
    sqlite_journal,
    sqlite_write_lock,
)
from nornir_buildmanager.metadata.migrate import (
    DifferenceKind,
    compare_trees,
    verify_migration,
)
from nornir_buildmanager.metadata.sqlite_backend import SQLiteMetadataBackend
from nornir_buildmanager.metadata.volume_metadata import MetadataNode

from .metadata_port_characterize_data import build_volume
from .test_metadata_sqlite_write_lock import _held_by_other_process

NET_TEST_PATH_ENV = 'NORNIR_VOLUME_METADATA_NET_TEST_PATH'

_WARNING_MARK = 'SHADOW-WARNING'

# argv: volume root, xpath of the container to edit, attribute name, saves, XML lock file.
_WRITER = (
    "import contextlib, logging, sys\n"
    "import nornir_buildmanager.volumemanager as vm\n"
    "from nornir_buildmanager.metadata import shadow_write, sqlite_write_lock\n"
    "root, xpath, name, count, xml_lock = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]\n"
    "handler = logging.StreamHandler()\n"
    "handler.setLevel(logging.WARNING)\n"
    f"handler.setFormatter(logging.Formatter('{_WARNING_MARK} %(message)s'))\n"
    "logging.getLogger(shadow_write.__name__).addHandler(handler)\n"
    "xml_turn = contextlib.ExitStack()\n"
    "shadow = shadow_write.after_container_saved\n"
    "def concurrent_shadow(container_dir):\n"
    "    xml_turn.close()\n"
    "    try:\n"
    "        shadow(container_dir)\n"
    "    finally:\n"
    "        xml_turn.enter_context(sqlite_write_lock.write_lock(xml_lock, 120))\n"
    "shadow_write.after_container_saved = concurrent_shadow\n"
    "for i in range(count):\n"
    "    with xml_turn:\n"
    "        xml_turn.enter_context(sqlite_write_lock.write_lock(xml_lock, 120))\n"
    "        volume = vm.VolumeManager.Load(root)\n"
    "        node = volume.find(xpath)\n"
    "        node.attrib[name] = str(i)\n"
    "        node.AttributesChanged = True\n"
    "        vm.VolumeManager.Save(volume)\n"
)

_SECTIONS = {'WriterA': "Block/Section[@Number='1']", 'WriterB': "Block/Section[@Number='2']"}
_ONE_BLOCK = {'WriterA': 'Block', 'WriterB': 'Block'}


def _present(node: MetadataNode | None) -> MetadataNode:
    assert node is not None
    return node


def _added_writer_attributes(known: MetadataNode, stored: MetadataNode, names: set[str]) -> None:
    """Assert *stored* is *known* plus only writer attributes, so it is a whole tree from one commit."""
    for diff in compare_trees(known, stored):
        assert diff.kind is DifferenceKind.ATTRIBUTE and diff.expected is None, diff
        assert diff.path.rpartition('@')[2] in names, diff


def _find(node: MetadataNode, xpath: str) -> MetadataNode:
    """Follow ``Tag`` or ``Tag[@Attr='value']`` steps from *node* in the SQLite tree."""
    for step in xpath.split('/'):
        tag, _, condition = step.partition('[@')
        key, _, value = condition.rstrip(']').partition('=')
        node = next(c for c in node.children
                    if c.tag == tag and (not key or c.attribs.get(key) == value.strip("'")))
    return node


def _run_writers(root: str, writers: dict[str, str], saves: int) -> int:
    """Run one writer process per ``{attribute: xpath}`` while loading SQLite; return the load count.

    Each writer sets its attribute on its container to 0 .. *saves* - 1. Asserts every load is a
    whole tree, every writer exits cleanly without a shadow warning, and the end state.
    """
    backend = SQLiteMetadataBackend(root)
    known = _present(backend.load())
    xml_lock = os.path.join(root, 'xml-turn.lock')
    env = {**os.environ, feature_flags.SHADOW_SQLITE_ENV: '1'}
    procs = [subprocess.Popen([sys.executable, '-c', _WRITER, root, xpath, name, str(saves), xml_lock],
                              stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, env=env)
             for name, xpath in writers.items()]
    loads = 0
    try:
        while any(p.poll() is None for p in procs):
            _added_writer_attributes(known, _present(backend.load()), set(writers))
            loads += 1
    finally:
        errors = [p.communicate(timeout=300)[1] for p in procs]
    for proc, stderr in zip(procs, errors):
        assert proc.returncode == 0, stderr
        assert _WARNING_MARK not in stderr, stderr

    with contextlib.closing(sqlite3.connect(backend.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE parent_id IS NULL").fetchone()[0] == 1
    assert verify_migration(root)
    stored = _present(backend.load())
    for name, xpath in writers.items():
        assert _find(stored, xpath).attribs.get(name) == str(saves - 1)
    return loads


@pytest.mark.parametrize('writers', [_SECTIONS, _ONE_BLOCK], ids=['different-containers', 'same-container'])
def test_concurrent_shadow_writers_keep_whole_trees_and_parity(monkeypatch, tmp_path, writers):
    monkeypatch.setenv(feature_flags.SHADOW_SQLITE_ENV, '1')
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root)
    assert _run_writers(root, writers, saves=12) > 0


def test_earlier_shadow_write_does_not_overwrite_a_later_save(monkeypatch, tmp_path):
    """A save that lands while an earlier save's shadow waits for the lock must win in SQLite too."""
    monkeypatch.setenv(feature_flags.SHADOW_SQLITE_ENV, '1')
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root)

    def save_block_notes(notes: str) -> None:
        volume: Any = vm.VolumeManager.Load(root)
        block = volume.find('Block')
        block.attrib['Notes'] = notes
        block.AttributesChanged = True
        vm.VolumeManager.Save(volume)

    real_lock = SQLiteMetadataBackend.write_lock
    waits: list[str] = []

    def lock_after_a_later_save(self: SQLiteMetadataBackend) -> contextlib.AbstractContextManager[None]:
        if not waits:
            waits.append('first')
            save_block_notes('later')
        return real_lock(self)

    monkeypatch.setattr(SQLiteMetadataBackend, 'write_lock', lock_after_a_later_save)
    save_block_notes('earlier')
    monkeypatch.setattr(SQLiteMetadataBackend, 'write_lock', real_lock)

    assert _present(_present(SQLiteMetadataBackend(root).load()).find_child('Block')).attribs['Notes'] == 'later'
    assert verify_migration(root)


@pytest.fixture
def share_dir() -> Iterator[str]:
    """A new folder under ``NORNIR_VOLUME_METADATA_NET_TEST_PATH``, removed afterwards."""
    parent = os.environ.get(NET_TEST_PATH_ENV)
    if not parent:
        pytest.skip(f'{NET_TEST_PATH_ENV} is not set')
    path = tempfile.mkdtemp(prefix='nornir-metadata-net-test-', dir=parent)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_network_share_journal_lock_and_concurrent_shadow_writers(monkeypatch, share_dir):
    monkeypatch.setenv(feature_flags.SHADOW_SQLITE_ENV, '1')
    root = os.path.join(share_dir, 'vol')
    build_volume(root)
    backend = SQLiteMetadataBackend(root)

    choice = sqlite_journal.choose_journal_mode(backend.db_path)
    assert choice.mode == sqlite_journal.DELETE, f'{NET_TEST_PATH_ENV} is not on a network share: {choice}'
    with contextlib.closing(backend._get_connection()) as conn:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0].upper() == sqlite_journal.DELETE

    with (_held_by_other_process(backend.db_path), pytest.raises(sqlite_write_lock.WriteLockTimeout),
          sqlite_write_lock.write_lock(backend.db_path, 0.3)):
        pass

    assert _run_writers(root, _SECTIONS, saves=4) > 0
    assert not os.path.exists(backend.db_path + '-wal')
