"""The SQLite shadow notices a VolumeData.xml that changed outside the pipeline when it is loaded.

Saves record each container file's ``(size, mtime_ns)`` beside its rows. A load compares the
file it just read with that signature and re-syncs the container from the bytes read when they
differ: on RC3 (2026-11-02) three hand-edited sections stayed out of step with the database
until the pipeline happened to save them again.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from typing import Any, cast

import pytest

import nornir_buildmanager.volumemanager as vm
from nornir_buildmanager.metadata import feature_flags, shadow_write
from nornir_buildmanager.metadata.migrate import compare_trees
from nornir_buildmanager.metadata.sqlite_backend import DEFAULT_DB_FILENAME, SQLiteMetadataBackend
from nornir_buildmanager.metadata.xml_backend import XMLMetadataBackend

from .metadata_port_characterize_data import build_volume

FLAG = feature_flags.SHADOW_SQLITE_ENV


@pytest.fixture(autouse=True)
def _fresh_process_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shadow on, parity checks on, and an empty signature cache, as in a newly started process."""
    monkeypatch.setenv(FLAG, '1')
    monkeypatch.setenv(feature_flags.SHADOW_VERIFY_ENV, '1')
    monkeypatch.setattr(shadow_write, '_known_signatures', {})


def _volume(tmp_path: Any) -> str:
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root)
    shadow_write._known_signatures.clear()  # the next load sees only what the database holds
    return root


def _load_all(root: str) -> None:
    volume = cast(vm.XContainerElementWrapper, vm.VolumeManager.Load(root))
    volume.LoadAllLinkedNodes()


def _section_dir(root: str) -> str:
    return os.path.join(root, 'TEM', '0001')


def _hand_edit(container_dir: str) -> None:
    """Add an attribute to the container's root element, as a person editing the file would."""
    path = os.path.join(container_dir, 'VolumeData.xml')
    with open(path, 'rb') as handle:
        raw = handle.read()
    tag_end = raw.index(b'>')
    if raw[tag_end - 1:tag_end] == b'/':
        tag_end -= 1
    with open(path, 'wb') as handle:
        handle.write(raw[:tag_end] + b' HandEdited="yes"' + raw[tag_end:])


def _differences(root: str) -> list:
    xml = XMLMetadataBackend(root).load()
    db = SQLiteMetadataBackend(root).load()
    assert xml is not None and db is not None
    return compare_trees(xml, db)


def _stored_signature(root: str, container_dir: str) -> tuple[int, int] | None:
    key = shadow_write.container_key(os.path.abspath(root), os.path.abspath(container_dir))
    with contextlib.closing(sqlite3.connect(os.path.join(root, DEFAULT_DB_FILENAME))) as conn:
        row = conn.execute("SELECT size, mtime_ns FROM container_signatures WHERE path = ?", (key,)).fetchone()
    return None if row is None else (row[0], row[1])


def _count_syncs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    synced: list[str] = []
    real = shadow_write._shadow_container

    def spy(container_dir, saved_xml=None, written_signature=None):
        synced.append(container_dir)
        return real(container_dir, saved_xml, written_signature)

    monkeypatch.setattr(shadow_write, '_shadow_container', spy)
    return synced


def test_saves_record_each_container_signature(tmp_path) -> None:
    root = _volume(tmp_path)
    section = _section_dir(root)
    assert _stored_signature(root, section) == shadow_write.file_signature(os.path.join(section, 'VolumeData.xml'))
    assert _stored_signature(root, root) is not None


def test_hand_edited_file_is_resynced_on_load(monkeypatch, tmp_path, caplog) -> None:
    root = _volume(tmp_path)
    section = _section_dir(root)
    _hand_edit(section)
    assert _differences(root), 'the database lags the hand edit until the section is loaded'
    synced = _count_syncs(monkeypatch)

    with caplog.at_level(logging.INFO, logger=shadow_write.__name__):
        _load_all(root)

    assert synced == [os.path.abspath(section)]
    assert _differences(root) == []
    assert _stored_signature(root, section) == shadow_write.file_signature(os.path.join(section, 'VolumeData.xml'))
    assert any('changed outside the pipeline' in r.getMessage() and section in r.getMessage()
               for r in caplog.records)


def test_unchanged_files_cause_no_database_write(monkeypatch, tmp_path) -> None:
    root = _volume(tmp_path)
    synced = _count_syncs(monkeypatch)
    connections: list[str] = []
    real_connect = SQLiteMetadataBackend._get_connection

    def counting_connect(self):
        connections.append(self.db_path)
        return real_connect(self)

    monkeypatch.setattr(SQLiteMetadataBackend, '_get_connection', counting_connect)

    _load_all(root)
    _load_all(root)

    assert synced == []
    assert len(connections) == 1, 'one read of the signature table per volume per process'


def test_shadow_off_does_nothing(monkeypatch, tmp_path) -> None:
    root = _volume(tmp_path)
    section = _section_dir(root)
    before = _stored_signature(root, section)
    _hand_edit(section)
    monkeypatch.setenv(FLAG, '0')
    synced = _count_syncs(monkeypatch)

    _load_all(root)

    assert synced == []
    assert _stored_signature(root, section) == before
    assert _differences(root), 'the hand edit is not mirrored while the shadow is off'


def test_missing_stored_signature_syncs(monkeypatch, tmp_path) -> None:
    """A database from before signatures existed (no table) is signed container by container as they load."""
    root = _volume(tmp_path)
    with contextlib.closing(sqlite3.connect(os.path.join(root, DEFAULT_DB_FILENAME))) as conn:
        conn.execute("DROP TABLE container_signatures")
        conn.commit()
    synced = _count_syncs(monkeypatch)

    _load_all(root)

    assert os.path.abspath(_section_dir(root)) in synced
    assert _stored_signature(root, _section_dir(root)) is not None
    assert _differences(root) == []
    synced.clear()
    shadow_write._known_signatures.clear()
    _load_all(root)
    assert synced == [], 'once signed, a later process loads without writing'


def test_signature_newer_than_this_process_cache_is_read_not_rewritten(monkeypatch, tmp_path) -> None:
    """Another process synced the file after this one cached the table: one lookup, no write."""
    root = _volume(tmp_path)
    _load_all(root)  # caches the table
    section = _section_dir(root)
    key = shadow_write.container_key(os.path.abspath(root), os.path.abspath(section))
    shadow_write._known_signatures[os.path.abspath(root)][key] = (-1, -1)
    synced = _count_syncs(monkeypatch)

    _load_all(root)

    assert synced == []
