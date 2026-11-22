"""AddToStosMap, RemoveFromStosMap: an unknown StosMap stops the pipeline instead of exiting 0.

RC1 (2026-11-22): AddToStosMap -Name Pair306 printed "No stos map found" and the chain carried on, so the next
pipeline failed on the missing map instead.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from typing import cast

import pytest
from nornir_buildmanager import build
from nornir_buildmanager.volumemanager import (
    BlockNode,
    VolumeManager,
    XContainerElementWrapper,
)


@pytest.fixture
def volume_dir() -> Iterator[str]:
    base = tempfile.mkdtemp(prefix='nornir_stosmap_edit_')
    path = os.path.join(base, 'vol')
    volume = cast(XContainerElementWrapper, VolumeManager.Load(path, Create=True))
    _, block = volume.UpdateOrAddChildByAttrib(BlockNode.Create('TEM'), 'Name')
    stos_map = block.GetOrCreateStosMap('StosMap')
    stos_map.AddMapping(304, 306)
    VolumeManager.Save(volume)
    yield path
    shutil.rmtree(base, ignore_errors=True)


def _mappings(path: str, name: str) -> dict[int, set[int]]:
    volume = VolumeManager.Load(path)
    stos_map = volume.find(f"Block/StosMap[@Name='{name}']")
    return {int(m.Control): set(m.Mapped) for m in stos_map.Mappings}


@pytest.mark.parametrize('pipeline', ['AddToStosMap', 'RemoveFromStosMap'])
def test_unknown_stos_map_stops_the_pipeline(volume_dir: str, pipeline: str) -> None:
    with pytest.raises(Exception, match='Pair306'):
        build.Execute([volume_dir, '-debug', pipeline, '-Name', 'Pair306',
                       '-ControlSection', '307', '-MappedSection', '306'])
    assert _mappings(volume_dir, 'StosMap') == {304: {306}}


def test_add_to_existing_stos_map_saves_the_mapping(volume_dir: str) -> None:
    build.Execute([volume_dir, '-debug', 'AddToStosMap', '-Name', 'StosMap',
                   '-ControlSection', '307', '-MappedSection', '306'])
    assert _mappings(volume_dir, 'StosMap') == {304: {306}, 307: {306}}
