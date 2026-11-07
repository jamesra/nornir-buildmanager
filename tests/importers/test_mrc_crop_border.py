"""-CropBorder: MRC tiles lose an artefact border on export and the Stage mosaic places the cropped tile."""
from __future__ import annotations

import os
import tempfile
import types
import unittest

import numpy as np
from PIL import Image

from nornir_buildmanager.importers import mrc, shared
from nornir_buildmanager.importers.mrc import MRCImport, crop_tile
from nornir_imageregistration import noise_crush
from nornir_imageregistration.noise_crush import CrushParams

PARAMS = CrushParams(k=2.5, step=4, sigma=2.0, offset=0, bits=8, source_dtype='uint16')
FRAME = 32
BORDER = 4


def _frame() -> np.ndarray:
    """A frame whose border ring is 9999 and whose interior counts up by position."""
    frame = np.full((FRAME, FRAME), 9999, dtype=np.uint16)
    frame[BORDER:-BORDER, BORDER:-BORDER] = (np.arange((FRAME - 2 * BORDER) ** 2, dtype=np.uint16)
                                              .reshape(FRAME - 2 * BORDER, FRAME - 2 * BORDER))
    return frame


class _FakeMrc:
    num_tiles = 1
    img_shape = np.array([FRAME, FRAME], dtype=np.int64)

    def __init__(self) -> None:
        self.tile_meta = [types.SimpleNamespace(pixel_coords=np.array([100.0, 200.0]))]

    def get_tile_as_numpy_memmap(self, iTile: int) -> np.ndarray:
        return _frame()


class TestCropTile(unittest.TestCase):

    def test_array_loses_the_border_on_every_edge(self) -> None:
        self.assertEqual(crop_tile(_frame(), BORDER).shape, (FRAME - 2 * BORDER,) * 2)
        self.assertTrue((crop_tile(_frame(), BORDER) != 9999).all())

    def test_pil_image_loses_the_border_on_every_edge(self) -> None:
        im = Image.fromarray(np.zeros((FRAME, FRAME), dtype=np.uint8))
        self.assertEqual(crop_tile(im, BORDER).size, (FRAME - 2 * BORDER,) * 2)

    def test_zero_border_is_the_identity(self) -> None:
        frame = _frame()
        self.assertIs(crop_tile(frame, 0), frame)


class TestCropBorderExport(unittest.TestCase):

    def test_exported_crushed_tile_is_the_cropped_size(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            MRCImport.ExportCrushedImage(_FakeMrc(), out, '.png', 0, PARAMS, flip_ud=False, crop_border=BORDER)
            tile = next(name for name in os.listdir(out) if name.endswith('.png'))
            with Image.open(os.path.join(out, tile)) as im:
                self.assertEqual(im.size, (FRAME - 2 * BORDER,) * 2)
                self.assertIn(noise_crush.PNG_TEXT_KEY, im.text)

    def test_default_keeps_the_whole_frame(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            MRCImport.ExportCrushedImage(_FakeMrc(), out, '.png', 0, PARAMS, flip_ud=False)
            tile = next(name for name in os.listdir(out) if name.endswith('.png'))
            with Image.open(os.path.join(out, tile)) as im:
                self.assertEqual(im.size, (FRAME, FRAME))

    def test_mosaic_places_the_cropped_tiles_where_their_content_was(self) -> None:
        mrc_file = _FakeMrc()
        mrc_file.tile_meta.append(types.SimpleNamespace(pixel_coords=np.array([110.0, 230.0])))
        full = MRCImport.CreateMosaic(mrc_file, '.png')
        cropped = MRCImport.CreateMosaic(mrc_file, '.png', crop_border=BORDER)
        first, second = sorted(full.ImageToTransform)
        frame_point = np.array([[10.0, 12.0]])  # the same frame pixel in each tile
        cropped_point = frame_point - BORDER  # the same pixel in the cropped tile
        full_gap = (full.ImageToTransform[second].Transform(frame_point)
                    - full.ImageToTransform[first].Transform(frame_point))
        cropped_gap = (cropped.ImageToTransform[second].Transform(cropped_point)
                       - cropped.ImageToTransform[first].Transform(cropped_point))
        np.testing.assert_allclose(full_gap, cropped_gap, atol=1e-6)
        np.testing.assert_allclose(full_gap, [[10.0, 30.0]], atol=1e-6)
        # Re-based to the zero origin of the cropped tiles, the whole mosaic sits one border nearer the origin.
        np.testing.assert_allclose(cropped.ImageToTransform[first].Transform(cropped_point),
                                   full.ImageToTransform[first].Transform(frame_point) - BORDER, atol=1e-6)


class TestCropBorderMarkerAndArgument(unittest.TestCase):

    def test_marker_records_the_border(self) -> None:
        with tempfile.TemporaryDirectory() as level:
            mrc._mark_export_complete(level, 3, 'x.mrc', BORDER)
            self.assertEqual(mrc._exported_crop_border(level), BORDER)
            self.assertTrue(mrc._export_marked_complete(level, 3))

    def test_marker_without_a_border_means_none(self) -> None:
        with tempfile.TemporaryDirectory() as level:
            with open(os.path.join(level, mrc.EXPORT_COMPLETE_MARKER), 'w', encoding='utf-8') as f:
                f.write('{"num_tiles": 3, "source": "x.mrc"}')
            self.assertEqual(mrc._exported_crop_border(level), 0)

    def test_argument_parsing(self) -> None:
        self.assertEqual(shared.CropBorderForImport(None), 0)
        self.assertEqual(shared.CropBorderForImport('8'), 8)
        self.assertEqual(shared.CropBorderForImport(8), 8)
        with self.assertRaises(ValueError):
            shared.CropBorderForImport(-1)


if __name__ == '__main__':
    unittest.main()
