"""Staging import sources to local disk ahead of use (source_staging).

RC1 ImportMRC on storage3 (2026-11-01) read ~89-100 MB/s with only 1-3 SMB requests in flight for the one
MRC being exported, while a sequential copy of the same mount reached 188 MB/s. Copying the next files while
the current one exports keeps more requests in flight; the importer then reads a local copy.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from nornir_buildmanager.importers import source_staging
from nornir_buildmanager.importers.source_staging import SourcePrefetcher


class _Files(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='test_source_staging_')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = os.path.join(self.tmp, 'source')
        self.stage = os.path.join(self.tmp, 'stage')
        os.makedirs(self.source)
        self.paths = []
        for i in range(5):
            path = os.path.join(self.source, f'{i:04d}.mrc')
            with open(path, 'wb') as f:
                f.write(bytes([i]) * (100_000 + i))
            self.paths.append(path)

    def prefetcher(self, **kwargs) -> SourcePrefetcher:
        # Test files share a filesystem with the stage folder; only_remote=False stages them anyway.
        return SourcePrefetcher(self.paths, stage_dir=self.stage, only_remote=False, enabled=True, **kwargs)

    def staged_files(self) -> list[str]:
        return sorted(os.listdir(self.stage)) if os.path.isdir(self.stage) else []


class TestStaging(_Files):

    def test_acquire_returns_an_identical_local_copy_and_release_deletes_it(self):
        with self.prefetcher() as staging:
            local = staging.acquire(self.paths[0])
            self.assertNotEqual(self.paths[0], local)
            self.assertEqual(os.path.dirname(local), self.stage)
            with open(local, 'rb') as a, open(self.paths[0], 'rb') as b:
                self.assertEqual(b.read(), a.read())
            staging.release(self.paths[0])
            self.assertFalse(os.path.exists(local))

    def test_every_file_in_order(self):
        with self.prefetcher(ahead=1) as staging:
            for path in self.paths:
                local = staging.acquire(path)
                self.assertEqual(os.path.getsize(path), os.path.getsize(local))
                staging.release(path)
        self.assertEqual([], self.staged_files())

    def test_no_more_than_ahead_plus_one_files_are_scheduled(self):
        with self.prefetcher(ahead=1) as staging:
            self.assertEqual(2, len(staging._futures))
            staging.acquire(self.paths[0])
            staging.release(self.paths[0])
            self.assertEqual(2, len(staging._futures))

    def test_a_stopped_copy_returns_the_source_and_leaves_no_partial_file(self):
        staging = self.prefetcher()
        os.makedirs(self.stage, exist_ok=True)
        stop = threading.Event()
        stop.set()
        self.assertEqual(self.paths[0], staging._copy(self.paths[0], stop))
        self.assertEqual([], self.staged_files())

    def test_releasing_stops_a_copy_in_progress(self):
        with open(self.paths[0], 'wb') as f:
            f.write(b'\x01' * 4_000_000)  # 40,000 chunks of 100 bytes: still copying when released
        with mock.patch.object(source_staging, 'COPY_BUFFER_BYTES', 100), self.prefetcher(ahead=0) as staging:
            future = staging._futures[self.paths[0]]
            staging.release(self.paths[0])
            self.assertEqual(self.paths[0], future.result(timeout=30), 'a stopped copy hands back the source')
        self.assertEqual([], self.staged_files())

    def test_files_released_without_acquire_leave_nothing_behind(self):
        # An import restarted over finished sections releases them without needing their pixels.
        with self.prefetcher(ahead=2) as staging:
            for path in self.paths:
                staging.release(path)
        self.assertEqual([], self.staged_files())


class TestFallbacks(_Files):

    def test_disabled_staging_reads_in_place(self):
        with mock.patch.dict(os.environ, {source_staging.STAGE_ENV: '0'}),                 SourcePrefetcher(self.paths, stage_dir=self.stage, only_remote=False) as staging:
            self.assertEqual(self.paths[0], staging.acquire(self.paths[0]))
        self.assertEqual([], self.staged_files())

    def test_a_source_on_the_staging_filesystem_is_read_in_place(self):
        with SourcePrefetcher(self.paths, stage_dir=self.stage, enabled=True) as staging:
            self.assertEqual(self.paths[0], staging.acquire(self.paths[0]))

    def test_too_little_free_space_reads_in_place(self):
        usage = shutil.disk_usage(self.tmp)._replace(free=1)
        with mock.patch.object(source_staging.shutil, 'disk_usage', return_value=usage), \
                mock.patch.object(source_staging.prettyoutput, 'Log') as logged, self.prefetcher() as staging:
            self.assertEqual(self.paths[0], staging.acquire(self.paths[0]))
        self.assertIn('Not staging', str(logged.call_args_list))

    def test_a_failed_copy_reads_in_place_and_leaves_no_partial_file(self):
        with mock.patch.object(source_staging.prettyoutput, 'Log'), self.prefetcher() as staging:
            os.remove(self.paths[3])  # the copy of this file will fail
            for path in self.paths:
                self.assertTrue(staging.acquire(path))
                staging.release(path)
        self.assertEqual([], self.staged_files())


class TestImportMRCWiring(_Files):
    """ImportMRC hands ToMosaic a callable for the staged copy and releases each file after its section."""

    def test_sections_export_from_staged_copies_and_leave_nothing_behind(self):
        from nornir_buildmanager.importers import mrc

        exported_from = []

        def fake_to_mosaic(volume, mrc_fullpath, read_path=None, **kwargs):
            local = read_path()
            with open(local, 'rb') as copy, open(mrc_fullpath, 'rb') as source:
                self.assertEqual(source.read(), copy.read())
            exported_from.append((mrc_fullpath, os.path.dirname(local)))
            yield from ()

        real_prefetcher = source_staging.SourcePrefetcher
        with mock.patch.object(mrc.MRCImport, 'ToMosaic', side_effect=fake_to_mosaic), \
                mock.patch.object(mrc.source_staging, 'SourcePrefetcher',
                                  side_effect=lambda paths: real_prefetcher(paths, stage_dir=self.stage,
                                                                            only_remote=False, enabled=True)), \
                mock.patch.object(mrc.nornir_buildmanager.importers, 'LoadOrCreateHistogramCutoffs', return_value={}):
            list(mrc.Import(mock.Mock(), self.source, Min=0.1, Max=0.9))

        self.assertEqual(sorted(self.paths), sorted(p for p, _ in exported_from))
        self.assertTrue(all(d == self.stage for _, d in exported_from))
        self.assertEqual([], self.staged_files())


if __name__ == '__main__':
    unittest.main()
