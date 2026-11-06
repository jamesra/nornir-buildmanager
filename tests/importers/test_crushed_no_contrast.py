"""Crushed builds: no histogram pre-pass, no contrast on crushed filters, a robust offset for signed sources.

James, 2026-11-05: crushed imports need no section histogram before converting, and no contrast mapping may be
applied to crushed filters anywhere. Prune keeps its score scale through ``CrushCodeSpan``. RC2 139 (int32 TIFs)
got CrushOffset -15951 and 9-bit codes from one stray negative pixel; the offset now follows a robust minimum.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, cast

import nornir_pools
import numpy as np
from nornir_buildmanager.importers import shared
from nornir_buildmanager.operations import setters, tile
from nornir_buildmanager.operations.pruneobj import PruneObj
from nornir_buildmanager.volumemanager import FilterNode
from nornir_imageregistration import noise_crush
from nornir_imageregistration.noise_crush import CrushParams
from PIL import Image

PARAMS = CrushParams(k=2.5, step=100, sigma=40.0, offset=0, bits=8, source_dtype='uint16')


def _section(dtype, rng: np.random.Generator, count: int = 6, mean: float = 24000, sigma: float = 48) -> list[np.ndarray]:
    low = 0 if np.issubdtype(np.dtype(dtype), np.unsignedinteger) else -30000
    return [np.clip(rng.normal(mean, sigma, (256, 256)), low, 30000).astype(dtype) for _ in range(count)]


def _noise(tiles: list[np.ndarray], tmp: str) -> shared.SectionNoise:
    paths = []
    for i in range(len(tiles)):
        paths.append(os.path.join(tmp, f'{i}.npy'))
        with open(paths[-1], 'w'):
            pass
    return shared.GetSectionNoise(os.path.join(tmp, 'cache', 'Histogram.xml'), paths, len(tiles), lambda i: tiles[i])


class TestSignedOffset:
    def test_int32_outlier_no_longer_sets_the_offset(self):
        tiles = _section(np.int32, np.random.default_rng(3))
        tiles[0][5, 5] = -15951  # the RC2 139 outlier
        with tempfile.TemporaryDirectory() as tmp:
            noise = _noise(tiles, tmp)
        assert noise.sample_min == -15951
        params = shared.SectionCrushParams(noise, None, k=2.5)
        assert params.offset == 0
        assert params.bits == 8
        # Before the fix: the outlier set the offset and pushed the codes past 8 bits.
        old = noise_crush.make_crush_params(noise.sigma, noise.sample_max, noise.source_dtype, k=2.5,
                                            section_min=noise.sample_min)
        assert old.offset == -15951 and old.bits == 9

    def test_genuinely_negative_signed_data_keeps_a_negative_offset(self):
        tiles = _section(np.int16, np.random.default_rng(4), mean=-200, sigma=40)
        with tempfile.TemporaryDirectory() as tmp:
            noise = _noise(tiles, tmp)
        params = shared.SectionCrushParams(noise, None, k=2.5)
        assert params.offset < -300
        assert params.offset >= int(np.floor(noise.sample_min))

    def test_uint16_params_are_unchanged(self):
        tiles = _section(np.uint16, np.random.default_rng(5))
        with tempfile.TemporaryDirectory() as tmp:
            noise = _noise(tiles, tmp)
        assert noise.sample_low is None
        expected = noise_crush.make_crush_params(noise.sigma, noise.sample_max, 'uint16', k=2.5,
                                                 section_min=noise.sample_min)
        assert shared.SectionCrushParams(noise, None, k=2.5) == expected

    def test_signed_cache_without_sample_low_is_re_estimated(self):
        tiles = _section(np.int32, np.random.default_rng(6))
        with tempfile.TemporaryDirectory() as tmp:
            noise = _noise(tiles, tmp)
            cache = os.path.join(tmp, 'cache', 'Histogram' + shared.NOISE_CACHE_SUFFIX)
            with open(cache) as f:
                meta = json.load(f)
            del meta['sample_low']
            with open(cache, 'w') as f:
                json.dump(meta, f)
            loads = []
            paths = [os.path.join(tmp, f'{i}.npy') for i in range(len(tiles))]
            again = shared.GetSectionNoise(os.path.join(tmp, 'cache', 'Histogram.xml'), paths, len(tiles),
                                           lambda i: (loads.append(i), tiles[i])[1])
        assert loads, "an old signed cache must be re-estimated"
        assert again.sample_low == noise.sample_low


class TestCrushStatsDuringConversion:
    def teardown_method(self):
        nornir_pools.ClosePools()

    def test_crush_tile_returns_histogram_and_clips(self):
        image = np.full((8, 8), 1000, np.uint16)
        image[0, 0] = 60000  # above 255 * 100: clipped high
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 's.tif')
            Image.fromarray(image).save(source)
            stats = noise_crush.crush_tile_to_file(source, os.path.join(tmp, 'o.png'), PARAMS)
        assert stats.code_histogram.sum() == 64
        assert stats.code_histogram[10] == 63 and stats.code_histogram[255] == 1
        assert stats.clipped_high == 1 and stats.clipped_low == 0

    def test_crush_tiles_sums_tile_stats(self):
        rng = np.random.default_rng(7)
        with tempfile.TemporaryDirectory() as tmp:
            mapping = {}
            for i in range(3):
                source = os.path.join(tmp, f'{i}.tif')
                Image.fromarray(rng.integers(0, 20000, (16, 16), dtype=np.uint16)).save(source)
                mapping[source] = os.path.join(tmp, 'out', f'{i}.png')
            stats = shared.CrushTiles(mapping, PARAMS, flip=False)
        assert stats is not None and stats.tiles == 3
        assert stats.code_histogram.sum() == 3 * 16 * 16
        assert shared.CrushTiles({}, PARAMS, flip=False) is None

    def test_code_span_matches_cutoffs(self):
        histogram = np.zeros(256, np.int64)
        histogram[20:61] = 100  # codes 20..60
        # Cutoffs are the import's ContrastCutoffs: (min fraction, max fraction), not two trim amounts.
        assert shared.CrushCodeSpan(histogram, (0.0, 1.0)) == 41
        assert shared.CrushCodeSpan(histogram, (0.0001, 0.9999)) == 41
        assert shared.CrushCodeSpan(np.zeros(256), (0.0001, 0.9999)) is None

    def test_rc2_139_span_from_crushed_section(self):
        """Build2, 2026-11-05: RC2 139 (codes about 5-52) got CrushCodeSpan 1 because -Max was read as a trim."""
        rng = np.random.default_rng(139)
        params = CrushParams(k=2.5, step=120, sigma=47.8, offset=0, bits=8, source_dtype='int32')
        with tempfile.TemporaryDirectory() as tmp:
            mapping = {}
            for i in range(4):
                codes = rng.integers(5, 53, (64, 64))  # codes 5..52, as Build2 saw
                raw = (codes * params.step + rng.integers(0, params.step, codes.shape)).astype(np.int32)
                source = os.path.join(tmp, f'{i}.tif')
                Image.fromarray(raw).save(source)
                mapping[source] = os.path.join(tmp, 'out', f'{i}.png')
            stats = shared.CrushTiles(mapping, params, flip=False)
        assert stats is not None and stats.code_histogram[5:53].sum() == 4 * 64 * 64
        span = shared.CrushCodeSpan(stats.code_histogram, (0.0001, 0.9999))
        assert 46 <= span <= 48
        node = _filter_node(params.to_attributes(), CrushCodeSpan=span)
        assert 5.0 < PruneObj.CrushedScoreScale(node) < 6.0


class _Filter:
    def __init__(self, attrib: dict[str, str]):
        self.attrib = attrib
        self.FullPath = '/vol/TEM/0139/TEM/Raw8'


def _filter_node(attrib: dict[str, str], **properties) -> FilterNode:
    """A real FilterNode (so the wrapper's change tracking runs), loaded clean with *attrib*, then *properties* set."""
    node = FilterNode.Create('Raw8', **attrib)
    node.AttributesChanged = False
    for (name, value) in properties.items():
        setattr(node, name, value)
    return node


class TestNoContrastOnCrushedFilters:
    def test_clear_moves_old_cutoffs_into_the_span(self):
        node = _filter_node({**PARAMS.to_attributes(), 'MinIntensityCutoff': '1.22', 'MaxIntensityCutoff': '40.965',
                             'Gamma': '1'})
        shared.ClearCrushedFilterContrast(node)
        assert not {'MinIntensityCutoff', 'MaxIntensityCutoff', 'Gamma'} & set(node.attrib)
        assert node.CrushCodeSpan == round(40.965 - 1.22, 3)
        assert node.AttributesChanged  # so the pipeline saves the filter

    def test_clear_marks_the_filter_changed_without_a_span_move(self):
        # The deletions alone must reach the saved file (raw attrib deletes did not mark the node changed).
        node = _filter_node({**PARAMS.to_attributes(), 'CrushCodeSpan': '51', 'Gamma': '1'})
        shared.ClearCrushedFilterContrast(node)
        assert 'Gamma' not in node.attrib and node.AttributesChanged

    def test_prune_scale_uses_the_span(self):
        node = _filter_node(PARAMS.to_attributes(), CrushCodeSpan=51)
        assert PruneObj.CrushedScoreScale(node) == 5.0
        older = _filter_node(PARAMS.to_attributes(), MinIntensityCutoff=5.0, MaxIntensityCutoff=56.0)
        assert PruneObj.CrushedScoreScale(older) == 5.0

    def test_contrast_stages_skip_crushed_filters(self):
        crushed = _Filter(PARAMS.to_attributes())
        assert tile.IsCrushedFilter(crushed) and not tile.IsCrushedFilter(_Filter({}))
        # AutolevelTiles would build Leveled from the crushed codes; it must not touch the filter at all.
        crushed.TilePyramid = None  # any access would raise
        assert list(tile.AutolevelTiles({}, cast(Any, crushed), cast(Any, None))) == []
        assert list(tile.HistogramFilter({}, cast(Any, crushed), 4, cast(Any, None))) == []

    def test_set_contrast_refuses_crushed_filter(self, caplog):
        crushed = _Filter(PARAMS.to_attributes())

        class _Histogram:
            def FindParent(self, tag):
                return crushed if tag == 'Filter' else None

            def GetOrCreateAutoLevelHint(self):
                raise AssertionError('no contrast hint may be created for a crushed filter')

        with caplog.at_level(logging.WARNING):
            assert setters.SetContrastRange(_Histogram(), 1.0, 2.0, 1.0) is None
        assert any('crushed' in r.getMessage() for r in caplog.records)


class TestCrushSpanWherePruneReadsIt:
    """2026-11-05 reimports: Prune read the Raw8 <Filter>, which lacked CrushCodeSpan because it was never saved."""

    def test_setting_the_span_marks_the_filter_changed(self):
        node = _filter_node(PARAMS.to_attributes(), CrushCodeSpan=47.0004)
        assert node.AttributesChanged and node.attrib['CrushCodeSpan'] == '47'
        assert abs(PruneObj.CrushedScoreScale(node) - 255.0 / 47) < 1e-9

    def test_contrast_override_sets_the_legacy_span(self):
        # RC2 1226: legacy Raw8 cutoffs 4500-9000 counts, crush step 259 -> 17.4 codes (legacy prune scale)
        params = CrushParams(k=2.5, step=259, sigma=103.8, offset=0, bits=8, source_dtype='uint16')
        override = shared.ContrastValue(1226, 4500, 9000, 1.0)
        assert abs(shared.CrushCodeSpanFromContrastOverride(override, params) - 4500 / 259) < 1e-9
        assert shared.CrushCodeSpanFromContrastOverride(None, params) is None
        assert shared.CrushCodeSpanFromContrastOverride(shared.ContrastValue(1226, 0, 0, 1.0), params) is None
