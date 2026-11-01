"""Copy large import sources to local disk ahead of use, and delete each copy when its import is done.

ImportMRC reads every tile of a section MRC (~33 GB for RC1) through a memmap. Over a network share the SMB
client kept only 1-3 reads in flight for that one file (RC1 on storage3, 2026-11-01: ~89-100 MB/s at ~2.8 MB per
read, while a sequential dd of the same mount reached 188 MB/s). Copying the next sections with large
sequential reads while the current one exports, several files at once, keeps more requests in flight; the
importer then reads a local file.

Only pixel reads use the copy: section names, notes, logs, cache paths and freshness checks keep the original
path. Staging is skipped when the source is already on the staging filesystem or there is not enough free space,
and can be turned off with ``NORNIR_IMPORT_STAGE=0``. ``NORNIR_IMPORT_STAGE_DIR`` chooses the folder (default: a
``nornir_import_stage`` folder in the system temp directory). Local disk use is at most the current file plus
``ahead`` prefetched files.

Safeguards (Build2 RC1 review, 2026-11-01): an optional ``validate`` callback keeps unhealthy sources (a truncated
MRC) from being copied; a copy that writes nothing for NO_PROGRESS_SECONDS is abandoned and the source is read in
place; a finished copy must match the source size and carries its mtime; copies are written as ``.partial`` and
renamed, and leftovers of killed runs are removed at start; each staged file logs its size, time and MB/s.
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from typing import Self

from nornir_imageregistration.env_flags import env_flag, is_falsey
from nornir_shared import prettyoutput

STAGE_ENV = 'NORNIR_IMPORT_STAGE'
STAGE_DIR_ENV = 'NORNIR_IMPORT_STAGE_DIR'

# Files copied ahead of the one being imported; they are copied concurrently.
DEFAULT_AHEAD = 2

# Free space kept on the staging filesystem beyond the file being copied.
RESERVE_BYTES = 2 * 1024 ** 3

COPY_BUFFER_BYTES = 16 * 1024 ** 2

# acquire() gives up on a copy that has written nothing for this long and reads the source in place. storage3
# (2026-11-01) left reads hanging for 30+ minutes; a thread stuck in a kernel read cannot be interrupted, but the
# import need not wait on it.
NO_PROGRESS_SECONDS = 300.0
PROGRESS_POLL_SECONDS = 10.0

# Leftovers from a killed run are removed at start when older than this (where the owning process cannot be
# checked) or when their process is gone.
STALE_SECONDS = 24 * 3600


def staging_enabled() -> bool:
    return not is_falsey(env_flag(STAGE_ENV))


def default_stage_dir() -> str:
    return os.environ.get(STAGE_DIR_ENV) or os.path.join(tempfile.gettempdir(), 'nornir_import_stage')


class SourcePrefetcher:
    """Stage ``paths`` to a local folder in order, ``ahead`` files ahead of the one in use.

    Use as a context manager; call ``acquire(path)`` for the path to read pixels from, and ``release(path)`` when
    that file's import is finished. ``acquire`` returns the original path whenever staging is off, skipped or
    failed, so callers never need a second code path.
    """

    def __init__(self, paths: Sequence[str], stage_dir: str | None = None, ahead: int = DEFAULT_AHEAD,
                 only_remote: bool = True, enabled: bool | None = None,
                 validate: Callable[[str], str | None] | None = None):
        """
        :param validate: Returns why a source should not be staged (e.g. a truncated MRC), or None; such a
            source is left in place and the caller decides what to do with it.
        """
        self._paths = list(paths)
        self._validate = validate
        self._progress: dict[str, float] = {}  # time of the last chunk written per path
        self._stage_dir = stage_dir or default_stage_dir()
        self._ahead = max(0, ahead)
        self._only_remote = only_remote
        self._enabled = staging_enabled() if enabled is None else enabled
        self._futures: dict[str, concurrent.futures.Future[str]] = {}
        self._stops: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._bytes_in_flight = 0  # sizes of copies still being written, so concurrent copies do not each see the same free space
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._closing = False
        self._next = 0

    def __enter__(self) -> Self:
        if self._enabled and self._paths:
            os.makedirs(self._stage_dir, exist_ok=True)
            _remove_stale(self._stage_dir)
            self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=self._ahead + 1,
                                                                   thread_name_prefix='ImportStage')
            self._schedule(self._ahead + 1)
        return self

    def __exit__(self, *exc) -> None:
        if self._executor is not None:
            self._closing = True
            for path in list(self._futures):
                self.release(path)  # stops running copies; finished or stopped ones are deleted
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None

    def acquire(self, path: str) -> str:
        """Path to read ``path``'s pixels from: the local copy once it is complete, else ``path`` itself."""
        if self._executor is None:
            return path
        with self._lock:
            future = self._futures.get(path)
        if future is None:
            return path
        while True:
            try:
                return future.result(timeout=PROGRESS_POLL_SECONDS)
            except concurrent.futures.TimeoutError:
                with self._lock:
                    last = self._progress.get(path)
                if last is not None and time.monotonic() - last > NO_PROGRESS_SECONDS:
                    prettyoutput.LogErr(f"Staging {path} made no progress for {NO_PROGRESS_SECONDS:.0f} s; "
                                        f"reading it in place")
                    with self._lock:
                        stop = self._stops.get(path)
                    if stop is not None:
                        stop.set()
                    return path
            except Exception as e:  # noqa: BLE001 - any copy failure falls back to reading the source
                prettyoutput.Log(f"Staging {path} failed ({e}); reading it in place")
                return path

    def release(self, path: str) -> None:
        """Stop or delete the local copy of ``path`` and start the next copy.

        A file can be released before its copy finishes (an import walking already-finished sections
        never acquires it): the copy is stopped at its next chunk, and anything it completes is deleted.
        """
        with self._lock:
            future = self._futures.pop(path, None)
            stop = self._stops.pop(path, None)
        if stop is not None:
            stop.set()
        # Started here rather than in acquire, so local disk holds at most the file in use plus ``ahead``.
        self._schedule(1)
        if future is not None:
            future.add_done_callback(lambda f, p=path: _delete_copy(f, p))

    def _schedule(self, count: int) -> None:
        if self._executor is None or self._closing:
            return
        with self._lock:
            while count > 0 and self._next < len(self._paths):
                path = self._paths[self._next]
                self._next += 1
                self._stops[path] = threading.Event()
                self._futures[path] = self._executor.submit(self._copy, path, self._stops[path])
                count -= 1

    def _copy(self, path: str, stop: threading.Event) -> str:
        if self._only_remote and _same_filesystem(path, self._stage_dir):
            return path
        if self._validate is not None:
            problem = self._validate(path)
            if problem is not None:
                prettyoutput.Log(f"Not staging {path}: {problem}")
                return path
        size = os.path.getsize(path)
        with self._lock:
            needed = size + self._bytes_in_flight + RESERVE_BYTES
            if shutil.disk_usage(self._stage_dir).free < needed:
                prettyoutput.Log(f"Not staging {path}: {self._stage_dir} lacks {needed / 1024 ** 3:.1f} GB free")
                return path
            self._bytes_in_flight += size
        local = os.path.join(self._stage_dir, f'{os.getpid()}_{self._paths.index(path)}_{os.path.basename(path)}')
        partial = local + '.partial'
        start = time.monotonic()
        try:
            with self._lock:
                self._progress[path] = start
            with open(path, 'rb') as source, open(partial, 'wb') as target:
                while not stop.is_set():
                    chunk = source.read(COPY_BUFFER_BYTES)
                    if not chunk:
                        break
                    target.write(chunk)
                    with self._lock:
                        self._progress[path] = time.monotonic()
            if stop.is_set():
                os.remove(partial)
                return path
            copied = os.path.getsize(partial)
            if copied != size:
                raise OSError(f"copy is {copied:,} bytes, source {size:,}")
            source_stat = os.stat(path)
            os.utime(partial, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
            os.replace(partial, local)
            seconds = max(time.monotonic() - start, 1e-6)
            prettyoutput.Log(f"Staged {os.path.basename(path)}: {size / 1024 ** 3:.1f} GB in {seconds:.0f} s "
                             f"({size / 1024 ** 2 / seconds:.0f} MB/s)")
        except BaseException:
            try:
                os.remove(partial)
            except OSError:
                pass
            raise
        finally:
            with self._lock:
                self._bytes_in_flight -= size
                self._progress.pop(path, None)
        return local


def _delete_copy(future: concurrent.futures.Future[str], path: str) -> None:
    if future.cancelled() or future.exception() is not None:
        return
    local = future.result()
    if local != path:
        try:
            os.remove(local)
        except OSError as e:
            prettyoutput.Log(f"Could not delete staged copy {local}: {e}")


def _remove_stale(stage_dir: str) -> None:
    """Delete copies and partial copies left by import runs that are no longer running.

    Staged names start with the owning process id. On POSIX a file whose process is gone is removed; where that
    cannot be checked (Windows) only files older than STALE_SECONDS are.
    """
    now = time.time()
    for name in os.listdir(stage_dir):
        pid_text = name.split('_', 1)[0]
        if not pid_text.isdigit() or int(pid_text) == os.getpid():
            continue
        full = os.path.join(stage_dir, name)
        try:
            stale = (not _pid_alive(int(pid_text))) if os.name == 'posix' else \
                now - os.path.getmtime(full) > STALE_SECONDS
            if stale:
                os.remove(full)
                prettyoutput.Log(f"Removed stale staged file {full}")
        except OSError as e:
            prettyoutput.Log(f"Could not remove stale staged file {full}: {e}")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # POSIX only: signal 0 checks for existence
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _same_filesystem(path: str, directory: str) -> bool:
    try:
        return os.stat(path).st_dev == os.stat(directory).st_dev
    except OSError:
        return False
