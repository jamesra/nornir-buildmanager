"""StampCrushMetadata adds the nornir_crush chunk to crushed tile-pyramid tiles that lack it.

On RC1_Crush, levels 002-256 built before Shrink carried the chunk had crushed pixels but no
metadata, and James asked that every crushed image carry it (Build2 RC1, 2026-11-04).
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import numpy as np
from nornir_buildmanager.operations import tile
from nornir_imageregistration import noise_crush
from nornir_imageregistration.noise_crush import CrushParams
from PIL import Image

_PARAMS = CrushParams(k=2.5, step=12, sigma=4.8, offset=0, bits=8, source_dtype='uint16')
_PIPELINES = Path(tile.__file__).resolve().parents[1] / 'config' / 'Pipelines.xml'


def _filter(tmp_path: Path, attrib: dict[str, str]) -> SimpleNamespace:
    pyramid = tmp_path / 'TilePyramid'
    codes = np.arange(64, dtype=np.uint8).reshape(8, 8)
    (pyramid / '001').mkdir(parents=True)
    noise_crush.save_crushed_png(str(pyramid / '001' / '000.png'), codes, _PARAMS)  # ImportMRC's level, labelled
    for level in ('002', '004'):
        (pyramid / level).mkdir()
        Image.fromarray(codes).save(pyramid / level / '000.png')
    return SimpleNamespace(attrib=attrib, HasTilePyramid=True, TilePyramid=SimpleNamespace(FullPath=str(pyramid)),
                           FullPath=str(tmp_path))


def test_unlabelled_levels_are_stamped_and_labelled_ones_kept(tmp_path: Path) -> None:
    filter_node = _filter(tmp_path, _PARAMS.to_attributes())
    tile.StampCrushMetadata(filter_node, logging.getLogger(__name__))
    for level in ('001', '002', '004'):
        path = tmp_path / 'TilePyramid' / level / '000.png'
        assert noise_crush.read_crush_params(str(path)) == _PARAMS, level
        with Image.open(path) as im:
            np.testing.assert_array_equal(np.asarray(im), np.arange(64, dtype=np.uint8).reshape(8, 8))


def test_filters_without_crush_parameters_are_untouched(tmp_path: Path) -> None:
    filter_node = _filter(tmp_path, {})
    tile.StampCrushMetadata(filter_node, logging.getLogger(__name__))
    assert noise_crush.read_crush_params(str(tmp_path / 'TilePyramid' / '002' / '000.png')) is None


def test_pipeline_calls_the_stage_on_each_filter() -> None:
    root = ElementTree.parse(_PIPELINES).getroot()
    pipeline = next(p for p in root.iter('Pipeline') if p.get('Name') == 'StampCrushMetadata')
    assert {a.get('flag') for a in pipeline.iter('Argument')} == {'-Sections', '-Channels', '-Filters'}
    assert next(pipeline.iter('PythonCall')).get('Function') == 'tile.StampCrushMetadata'
