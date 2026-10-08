"""Metadata port stage 5: load/save timings on a real PMG fixture volume.

Copies ``PlatformRaw/PMG/6259_Registered`` from ``TESTINPUTPATH`` into
``TESTOUTPUTPATH``, builds ``VolumeData.db`` with ``migrate_volume``, then
records median wall times for ``VolumeManager.Load``, load plus
``LoadAllLinkedNodes``, and a no-op ``Save`` with metadata flags off versus
shadow write and SQLite read enabled.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import statistics
import time
from collections.abc import Callable
from typing import Any, cast
from xml.etree import ElementTree

import pytest

import nornir_buildmanager.volumemanager as vm
from nornir_buildmanager.metadata import feature_flags, sqlite_read
from nornir_buildmanager.metadata.migrate import migrate_volume, verify_migration
from nornir_buildmanager.metadata.sqlite_backend import DEFAULT_DB_FILENAME
from nornir_buildmanager.volumemanager.levelnode import LevelNode

READ = feature_flags.READ_SQLITE_ENV
SHADOW = feature_flags.SHADOW_SQLITE_ENV
TIMINGS_VOLUME_ENV = feature_flags.TIMINGS_VOLUME_ENV

_DEFAULT_REL = os.path.join('PlatformRaw', 'PMG', '6259_Registered')
_COPY_NAME = 'metadata-port-read-timings'
_TIMING_REPEATS = 3
_TIMING_WARMUP = 1


def _testinput_root() -> str:
    return os.environ.get('TESTINPUTPATH', '/nornir-testdata')


def _remove_dead_links(root: str) -> None:
    """Drop *_Link nodes whose target has no VolumeData.xml via normal container save."""
    for dirpath, _, filenames in os.walk(root):
        if 'VolumeData.xml' not in filenames:
            continue
        container = vm.VolumeManager.Load(dirpath, UseCache=False)
        if container is None:
            continue
        removed = False
        for link in list(container):
            if not link.tag.endswith('_Link'):
                continue
            target = os.path.join(dirpath, link.attrib.get('Path', ''))
            if os.path.isfile(os.path.join(target, 'VolumeData.xml')):
                continue
            container.remove(link)
            removed = True
        if removed:
            container.Save()


def _prepare_volume_copy() -> str:
    source = os.path.join(_testinput_root(), _DEFAULT_REL)
    if not os.path.isfile(os.path.join(source, 'VolumeData.xml')):
        pytest.skip(f'PMG fixture missing under TESTINPUTPATH: {source}')
    out_root = os.environ.get('TESTOUTPUTPATH')
    if not out_root:
        pytest.skip('TESTOUTPUTPATH is unset; refusing to copy the fixture into the repo tree')
    dest = os.path.join(out_root, _COPY_NAME, '6259_Registered')
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    _remove_dead_links(dest)
    return dest


def _median_seconds(run: Callable[[], None], *, repeats: int = _TIMING_REPEATS,
                    warmup: int = _TIMING_WARMUP) -> float:
    for _ in range(warmup):
        run()
    samples: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        run()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


def _time_load(root: str) -> float:
    def once() -> None:
        volume = vm.VolumeManager.Load(root, UseCache=False)
        assert volume is not None

    return _median_seconds(once)


def _time_load_linked(root: str) -> float:
    def once() -> None:
        volume = vm.VolumeManager.Load(root, UseCache=False)
        assert volume is not None
        volume.LoadAllLinkedNodes()

    return _median_seconds(once)


def _time_noop_save(root: str) -> float:
    def once() -> None:
        volume = vm.VolumeManager.Load(root, UseCache=False)
        assert volume is not None
        volume.LoadAllLinkedNodes()
        vm.VolumeManager.Save(volume)

    return _median_seconds(once)


@pytest.fixture(autouse=True)
def _metadata_load_only(monkeypatch) -> None:
    """Keep linked loads from repairing tile pyramids or racing on missing pipeline links."""

    def _level_valid(self: LevelNode) -> tuple[bool, str]:
        if not os.path.isdir(self.FullPath):
            return False, 'Directory does not exist'
        return True, ''

    monkeypatch.setattr(LevelNode, 'IsValid', _level_valid)
    real_executor = concurrent.futures.ThreadPoolExecutor

    def _sequential_executor(*args: Any, **kwargs: Any) -> concurrent.futures.ThreadPoolExecutor:
        kwargs['max_workers'] = 1
        return real_executor(*args, **kwargs)

    monkeypatch.setattr(concurrent.futures, 'ThreadPoolExecutor', _sequential_executor)


@pytest.fixture
def sources(monkeypatch) -> dict[str, bool]:
    used: dict[str, bool] = {}
    real = sqlite_read.load_container_element

    def recording(container_dir: str, xml_root: ElementTree.Element) -> ElementTree.Element:
        element = real(container_dir, xml_root)
        used[os.path.abspath(container_dir)] = element is not xml_root
        return element

    monkeypatch.setattr(sqlite_read, 'load_container_element', recording)
    return used


def _may_fallback_from_sqlite(container_dir: str, volume_root: str) -> bool:
    """Containers whose on-disk shard layout does not round-trip in per-container DB rows.

    ``verify_migration`` compares the merged logical tree; Block and some Filter shards on
    PMG 6259_Registered still fall back when only that folder's ``VolumeData.xml`` is checked.
    """
    rel = os.path.relpath(container_dir, volume_root)
    if rel == 'TEM':
        return True
    return os.path.basename(container_dir) in ('ShadingCorrected', 'Mask', 'Leveled')


def _volume_with_db() -> str:
    override = os.environ.get(TIMINGS_VOLUME_ENV, '').strip()
    root = os.path.abspath(override) if override else _prepare_volume_copy()
    if override and not os.path.isfile(os.path.join(root, 'VolumeData.xml')):
        pytest.skip(f'{TIMINGS_VOLUME_ENV} is not a volume root: {override!r}')
    result = migrate_volume(root, merge_first=False, force=True)
    assert result.success, result.message
    assert os.path.isfile(os.path.join(root, DEFAULT_DB_FILENAME))
    assert verify_migration(root), 'VolumeData.db must match XML before timings'
    return root


def test_pmg_registered_timings_and_sqlite_read(monkeypatch, sources: dict[str, bool]) -> None:
    root = _volume_with_db()
    timings: dict[str, Any] = {
        'volume': _DEFAULT_REL,
        'repeats': _TIMING_REPEATS,
        'warmup': _TIMING_WARMUP,
        'seconds': {},
    }

    monkeypatch.delenv(SHADOW, raising=False)
    monkeypatch.delenv(READ, raising=False)
    timings['seconds']['flagsOff'] = {
        'path': root,
        'load': _time_load(root),
        'loadAllLinked': _time_load_linked(root),
    }

    monkeypatch.setenv(SHADOW, '1')
    monkeypatch.setenv(READ, '1')
    sources.clear()
    volume = cast(vm.XContainerElementWrapper, vm.VolumeManager.Load(root, UseCache=False))
    volume.LoadAllLinkedNodes()
    assert sources, 'expected container loads while the read flag is on'
    for container_dir, used_sqlite in sources.items():
        if _may_fallback_from_sqlite(container_dir, root):
            continue
        assert used_sqlite, container_dir
    sqlite_reads = sum(sources.values())
    allowed = sum(1 for d in sources if _may_fallback_from_sqlite(d, root))
    assert sqlite_reads >= len(sources) - allowed, (sqlite_reads, len(sources), allowed, sources)

    timings['seconds']['shadowReadOn'] = {
        'path': root,
        'load': _time_load(root),
        'loadAllLinked': _time_load_linked(root),
        'noopSave': _time_noop_save(root),
    }

    monkeypatch.delenv(SHADOW, raising=False)
    monkeypatch.delenv(READ, raising=False)
    timings['seconds']['flagsOff']['noopSave'] = _time_noop_save(root)

    print('TIMINGS ' + json.dumps(timings, sort_keys=True), flush=True)
