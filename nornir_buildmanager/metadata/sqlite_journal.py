"""Choose the SQLite journal mode for a volume database from the filesystem it is stored on.

WAL keeps readers and the writer apart through a shared-memory ``-shm`` file, which is
only safe when every process opens the database on the same machine's local disk. On a
network share (SMB/CIFS, NFS, the 9p/drvfs/virtiofs mounts a dev container or WSL uses
for Windows folders, or a FUSE filesystem) the rollback journal (``DELETE``) is used
instead. A filesystem that cannot be identified is treated as non-local.
"""

import ctypes
import dataclasses
import logging
import os
import re
import sqlite3
import sys
from collections.abc import Iterable

logger = logging.getLogger(__name__)

WAL = 'WAL'
DELETE = 'DELETE'

MOUNT_TABLE_PATH = '/proc/self/mounts'

_NETWORK_FILESYSTEMS = frozenset({'cifs', 'smb3', 'smbfs', 'nfs', 'nfs4', '9p', 'drvfs', 'virtiofs'})
_MOUNT_ESCAPE = re.compile(r'\\([0-7]{3})')
_DRIVE_REMOTE = 4

MountEntry = tuple[str, str]

_logged_choices: set[tuple[str, str]] = set()


@dataclasses.dataclass(frozen=True)
class JournalChoice:
    """The journal mode picked for one database path and the filesystem evidence behind it."""

    mode: str
    filesystem: str | None
    mount_point: str | None


def is_network_filesystem(fstype: str) -> bool:
    """Return True for a filesystem type on which WAL's shared-memory file is unsafe.

    ``fuse`` covers every FUSE variant (``fuse``, ``fuseblk``, ``fuse.sshfs``, ...).
    """
    fstype = fstype.lower()
    return fstype in _NETWORK_FILESYSTEMS or fstype.startswith('fuse')


def is_unc_path(path: str) -> bool:
    """Return True for a Windows UNC path (``\\\\server\\share``, also ``//server/share`` on Windows)."""
    return path.startswith('\\\\') or (sys.platform == 'win32' and path.startswith('//'))


def read_mount_table(mount_table_path: str = MOUNT_TABLE_PATH) -> list[MountEntry] | None:
    """Return ``(mount_point, fstype)`` for each line of a Linux mount table, or None when it cannot be read."""
    try:
        with open(mount_table_path, encoding='utf-8', errors='surrogateescape') as handle:
            lines = handle.readlines()
    except OSError:
        return None
    entries: list[MountEntry] = []
    for line in lines:
        fields = line.split()
        if len(fields) >= 3:
            mount_point = _MOUNT_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), fields[1])
            entries.append((mount_point, fields[2]))
    return entries


def find_mount(path: str, mounts: Iterable[MountEntry]) -> MountEntry | None:
    """Return the mount holding *path*: the longest mount point containing it, the later entry on a tie."""
    best: MountEntry | None = None
    best_length = -1
    for mount_point, fstype in mounts:
        root = mount_point.rstrip('/')
        if (path == root or path.startswith(root + '/')) and len(root) >= best_length:
            best, best_length = (mount_point, fstype), len(root)
    return best


def choose_journal_mode(db_path: str, mounts: Iterable[MountEntry] | None = None) -> JournalChoice:
    """Return WAL when *db_path* is on a local disk, otherwise DELETE.

    *mounts* replaces the system mount table (tests). Without it, Windows asks for the drive
    type and other platforms read ``/proc/self/mounts``.
    """
    if is_unc_path(db_path):
        return JournalChoice(DELETE, 'UNC', None)
    if mounts is None and sys.platform == 'win32':
        drive = os.path.splitdrive(os.path.abspath(db_path))[0]
        remote = _windows_drive_is_remote(drive)
        return JournalChoice(DELETE if remote else WAL, 'remote drive' if remote else 'local drive', drive)

    entries = read_mount_table() if mounts is None else mounts
    mount = find_mount(os.path.realpath(db_path), entries) if entries is not None else None
    if mount is None:
        return JournalChoice(DELETE, None, None)
    mount_point, fstype = mount
    return JournalChoice(DELETE if is_network_filesystem(fstype) else WAL, fstype, mount_point)


def _windows_drive_is_remote(drive: str) -> bool:
    if sys.platform != 'win32' or not drive:
        return False
    return ctypes.windll.kernel32.GetDriveTypeW(drive + '\\') == _DRIVE_REMOTE


def apply_journal_mode(conn: sqlite3.Connection, db_path: str) -> str:
    """Set the journal mode chosen for *db_path* on *conn* and return the mode SQLite reports.

    The choice is logged at INFO once per database path and mode. A mode SQLite cannot use
    (WAL on an in-memory or unsupported database) leaves the old one, which is a warning.
    Leaving WAL needs every other connection closed; until then the pragma waits for the
    busy timeout and raises ``sqlite3.OperationalError`` rather than keep WAL on a share.
    """
    choice = choose_journal_mode(db_path)
    applied = str(conn.execute(f"PRAGMA journal_mode={choice.mode}").fetchone()[0]).upper()
    if applied != choice.mode:
        logger.warning("SQLite journal mode for %s is %s, wanted %s (filesystem %s at %s)",
                       db_path, applied, choice.mode, choice.filesystem, choice.mount_point)
    elif (db_path, applied) not in _logged_choices:
        _logged_choices.add((db_path, applied))
        logger.info("SQLite journal mode %s for %s (filesystem %s at %s)",
                    applied, db_path, choice.filesystem or 'unknown', choice.mount_point)
    return applied
