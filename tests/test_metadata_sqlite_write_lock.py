"""Metadata port stage 4: cross-process write lock around multi-statement SQLite writes.

A whole-tree save is one transaction under the lock, so a failure leaves the previous tree
and concurrent writers never interleave; the shadow write waits for the same lock and
reports a timeout as a warning instead of failing the XML save.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

import nornir_buildmanager.volumemanager as vm
from nornir_buildmanager.metadata import (
    feature_flags,
    shadow_write,
    sqlite_backend,
    sqlite_write_lock,
)
from nornir_buildmanager.metadata.migrate import compare_trees
from nornir_buildmanager.metadata.sqlite_backend import SQLiteMetadataBackend
from nornir_buildmanager.metadata.volume_metadata import MetadataNode
from nornir_buildmanager.metadata.xml_backend import XMLMetadataBackend

from .metadata_port_characterize_data import build_volume

# Loads the lock module by file path: importing the nornir_buildmanager package costs seconds.
_HOLDER = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('sqlite_write_lock', sys.argv[2])\n"
    "lock = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(lock)\n"
    "with lock.write_lock(sys.argv[1], 30):\n"
    "    print('held', flush=True)\n"
    "    sys.stdin.readline()\n"
)

_WRITER = (
    "import sys\n"
    "from nornir_buildmanager.metadata.sqlite_backend import SQLiteMetadataBackend\n"
    "from nornir_buildmanager.metadata.volume_metadata import MetadataNode\n"
    "volume, name, count = sys.argv[1], sys.argv[2], int(sys.argv[3])\n"
    "tree = MetadataNode('Volume', {'Name': name},\n"
    "                    children=[MetadataNode('Block', {'Name': f'{name}{i}'}) for i in range(40)])\n"
    "for _ in range(count):\n"
    "    SQLiteMetadataBackend(volume).save(tree)\n"
)

def _tree(name: str, children: int = 40) -> MetadataNode:
    return MetadataNode('Volume', {'Name': name},
                        children=[MetadataNode('Block', {'Name': f'{name}{i}'}) for i in range(children)])


@contextlib.contextmanager
def _held_by_other_process(db_path: str) -> Iterator[None]:
    """Hold the write lock for *db_path* in a child process for the body of the block."""
    holder = subprocess.Popen([sys.executable, '-c', _HOLDER, db_path, sqlite_write_lock.__file__],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert holder.stdin is not None and holder.stdout is not None
    try:
        assert holder.stdout.readline().strip() == 'held'
        yield
    finally:
        holder.stdin.close()
        assert holder.wait(timeout=30) == 0


def test_lock_times_out_while_another_process_holds_it(tmp_path):
    db_path = str(tmp_path / 'VolumeData.db')
    with _held_by_other_process(db_path):
        started = time.monotonic()
        with pytest.raises(sqlite_write_lock.WriteLockTimeout), sqlite_write_lock.write_lock(db_path, 0.3):
            pass
        assert 0.3 <= time.monotonic() - started < 3.0
    with sqlite_write_lock.write_lock(db_path, 0):
        pass
    assert sorted(os.listdir(tmp_path)) == ['VolumeData.db.lock']


def test_waiting_writer_proceeds_when_the_other_process_releases(tmp_path):
    db_path = str(tmp_path / 'VolumeData.db')
    acquired = threading.Event()

    def wait_for_lock() -> None:
        with sqlite_write_lock.write_lock(db_path, 30):
            acquired.set()

    waiter = threading.Thread(target=wait_for_lock)
    with _held_by_other_process(db_path):
        waiter.start()
        assert not acquired.wait(0.5)
    waiter.join(timeout=30)
    assert acquired.is_set()


def test_threads_of_one_process_exclude_each_other(tmp_path):
    db_path = str(tmp_path / 'VolumeData.db')
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with sqlite_write_lock.write_lock(db_path, 5):
            held.set()
            release.wait(30)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(30)
        with pytest.raises(sqlite_write_lock.WriteLockTimeout), sqlite_write_lock.write_lock(db_path, 0.1):
            pass
    finally:
        release.set()
        holder.join(timeout=30)
    with sqlite_write_lock.write_lock(db_path, 0):
        pass


def test_lock_is_released_when_the_body_raises(tmp_path):
    db_path = str(tmp_path / 'VolumeData.db')
    with pytest.raises(RuntimeError), sqlite_write_lock.write_lock(db_path, 1):
        raise RuntimeError('body failed')
    with sqlite_write_lock.write_lock(db_path, 0):
        pass


def test_save_waits_for_the_lock_and_leaves_the_database_alone(monkeypatch, tmp_path):
    volume = str(tmp_path)
    SQLiteMetadataBackend(volume).save(_tree('old', 3))
    monkeypatch.setattr(sqlite_backend, 'BUSY_TIMEOUT_SECONDS', 0.3)
    backend = SQLiteMetadataBackend(volume)
    with _held_by_other_process(backend.db_path), pytest.raises(sqlite_write_lock.WriteLockTimeout):
        backend.save(_tree('new', 3))
    stored = backend.load()
    assert stored is not None and compare_trees(_tree('old', 3), stored) == []


def test_concurrent_writer_processes_never_expose_a_partial_tree(tmp_path):
    volume = str(tmp_path)
    trees = {name: _tree(name) for name in ('a', 'b')}
    SQLiteMetadataBackend(volume).save(trees['a'])
    writers = [subprocess.Popen([sys.executable, '-c', _WRITER, volume, name, '15']) for name in trees]
    reader = SQLiteMetadataBackend(volume)
    loads = 0
    try:
        while any(w.poll() is None for w in writers):
            stored = reader.load()
            assert stored is not None
            assert compare_trees(trees[stored.attribs['Name']], stored) == []
            loads += 1
    finally:
        for writer in writers:
            assert writer.wait(timeout=120) == 0
    assert loads > 0
    with contextlib.closing(sqlite3.connect(reader.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE parent_id IS NULL").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 41


def test_shadow_write_waits_for_the_lock_then_warns_and_xml_save_succeeds(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv(feature_flags.SHADOW_SQLITE_ENV, '1')
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root)
    monkeypatch.setattr(sqlite_backend, 'BUSY_TIMEOUT_SECONDS', 0.3)
    block_dir = os.path.join(root, 'TEM')

    def save_block_notes(notes: str) -> None:
        volume: Any = vm.VolumeManager.Load(root)
        block = volume.find('Block')
        block.attrib['Notes'] = notes
        block.AttributesChanged = True
        vm.VolumeManager.Save(volume)

    def stored_block_notes() -> str | None:
        stored = SQLiteMetadataBackend(root).load()
        assert stored is not None
        block = stored.find_child('Block')
        assert block is not None
        return block.attribs.get('Notes')

    with (_held_by_other_process(os.path.join(root, sqlite_backend.DEFAULT_DB_FILENAME)),
          caplog.at_level(logging.WARNING, logger=shadow_write.__name__)):
        save_block_notes('while locked')
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(block_dir in r.getMessage() and r.exc_info is not None
               and isinstance(r.exc_info[1], sqlite_write_lock.WriteLockTimeout) for r in warnings)
    saved_block = XMLMetadataBackend(block_dir, single_file=True).load()
    assert saved_block is not None and saved_block.attribs['Notes'] == 'while locked'
    assert stored_block_notes() != 'while locked'

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=shadow_write.__name__):
        save_block_notes('after release')
    # Only the shadow's records: with NORNIR_VOLUME_METADATA_READ_SQLITE on, the load before this save
    # rightly warns that the Block rows lag the XML the locked save wrote.
    assert [r for r in caplog.records if r.levelno == logging.WARNING and r.name == shadow_write.__name__] == []
    assert stored_block_notes() == 'after release'
