"""Cross-process advisory lock held for a whole multi-statement write to a volume's SQLite database.

SQLite's own lock covers one transaction. A writer whose work spans more than that (set up
the schema, replace rows, read them back for a parity check) also holds this lock, so two
processes, or two threads, never interleave those steps. The lock is a ``<database>.lock``
file beside the database, locked with ``flock`` on POSIX and ``msvcrt.locking`` on Windows.
Each acquisition opens its own handle, so threads of one process exclude each other too.
The file is left in place on release: deleting a lock file races with the next opener.

Readers do not take it. Writers always take it before opening their SQLite transaction, so
lock-aware writers cannot deadlock against each other. Processes that reach the same folder
through different filesystem drivers (a Windows process and a dev container on one share)
may not see each other's lock; SQLite's own lock has the same limit there.
"""

import contextlib
import os
import sys
import time
from collections.abc import Iterator

if sys.platform == 'win32':
    import msvcrt
else:
    import fcntl

LOCK_SUFFIX = '.lock'

_POLL_SECONDS = 0.05


class WriteLockTimeout(TimeoutError):
    """Another writer held the database's write lock for longer than the timeout."""


def lock_path_for(db_path: str) -> str:
    """Return the lock file path used for the database at *db_path*."""
    return db_path + LOCK_SUFFIX


@contextlib.contextmanager
def write_lock(db_path: str, timeout: float) -> Iterator[None]:
    """Hold the exclusive write lock for *db_path* for the body of the ``with`` block.

    Waits up to *timeout* seconds for another holder, then raises :class:`WriteLockTimeout`.
    Not reentrant: acquiring it again while held, even in the same thread, waits for the timeout.
    """
    lock_path = lock_path_for(db_path)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise WriteLockTimeout(f"write lock {lock_path} still held by another writer after {timeout:g} s")
            time.sleep(_POLL_SECONDS)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


if sys.platform == 'win32':
    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
