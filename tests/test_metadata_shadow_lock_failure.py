"""A volume whose SQLite shadow database cannot be locked by SQLite is skipped after the first failure.

A share whose file locks fail (Lobanova on /storage2, 2026-11-11, likely CIFS without nobrl) can make
every container save wait out the 30 s busy timeout and then log "database is locked". The shadow now turns itself off for that
volume, once, for the rest of the process; the XML saves are unaffected.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, cast

import nornir_buildmanager.volumemanager as vm
import pytest
from nornir_buildmanager.metadata import feature_flags, shadow_write
from nornir_buildmanager.metadata.sqlite_backend import SQLiteMetadataBackend
from nornir_buildmanager.metadata.sqlite_write_lock import WriteLockTimeout
from nornir_buildmanager.metadata.xml_backend import XMLMetadataBackend

from .metadata_port_characterize_data import build_volume


@pytest.fixture(autouse=True)
def shadow_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(feature_flags.SHADOW_SQLITE_ENV, '1')
    monkeypatch.delenv(feature_flags.READ_SQLITE_ENV, raising=False)
    monkeypatch.setattr(shadow_write, '_lock_failed_volumes', set())


def _save_block_notes(root: str, value: str) -> None:
    volume = cast(Any, vm.VolumeManager.Load(root))
    block = volume.find('Block')
    block.attrib['Notes'] = value
    block.AttributesChanged = True
    vm.VolumeManager.Save(volume)


def _block_notes(root: str) -> str:
    node = XMLMetadataBackend(os.path.join(root, 'TEM'), single_file=True).load()
    assert node is not None
    return node.attribs['Notes']


def test_lock_failure_turns_the_shadow_off_once(monkeypatch, tmp_path, caplog):
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root)
    attempts = []

    def locked(self: SQLiteMetadataBackend) -> Any:
        attempts.append(self.db_path)
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(SQLiteMetadataBackend, '_get_connection', locked)
    with caplog.at_level(logging.WARNING, logger=shadow_write.__name__):
        _save_block_notes(root, 'first')
        _save_block_notes(root, 'second')

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, warnings
    assert 'nobrl' in warnings[0] and 'VolumeData.db' in warnings[0]
    assert len(attempts) <= 1, 'no database access after the first failure'
    assert _block_notes(root) == 'second'


@pytest.mark.parametrize('patched, error', [
    ('_get_connection', sqlite3.OperationalError('disk I/O error')),
    # Another writer holding the write lock is contention, not a broken share
    # (test_metadata_sqlite_write_lock covers the next save syncing again).
    ('write_lock', WriteLockTimeout('write lock still held by another writer')),
])
def test_other_errors_still_warn_per_save(monkeypatch, tmp_path, caplog, patched, error):
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root)

    def broken(self: SQLiteMetadataBackend) -> Any:
        raise error

    monkeypatch.setattr(SQLiteMetadataBackend, patched, broken)
    with caplog.at_level(logging.WARNING, logger=shadow_write.__name__):
        _save_block_notes(root, 'first')
        _save_block_notes(root, 'second')

    assert sum('shadow write failed' in r.getMessage() for r in caplog.records) >= 2
    assert shadow_write._lock_failed_volumes == set()
