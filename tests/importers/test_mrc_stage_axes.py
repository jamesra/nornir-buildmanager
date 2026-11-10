"""MRC stage positions are placed like the IDOC importer's: transposed, with Y negated (RC1_Crush, 2026-11-10)."""

from __future__ import annotations

import types
import unittest

import numpy as np

from nornir_buildmanager.importers.mrc import MRCImport, stage_to_mosaic_offset


class TestStageAxes(unittest.TestCase):

    def test_offset_is_minus_y_then_x(self) -> None:
        np.testing.assert_allclose(stage_to_mosaic_offset(np.array([100.0, 30.0])), [-30.0, 100.0])

    def test_stage_left_to_right_neighbours_are_left_to_right_in_the_mosaic(self) -> None:
        """Tiles that differ in stage X only (one frame apart) must differ in mosaic X only."""
        frame = 64
        mrc_file = types.SimpleNamespace(img_shape=np.array([frame, frame], dtype=np.int64), tile_meta=[
            types.SimpleNamespace(pixel_coords=np.array([0.0, 0.0])),
            types.SimpleNamespace(pixel_coords=np.array([60.0, 0.0]))])
        mosaic = MRCImport.CreateMosaic(mrc_file, '.png')
        first, second = sorted(mosaic.ImageToTransform)
        point = np.array([[5.0, 5.0]])
        gap = mosaic.ImageToTransform[second].Transform(point) - mosaic.ImageToTransform[first].Transform(point)
        np.testing.assert_allclose(gap, [[0.0, 60.0]], atol=1e-6)


if __name__ == '__main__':
    unittest.main()
