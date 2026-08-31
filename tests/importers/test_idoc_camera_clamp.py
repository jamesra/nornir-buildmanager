"""Regression tests for the off-by-one camera clamp in the idoc importer (#150).

``_SetCameraBpp`` promises that "the maximum intensity reported for tiles does not exceed the
known capability of the camera", but clamped against ``1 << bpp``. A 14-bit camera saturates at
16383, so that bound was one too high, which broke the promise twice over: a tile reporting
exactly 16384 compared ``>`` against 16384 and escaped the clamp untouched, and a tile reporting
SerialEM's maxint outlier was clamped *onto* 16384 -- still a value the camera cannot produce.

Two other sites in the codebase already used the correct form (``CalculateHistogram`` in this
same file and ``camera_max_val`` in the mrc importer), so this was the lone outlier of three.

Worth being clear about the blast radius, because it shapes what these tests can assert: every
reachable consumer of the inflated value happens to mask it today. ``CalculateHistogram``
re-clamps correctly, and the ``GetImageBpp`` fallback that reads ``Max`` is unreachable (#151)
and would derive the same bpp either way. So this fix changes no current output. The tests
therefore assert the clamp's own contract rather than a downstream difference, and pin the
masking explicitly so that if a future change starts trusting ``Max``, the reason this was
latent is on record instead of being rediscovered.
"""

from __future__ import annotations

import math
import unittest

from nornir_buildmanager.importers.idoc import IDoc, IDocTileData

_REALISTIC_BPPS = (8, 12, 14, 16)


def build_idoc(bpp: int | None, tile_maxima: list[int]) -> IDoc:
    idoc = IDoc()
    for index, maximum in enumerate(tile_maxima):
        tile = IDocTileData(f'{index:03d}.tif')
        tile.MinMaxMean = [0, maximum, maximum / 2.0]
        idoc.tiles.append(tile)
    idoc._SetCameraBpp(bpp)
    return idoc


def camera_max(bpp: int) -> int:
    return (1 << bpp) - 1


class TestTheClampKeepsItsPromise(unittest.TestCase):
    """No tile may end up above what the camera can produce."""

    def test_no_tile_exceeds_the_camera_maximum(self):
        for bpp in _REALISTIC_BPPS:
            real_max = camera_max(bpp)
            idoc = build_idoc(bpp, [0, 1, real_max, real_max + 1, real_max + 2, 65535])
            for tile in idoc.tiles:
                self.assertLessEqual(tile.Max, real_max,
                                     f'{bpp}-bit camera: tile Max {tile.Max} exceeds {real_max}')

    def test_the_value_one_above_the_maximum_no_longer_escapes(self):
        # The specific hole the `>` comparison left: exactly 1 << bpp was neither below the
        # bound nor above it, so it passed through untouched.
        for bpp in _REALISTIC_BPPS:
            idoc = build_idoc(bpp, [1 << bpp])
            self.assertEqual(camera_max(bpp), idoc.tiles[0].Max,
                             f'{bpp}-bit camera: {1 << bpp} escaped the clamp')

    def test_an_out_of_range_tile_is_clamped_to_the_real_maximum(self):
        for bpp in _REALISTIC_BPPS:
            idoc = build_idoc(bpp, [65535 if bpp < 16 else 1 << 20])
            self.assertEqual(camera_max(bpp), idoc.tiles[0].Max)

    def test_the_serialem_maxint_outlier_lands_on_the_camera_maximum(self):
        # The outlier the clamp was written for: RC1-era SerialEM reported maxint for some
        # pixels on a 14-bit camera.
        idoc = build_idoc(14, [65535])
        self.assertEqual(16383, idoc.tiles[0].Max)

    def test_the_aggregate_maximum_is_also_in_range(self):
        for bpp in _REALISTIC_BPPS:
            idoc = build_idoc(bpp, [10, 1 << bpp, 65535])
            self.assertLessEqual(idoc.Max, camera_max(bpp),
                                 f'{bpp}-bit camera: IDoc.Max {idoc.Max} exceeds the camera')


