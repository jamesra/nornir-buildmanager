"""Section metadata for a stage is loaded in parallel up front, then lookups read nothing.

``find()`` loads each ``*_Link`` container the first time a lookup reaches it, one file
at a time. Per-pair stages reach about six new files per section; on the NAS that was
58% of the Grid32 overlay stage (srv, RC3, 2026-11-02).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, cast

import nornir_buildmanager.volumemanager as vm
from nornir_buildmanager.volumemanager import container_storage, link_prefetch
from nornir_buildmanager.volumemanager.xcontainerelementwrapper import (
    XContainerElementWrapper,
)

from .metadata_port_characterize_data import build_volume


def _volume(tmp_path, sections=(1, 2, 3, 4)) -> tuple[str, Any]:
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root, sections=sections)
    volume = cast(vm.XContainerElementWrapper, vm.VolumeManager.Load(root))
    return root, volume.find('Block')


class _CountingStorage(container_storage.XmlContainerStorage):
    """Counts container loads and the most loads in flight at once."""

    def __init__(self, delay_s: float = 0.0) -> None:
        super().__init__()
        self.loads: list[str] = []
        self.in_flight = 0
        self.peak = 0
        self.delay_s = delay_s
        self._lock = threading.Lock()

    def load_container(self, container_dir: str):
        with self._lock:
            self.loads.append(container_dir)
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            time.sleep(self.delay_s)
            return super().load_container(container_dir)
        finally:
            with self._lock:
                self.in_flight -= 1


def _install(monkeypatch, storage: _CountingStorage) -> _CountingStorage:
    monkeypatch.setattr(XContainerElementWrapper, 'storage', storage)
    return storage


def _lookups(block, sections, filter_name='Leveled') -> None:
    """The lookups GetImage makes."""
    for number in sections:
        filter_node = block.GetSection(number).GetChannel('TEM').GetFilter(filter_name)
        assert filter_node is not None
        assert filter_node.HasImageset


def test_lookups_after_preload_read_no_files(tmp_path, monkeypatch):
    _, block = _volume(tmp_path)
    storage = _install(monkeypatch, _CountingStorage())
    wanted = [(n, 'TEM', 'Leveled') for n in (1, 2, 3, 4)]

    link_prefetch.preload_section_images(block, wanted)
    loaded = len(storage.loads)
    _lookups(block, (1, 2, 3, 4))

    assert loaded == 4 * 4, 'section, channel, filter and ImageSet for 4 sections'
    assert len(storage.loads) == loaded, 'lookups found everything already loaded'


def test_preload_reads_what_serial_lookups_would(tmp_path, monkeypatch):
    _, block = _volume(tmp_path)
    storage = _install(monkeypatch, _CountingStorage())
    _lookups(block, (1, 2, 3, 4))
    serial = sorted(storage.loads)

    _, block = _volume(tmp_path / 'again')
    storage = _install(monkeypatch, _CountingStorage())
    link_prefetch.preload_section_images(block, [(n, 'TEM', 'Leveled') for n in (1, 2, 3, 4)])

    rel = lambda paths, marker: sorted(p.split(marker, 1)[1] for p in paths)
    assert rel(storage.loads, 'again') == rel(serial, str(tmp_path))


def test_loads_run_in_parallel_up_to_max_workers(tmp_path, monkeypatch):
    _, block = _volume(tmp_path, sections=tuple(range(1, 9)))
    storage = _install(monkeypatch, _CountingStorage(delay_s=0.05))

    link_prefetch.preload_section_images(block, [(n, 'TEM', 'Leveled') for n in range(1, 9)], max_workers=4)

    assert 1 < storage.peak <= 4


def test_preload_leaves_containers_clean(tmp_path, monkeypatch):
    """Like find(), swapping a link for its container is not a change to save."""
    _, block = _volume(tmp_path)
    link_prefetch.preload_section_images(block, [(1, 'TEM', 'Leveled')])

    assert not block.ElementHasChangesToSave
    section = block.GetSection(1)
    assert not section.ElementHasChangesToSave


def test_mask_filter_and_its_imageset_are_preloaded(tmp_path, monkeypatch):
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root, sections=(1,))
    volume = cast(vm.XContainerElementWrapper, vm.VolumeManager.Load(root))
    leveled = volume.find('Block').GetSection(1).GetChannel('TEM').GetFilter('Leveled')
    leveled.MaskName = 'Raw8'
    vm.VolumeManager.Save(volume)

    volume = cast(vm.XContainerElementWrapper, vm.VolumeManager.Load(root))
    block = volume.find('Block')
    storage = _install(monkeypatch, _CountingStorage())
    link_prefetch.preload_section_images(block, [(1, 'TEM', 'Leveled')])
    loaded = len(storage.loads)

    mask = block.GetSection(1).GetChannel('TEM').GetFilter('Leveled').GetMaskFilter()
    assert mask is not None and mask.HasImageset
    assert len(storage.loads) == loaded == 6


def test_missing_sections_are_skipped(tmp_path, monkeypatch):
    _, block = _volume(tmp_path, sections=(1,))

    link_prefetch.preload_section_images(block, [(1, 'TEM', 'Leveled'), (99, 'TEM', 'Leveled'),
                                                 (1, 'NoSuchChannel', 'Leveled')])

    assert block.GetSection(99) is None
    _lookups(block, (1,))


def test_preload_descendants_loads_each_level(tmp_path, monkeypatch):
    """CreateVikingXML walks every section, channel, filter and pyramid; after the preload it reads nothing."""
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root, sections=(1, 2, 3))
    volume = cast(vm.XContainerElementWrapper, vm.VolumeManager.Load(root))
    storage = _install(monkeypatch, _CountingStorage())

    link_prefetch.preload_descendants(volume, (('Block',), ('Section',), ('Channel',), ('Filter',),
                                               ('TilePyramid', 'Tileset')))
    loaded = len(storage.loads)
    pyramids = list(volume.findall('Block/Section/Channel/Filter/TilePyramid'))
    tilesets = list(volume.findall('Block/Section/Channel/Filter/Tileset'))

    assert len(pyramids) == len(tilesets) == 3 * 2
    assert len(storage.loads) == loaded, 'the walk found everything already loaded'
    assert not volume.ElementHasChangesToSave
