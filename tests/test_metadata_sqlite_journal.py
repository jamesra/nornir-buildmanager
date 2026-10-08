"""Metadata port stage 4: SQLite journal mode follows the filesystem the database is on.

WAL only on a local disk; DELETE (rollback journal) on network shares, UNC paths, and
filesystems that cannot be identified. Mount tables are mocked so the tests do not depend
on where this machine keeps its files.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from nornir_buildmanager.metadata import sqlite_journal
from nornir_buildmanager.metadata.sqlite_backend import (
    BUSY_TIMEOUT_SECONDS,
    SQLiteMetadataBackend,
)
from nornir_buildmanager.metadata.volume_metadata import MetadataNode

NETWORK_TYPES = ['cifs', 'smb3', 'smbfs', 'nfs', 'nfs4', '9p', 'drvfs', 'virtiofs',
                 'fuse', 'fuseblk', 'fuse.sshfs', 'CIFS']
LOCAL_TYPES = ['ext4', 'xfs', 'btrfs', 'tmpfs', 'overlay', 'zfs', 'ntfs3']

_segment = st.text(alphabet='abcxyz', min_size=1, max_size=3)
_abs_path = st.lists(_segment, min_size=0, max_size=4).map(lambda parts: '/' + '/'.join(parts))
_mount_table = st.lists(st.tuples(_abs_path, st.sampled_from(NETWORK_TYPES + LOCAL_TYPES)), max_size=6)


def _test_dir() -> str:
    root = os.path.join(os.environ.get('TESTOUTPUTPATH', tempfile.gettempdir()), 'test_metadata_sqlite_journal')
    os.makedirs(root, exist_ok=True)
    return root


def _reference_mount(path: str, mounts: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Slow oracle: compare path components instead of string prefixes."""
    parts = [p for p in path.split('/') if p]
    best = None
    for mount_point, fstype in mounts:
        mount_parts = [p for p in mount_point.split('/') if p]
        if parts[:len(mount_parts)] == mount_parts and (best is None or len(mount_parts) >= best[0]):
            best = (len(mount_parts), (mount_point, fstype))
    return best[1] if best else None


@pytest.mark.parametrize('fstype', NETWORK_TYPES)
def test_network_filesystems_use_rollback_journal(fstype: str) -> None:
    mounts = [('/', 'overlay'), ('/data', fstype)]
    choice = sqlite_journal.choose_journal_mode('/data/vol/VolumeData.db', mounts)
    assert choice == sqlite_journal.JournalChoice('DELETE', fstype, '/data')


@pytest.mark.parametrize('fstype', LOCAL_TYPES)
def test_local_filesystems_use_wal(fstype: str) -> None:
    mounts = [('/', 'overlay'), ('/data', fstype)]
    assert sqlite_journal.choose_journal_mode('/data/vol/VolumeData.db', mounts).mode == 'WAL'


@pytest.mark.parametrize('path', [r'\\nas\share\vol\VolumeData.db', r'\\?\UNC\nas\share\VolumeData.db'])
def test_unc_paths_use_rollback_journal_without_a_mount_table(path: str) -> None:
    choice = sqlite_journal.choose_journal_mode(path, [('/', 'ext4')])
    assert (choice.mode, choice.filesystem) == ('DELETE', 'UNC')


def test_unknown_filesystem_uses_rollback_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sqlite_journal.choose_journal_mode('/vol/VolumeData.db', []).mode == 'DELETE'
    monkeypatch.setattr(sqlite_journal, 'read_mount_table', lambda: None)
    assert sqlite_journal.choose_journal_mode('/vol/VolumeData.db').mode == 'DELETE'


@pytest.mark.parametrize('remote, mode, filesystem', [(True, 'DELETE', 'remote drive'), (False, 'WAL', 'local drive')])
def test_windows_drive_type_decides(monkeypatch: pytest.MonkeyPatch, remote: bool, mode: str, filesystem: str) -> None:
    monkeypatch.setattr(sqlite_journal.sys, 'platform', 'win32')
    monkeypatch.setattr(sqlite_journal, '_windows_drive_is_remote', lambda drive: remote)
    choice = sqlite_journal.choose_journal_mode('Z:\\vol\\VolumeData.db')
    assert (choice.mode, choice.filesystem) == (mode, filesystem)
    assert sqlite_journal.choose_journal_mode('//nas/share/VolumeData.db').mode == 'DELETE'


def test_nested_local_mount_inside_a_share_and_prefix_boundaries() -> None:
    mounts = [('/', 'ext4'), ('/mnt/share', 'cifs'), ('/mnt/share/cache', 'tmpfs')]
    assert sqlite_journal.choose_journal_mode('/mnt/share/cache/VolumeData.db', mounts).mode == 'WAL'
    assert sqlite_journal.choose_journal_mode('/mnt/share/vol/VolumeData.db', mounts).mode == 'DELETE'
    assert sqlite_journal.choose_journal_mode('/mnt/shared/VolumeData.db', mounts).mode == 'WAL'


def test_symlink_into_a_share_is_resolved() -> None:
    with tempfile.TemporaryDirectory(dir=_test_dir()) as tmp:
        share = os.path.join(tmp, 'share')
        os.mkdir(share)
        link = os.path.join(tmp, 'link')
        os.symlink(share, link)
        mounts = [('/', 'ext4'), (os.path.realpath(share), 'nfs')]
        assert sqlite_journal.choose_journal_mode(os.path.join(link, 'VolumeData.db'), mounts).mode == 'DELETE'