class TestInRangeValuesAreUntouched(unittest.TestCase):
    """The clamp must not pull down values the camera can genuinely produce."""

    def test_the_camera_maximum_itself_survives(self):
        for bpp in _REALISTIC_BPPS:
            real_max = camera_max(bpp)
            idoc = build_idoc(bpp, [real_max])
            self.assertEqual(real_max, idoc.tiles[0].Max,
                             f'{bpp}-bit camera: a legitimate saturated tile was altered')

    def test_ordinary_values_survive(self):
        idoc = build_idoc(14, [0, 1, 100, 8192, 16382])
        self.assertEqual([0, 1, 100, 8192, 16382], [t.Max for t in idoc.tiles])

    def test_a_16_bit_camera_still_accepts_maxint(self):
        # 65535 is the legitimate maximum for a 16-bit camera, not an outlier, so the clamp
        # must leave it alone. Under the old bound it was left alone too; this pins that the
        # fix did not turn a valid value into a clamped one.
        idoc = build_idoc(16, [65535])
        self.assertEqual(65535, idoc.tiles[0].Max)

    def test_no_camera_bpp_means_no_clamping(self):
        idoc = build_idoc(None, [65535, 1 << 20])
        self.assertEqual([65535, 1 << 20], [t.Max for t in idoc.tiles])

    def test_a_tile_with_no_maximum_is_left_alone(self):
        idoc = IDoc()
        tile = IDocTileData('000.tif')
        idoc.tiles.append(tile)
        idoc._SetCameraBpp(14)
        self.assertIsNone(tile.Max)

    def test_an_idoc_with_no_tiles_does_not_raise(self):
        idoc = build_idoc(14, [])
        self.assertEqual([], idoc.tiles)


class TestItAgreesWithTheOtherTwoSites(unittest.TestCase):
    """The clamp was the odd one out among three sites computing the same bound."""

    def test_it_matches_the_histogram_path_in_the_same_file(self):
        # CalculateHistogram computes (1 << CameraBpp) - 1. Reproduced here rather than
        # called, because the real function needs image files on disk.
        for bpp in _REALISTIC_BPPS:
            idoc = build_idoc(bpp, [65535 if bpp < 16 else 1 << 20])
            histogram_bound = (1 << idoc.CameraBpp) - 1
            self.assertEqual(histogram_bound, idoc.Max,
                             f'{bpp}-bit camera: the clamp and the histogram bound disagree')

    def test_it_matches_the_mrc_importer(self):
        from nornir_buildmanager.importers import mrc

        source_path = mrc.__file__
        with open(source_path, 'r', encoding='utf-8') as handle:
            mrc_text = handle.read()

        self.assertIn('(1 << CameraBpp) - 1', mrc_text,
                      'the mrc importer no longer uses the form this fix aligned with; '
                      'if it changed, revisit which form is correct')

    def test_the_histogram_reclamp_is_now_a_no_op(self):
        # It used to be the thing hiding the defect. Recording that it is now redundant
        # explains why removing it would be safe, without removing it here.
        for bpp in _REALISTIC_BPPS:
            idoc = build_idoc(bpp, [65535 if bpp < 16 else 1 << 20])
            max_val = idoc.Max
            if (1 << idoc.CameraBpp) - 1 < max_val:
                max_val = (1 << idoc.CameraBpp) - 1
            self.assertEqual(idoc.Max, max_val,
                             'the histogram path still has to correct the clamp')


class TestWhyThisWasLatent(unittest.TestCase):
    """Pins the two things that masked the defect, so the reasoning is not lost."""

    def test_the_derived_bpp_was_insensitive_to_the_off_by_one(self):
        # GetImageBpp's fallback derives bpp with ceil(log2(Max)). Both the old and new bounds
        # give the same answer for every realistic camera, so fixing #151 would not have
        # surfaced this. Asserted rather than assumed.
        for bpp in _REALISTIC_BPPS:
            from_old_bound = math.ceil(math.log2(1 << bpp))
            from_new_bound = math.ceil(math.log2((1 << bpp) - 1))
            self.assertEqual(from_old_bound, from_new_bound,
                             f'{bpp}-bit camera: the derived bpp did depend on the bound, so '
                             f'this bug was not latent after all')

    def test_the_old_bound_really_was_out_of_range(self):
        # Premise guard: documents the magnitude of what was wrong, so the fix is not just an
        # unexplained character change in a diff.
        for bpp in _REALISTIC_BPPS:
            old_bound = 1 << bpp
            self.assertGreater(old_bound, camera_max(bpp))
            self.assertEqual(1, old_bound - camera_max(bpp),
                             'the old bound should be exactly one above the camera maximum')


if __name__ == '__main__':
    unittest.main()
