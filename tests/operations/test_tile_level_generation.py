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


def _write_tiles(pyramid: TilePyramidNode, level: int, names: tuple[str, ...] = ('000.png',)) -> None:
    level_dir = Path(pyramid.FullPath) / (pyramid.attrib['LevelFormat'] % level)
    level_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (level_dir / name).write_bytes(b'png')


def test_missing_level_is_built_before_assembly(tmp_path: Path) -> None:
    pyramid = _pyramid(tmp_path)
    with mock.patch.object(pyramid, 'GenerateLevels', side_effect=lambda levels: _write_tiles(pyramid, 16, ('000.png', '001.png'))) as gen:
        tile._ensure_tile_level_on_disk(pyramid, 16, logging.getLogger(__name__))
    gen.assert_called_once_with([16])


def test_level_with_tiles_on_disk_is_never_rebuilt(tmp_path: Path) -> None:
    """The 19817b8 case: tiles exist but the Level metadata does not."""
    pyramid = _pyramid(tmp_path)
    _write_tiles(pyramid, 16, ('000.png', '001.png'))
    with mock.patch.object(pyramid, 'GenerateLevels') as gen:
        tile._ensure_tile_level_on_disk(pyramid, 16, logging.getLogger(__name__))
    gen.assert_not_called()
    assert pyramid.GetLevel(16) is None


def test_partial_level_is_completed(tmp_path: Path) -> None:
    """A level with fewer tiles than its source (still being built elsewhere, or interrupted) is not trusted."""
    pyramid = _pyramid(tmp_path)
    _write_tiles(pyramid, 16)  # 1 of the 2 tiles level 001 has
    with mock.patch.object(pyramid, 'GenerateLevels') as gen:
        tile._ensure_tile_level_on_disk(pyramid, 16, logging.getLogger(__name__))
    gen.assert_called_once_with([16])


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


def _tileset_with_box(min_x: float, min_y: float, max_x: float, max_y: float) -> mock.Mock:
    tileset = mock.Mock()
    tileset.TargetBoundingBox = mock.Mock(MinX=min_x, MinY=min_y, MaxX=max_x, MaxY=max_y)
    return tileset


def test_centred_mosaic_reports_the_crop() -> None:
    """RC1 RABBIT 0198: an ir-tools mosaic centred on the origin assembled as its positive quadrant."""
    logger = mock.Mock()
    with mock.patch.object(tile.prettyoutput, 'LogErr') as log_err:
        tile._warn_if_mosaic_origin_is_negative(
            _tileset_with_box(-60318.6, -61026.8, 56688.8, 56996.4), 'Grid.mosaic', logger)
    message = logger.error.call_args.args[0]
    assert 'negative origin' in message and '23%' in message and 'TransformNodeToZeroOrigin' in message
    log_err.assert_called_once()


def test_zero_origin_and_small_refine_offsets_stay_quiet() -> None:
    logger = mock.Mock()
    for box in ((0, 0, 1000, 1000), (-3.5, -10.0, 1000, 1000)):
        tile._warn_if_mosaic_origin_is_negative(_tileset_with_box(*box), 'Grid.mosaic', logger)
    logger.error.assert_not_called()