@given(path=_abs_path, mounts=_mount_table)
@example(path='/a/b', mounts=[('/a', 'cifs'), ('/a/', 'ext4')])
@example(path='/ab', mounts=[('/a', 'cifs')])
@example(path='/mnt/v', mounts=[('/', 'ext4'), ('/mnt/X', 'cifs')])
def test_find_mount_matches_component_prefix_oracle(path: str, mounts: list[tuple[str, str]]) -> None:
    assert sqlite_journal.find_mount(path, mounts) == _reference_mount(path, mounts)


def test_read_mount_table_decodes_escaped_mount_points() -> None:
    lines = (b'overlay / overlay rw 0 0\n'
             b'//nas/share /mnt/My\\040Share cifs rw 0 0\n'
             b'D:\\134 /workspace 9p rw 0 0\n'
             b'/dev/sdb1 /media/caf\xe9 ext4 rw 0 0\n'
             b'dev /three ext4\n'
             b'short line\n')
    with tempfile.TemporaryDirectory(dir=_test_dir()) as tmp:
        table = os.path.join(tmp, 'mounts')
        with open(table, 'wb') as handle:
            handle.write(lines)
        assert sqlite_journal.read_mount_table(table) == [
            ('/', 'overlay'), ('/mnt/My Share', 'cifs'), ('/workspace', '9p'),
            ('/media/caf\udce9', 'ext4'), ('/three', 'ext4')]
        assert sqlite_journal.read_mount_table(os.path.join(tmp, 'missing')) is None


def _journal_mode(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return str(conn.execute('PRAGMA journal_mode').fetchone()[0]).upper()
    finally:
        conn.close()


@pytest.mark.parametrize('fstype, expected', [('ext4', 'WAL'), ('cifs', 'DELETE'), ('virtiofs', 'DELETE')])
def test_backend_connection_applies_choice_and_busy_timeout(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
                                                            fstype: str, expected: str) -> None:
    monkeypatch.setattr(sqlite_journal, 'read_mount_table', lambda: [('/', fstype)])
    with tempfile.TemporaryDirectory(dir=_test_dir()) as tmp, caplog.at_level(logging.INFO, sqlite_journal.__name__):
        backend = SQLiteMetadataBackend(tmp)
        backend.save(MetadataNode(tag='Volume', attribs={'Name': 'v'}))
        conn = backend._get_connection()
        try:
            assert conn.execute('PRAGMA busy_timeout').fetchone()[0] == int(BUSY_TIMEOUT_SECONDS * 1000)
        finally:
            conn.close()
        assert _journal_mode(backend.db_path) == expected
        assert not os.path.exists(backend.db_path + '-shm') or expected == 'WAL'
        logged = [r.getMessage() for r in caplog.records if backend.db_path in r.getMessage()]
        assert len(logged) == 1 and expected in logged[0] and fstype in logged[0]


def test_wal_database_moved_to_a_share_switches_to_rollback_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory(dir=_test_dir()) as tmp:
        backend = SQLiteMetadataBackend(tmp)
        monkeypatch.setattr(sqlite_journal, 'read_mount_table', lambda: [('/', 'ext4')])
        backend.save(MetadataNode(tag='Volume'))
        assert _journal_mode(backend.db_path) == 'WAL'
        monkeypatch.setattr(sqlite_journal, 'read_mount_table', lambda: [('/', 'nfs')])
        loaded = backend.load()
        assert loaded is not None and loaded.tag == 'Volume'
        assert _journal_mode(backend.db_path) == 'DELETE'


def test_schema_version_read_on_a_share_leaves_wal(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory(dir=_test_dir()) as tmp:
        backend = SQLiteMetadataBackend(tmp)
        monkeypatch.setattr(sqlite_journal, 'read_mount_table', lambda: [('/', 'ext4')])
        backend.save(MetadataNode(tag='Volume'))
        assert _journal_mode(backend.db_path) == 'WAL'
        monkeypatch.setattr(sqlite_journal, 'read_mount_table', lambda: [('/', 'cifs')])
        assert backend.get_schema_version() == 1
        assert _journal_mode(backend.db_path) == 'DELETE'
        assert not os.path.exists(backend.db_path + '-shm')


def test_mode_sqlite_cannot_use_is_a_warning(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(sqlite_journal, 'read_mount_table', lambda: [('/', 'ext4')])
    conn = sqlite3.connect(':memory:')
    try:
        with caplog.at_level(logging.WARNING, sqlite_journal.__name__):
            assert sqlite_journal.apply_journal_mode(conn, ':memory:') == 'MEMORY'
    finally:
        conn.close()
    assert any('wanted WAL' in r.getMessage() for r in caplog.records)


def test_wal_database_held_open_elsewhere_is_not_kept_on_a_share(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory(dir=_test_dir()) as tmp:
        db_path = os.path.join(tmp, 'VolumeData.db')
        holder = sqlite3.connect(db_path)
        try:
            holder.execute('PRAGMA journal_mode=WAL')
            holder.execute('CREATE TABLE t (x)')
            holder.execute('BEGIN')
            holder.execute('SELECT * FROM t').fetchall()
            monkeypatch.setattr(sqlite_journal, 'read_mount_table', lambda: [('/', 'cifs')])
            conn = sqlite3.connect(db_path, timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    sqlite_journal.apply_journal_mode(conn, db_path)
            finally:
                conn.close()
        finally:
            holder.close()
