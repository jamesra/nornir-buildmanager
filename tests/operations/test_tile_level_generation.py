"""Assemble builds a tile-pyramid level that has no tiles, and BuildTilePyramids is a pipeline again.

Since 19817b8 (2026-08) Assemble looked levels up with ``GenerateData=False`` so that tiles on
disk without Level metadata are not rebuilt. That also left levels with no tiles at all
unbuilt: on RC1_Crush (only ``001``), ``Assemble -Downsample 16,32`` reported every ``016``
tile missing, failed with "No transforms in MosaicTileset", and left an empty
``<Level Path="016"/>`` behind (Build2 RC1, 2026-11).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

import pytest

from nornir_buildmanager.operations import tile
from nornir_buildmanager.volumemanager import TilePyramidNode

_PIPELINES = Path(tile.__file__).resolve().parents[1] / 'config' / 'Pipelines.xml'


def _pyramid(tmp_path: Path) -> TilePyramidNode:
    pyramid = TilePyramidNode.Create(NumberOfTiles=2)
    pyramid.Path = str(tmp_path / 'TilePyramid')
    _added, level_1 = pyramid.GetOrCreateLevel(1, GenerateData=False)
    assert level_1 is not None
    os.makedirs(level_1.FullPath)
    for name in ('000.png', '001.png'):
        (Path(level_1.FullPath) / name).write_bytes(b'png')
    return pyramid


def _write_tiles(pyramid: TilePyramidNode, level: int) -> None:
    level_dir = Path(pyramid.FullPath) / (pyramid.attrib['LevelFormat'] % level)
    level_dir.mkdir(parents=True, exist_ok=True)
    (level_dir / '000.png').write_bytes(b'png')


def test_missing_level_is_built_before_assembly(tmp_path: Path) -> None:
    pyramid = _pyramid(tmp_path)
    with mock.patch.object(pyramid, 'GenerateLevels', side_effect=lambda levels: _write_tiles(pyramid, 16)) as gen:
        tile._ensure_tile_level_on_disk(pyramid, 16, logging.getLogger(__name__))
    gen.assert_called_once_with([16])


def test_level_with_tiles_on_disk_is_never_rebuilt(tmp_path: Path) -> None:
    """The 19817b8 case: tiles exist but the Level metadata does not."""
    pyramid = _pyramid(tmp_path)
    _write_tiles(pyramid, 16)
    with mock.patch.object(pyramid, 'GenerateLevels') as gen:
        tile._ensure_tile_level_on_disk(pyramid, 16, logging.getLogger(__name__))
    gen.assert_not_called()
    assert pyramid.GetLevel(16) is None


def test_level_that_cannot_be_built_fails_clearly_without_an_empty_level_node(tmp_path: Path) -> None:
    pyramid = _pyramid(tmp_path)
    with mock.patch.object(pyramid, 'GenerateLevels'), pytest.raises(ValueError, match='016'):
        tile._ensure_tile_level_on_disk(pyramid, 16, logging.getLogger(__name__))
    assert pyramid.GetLevel(16) is None


def test_build_tile_pyramids_pipeline_calls_the_stage_on_each_filter_pyramid() -> None:
    root = ElementTree.parse(_PIPELINES).getroot()
    pipeline = next(p for p in root.iter('Pipeline') if p.get('Name') == 'BuildTilePyramids')
    args = {a.get('flag'): a for a in pipeline.iter('Argument')}
    assert {'-Sections', '-Channels', '-Filters', '-Downsample'} <= set(args)
    assert args['-Downsample'].get('required') == 'True'
    select = next(pipeline.iter('Select'))
    assert (select.get('VariableName'), select.get('Root'), select.get('XPath')) == \
           ('PyramidNode', 'filter_node', 'TilePyramid')
    call = next(pipeline.iter('PythonCall'))
    assert (call.get('Function'), call.get('Levels')) == ('tile.BuildTilePyramids', '#Levels')
