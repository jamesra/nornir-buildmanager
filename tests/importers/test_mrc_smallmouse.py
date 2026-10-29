"""Basic MRC importer tests on real SerialEM files.

``TESTINPUTPATH/PlatformRaw/MRC/SmallMouse`` holds the two raw ``.mrc`` sections of the
SmallMouse volume (``0001/1.mrc``, ``0002/2.mrc``; pixel mode 1, sixteen 4096x4096 tiles each).
``SmallMouse_2014Build`` beside it holds a few outputs of the 2014 build of the same volume:
the ``.mosaic`` transforms, ``histogram.txt``, and the 8-bit tile pyramid at downsample 16 and 64.

The header and tile-read tests pin what ``MRCFile`` reads today, so a refactor of the reader
(for example the pixel-mode lookups) has a real-file before/after check. The 2014 comparison
checks the importer's bottom-up row convention (``DEFAULT_FLIP_UD``) against images an
independent, older build produced from the same files.
"""

from __future__ import annotations

import hashlib
import os
import unittest

import numpy as np
from nornir_buildmanager.importers.mrc import MRCFile, flip_ud_for_section
from PIL import Image

_MRC_ROOT = os.path.join(os.environ.get('TESTINPUTPATH', ''), 'PlatformRaw', 'MRC')
_RAW = os.path.join(_MRC_ROOT, 'SmallMouse')
_BUILD_2014 = os.path.join(_MRC_ROOT, 'SmallMouse_2014Build')

# section number -> (relative path, SHA-256 of tile 0's raw bytes)
_SECTIONS: dict[int, tuple[str, str]] = {
    1: (os.path.join('0001', '1.mrc'), '9a3b63383b3b5ceab885c4f7ea33681873634b179e633f9af534262c0622ef01'),
    2: (os.path.join('0002', '2.mrc'), '5ca7a40366118e4be202bf649cf90d3abb59f90f787fa5aa406fbac4a6ee1494'),
}

_TILE_DIM = 4096
_NUM_TILES = 16
# The 2014 build trimmed 8 px from each tile edge (4080 px) before its 16x downsample (255 px).
_TRIM = 8
_DOWNSAMPLE = 16


def _path(section: int) -> str:
    """Full path of a raw SmallMouse section."""
    return os.path.join(_RAW, _SECTIONS[section][0])


def _have_raw() -> bool:
    """True when both raw SmallMouse sections are present under TESTINPUTPATH."""
    return all(os.path.isfile(_path(s)) for s in _SECTIONS)


def _block_mean(tile: np.ndarray) -> np.ndarray:
    """Trim the 2014 build's border and average 16x16 blocks, matching its downsample-16 tiles."""
    trimmed = tile.astype(np.float64)[_TRIM:-_TRIM, _TRIM:-_TRIM]
    n = trimmed.shape[0] // _DOWNSAMPLE
    return trimmed.reshape(n, _DOWNSAMPLE, n, _DOWNSAMPLE).mean(axis=(1, 3))


@unittest.skipUnless(_have_raw(), 'SmallMouse .mrc files not under TESTINPUTPATH/PlatformRaw/MRC')
class TestSmallMouseHeaders(unittest.TestCase):
    """Header fields and file layout of the two real sections."""

    def test_pixel_mode_lookups(self) -> None:
        """Mode 1 is signed 16-bit everywhere the reader derives it."""
        for section in _SECTIONS:
            with self.subTest(section=section):
                mrc = MRCFile.Load(_path(section))
                try:
                    self.assertEqual(1, mrc.img_pixel_mode)
                    self.assertEqual(2, mrc.bytes_per_pixel)
                    self.assertIs(np.int16, mrc.pixel_dtype)
                    self.assertEqual('I;16', mrc.pil_pixel_mode)
                finally:
                    mrc.mrc.close()

    def test_tiles_fill_the_file(self) -> None:
        """Sixteen 4096x4096 tiles end exactly at the end of the file."""
        for section in _SECTIONS:
            with self.subTest(section=section):
                mrc = MRCFile.Load(_path(section))
                try:
                    self.assertEqual(_NUM_TILES, mrc.num_tiles)
                    self.assertEqual((_TILE_DIM, _TILE_DIM), (mrc.img_XDim, mrc.img_YDim))
                    self.assertEqual(_TILE_DIM * _TILE_DIM * 2, mrc.image_length_in_bytes)
                    end = mrc._get_image_offset(_NUM_TILES - 1) + mrc.image_length_in_bytes
                    self.assertEqual(os.path.getsize(_path(section)), end)
                finally:
                    mrc.mrc.close()

    def test_every_tile_has_stage_coordinates(self) -> None:
        """SerialEM wrote stage coordinates for every tile, so the importer can place them."""
        for section in _SECTIONS:
            with self.subTest(section=section):
                mrc = MRCFile.Load(_path(section))
                try:
                    self.assertEqual(_NUM_TILES, len(mrc.tile_meta))
                    for tile in mrc.tile_meta:
                        coords = tile.stage_coords
                        assert coords is not None, f'tile {tile.ID} has no stage coordinates'
                        self.assertTrue(np.all(np.isfinite(coords)))
                finally:
                    mrc.mrc.close()


@unittest.skipUnless(_have_raw(), 'SmallMouse .mrc files not under TESTINPUTPATH/PlatformRaw/MRC')
class TestSmallMouseTileReads(unittest.TestCase):
    """The three tile accessors agree, and tile 0's bytes are pinned."""

    def test_tile_zero_bytes_are_pinned(self) -> None:
        """A reader change that alters the bytes of tile 0 fails here."""
        for section, (_, expected) in _SECTIONS.items():
            with self.subTest(section=section):
                mrc = MRCFile.Load(_path(section))
                try:
                    data = mrc.get_tile_as_bytes(0)
                    self.assertEqual(mrc.image_length_in_bytes, len(data))
                    self.assertEqual(expected, hashlib.sha256(data).hexdigest())
                finally:
                    mrc.mrc.close()

    def test_numpy_and_memmap_reads_agree(self) -> None:
        """``get_tile_as_numpy`` and ``get_tile_as_numpy_memmap`` return the same pixels."""
        for section in _SECTIONS:
            mrc = MRCFile.Load(_path(section))
            try:
                for i in (0, _NUM_TILES - 1):
                    with self.subTest(section=section, tile=i):
                        array = mrc.get_tile_as_numpy(i)
                        self.assertEqual((_TILE_DIM, _TILE_DIM), array.shape)
                        self.assertEqual(np.int16, array.dtype)
                        np.testing.assert_array_equal(array, mrc.get_tile_as_numpy_memmap(i))
            finally:
                mrc.mrc.close()


@unittest.skipUnless(_have_raw() and os.path.isdir(_BUILD_2014),
                     'SmallMouse .mrc files or SmallMouse_2014Build not under TESTINPUTPATH/PlatformRaw/MRC')
class TestSmallMouseMatches2014Build(unittest.TestCase):
    """Tiles, oriented as the importer orients them, match the 2014 build's images."""

    def test_flipped_tiles_match_the_2014_downsample(self) -> None:
        """With the importer's flip the tiles correlate above 0.99; without it they do not."""
        for section, (relative, _) in _SECTIONS.items():
            folder = os.path.dirname(relative)
            flip = flip_ud_for_section(section, None)
            mrc = MRCFile.Load(_path(section))
            try:
                for i in (0, 5, 10, _NUM_TILES - 1):
                    with self.subTest(section=section, tile=i):
                        png = os.path.join(_BUILD_2014, folder, '8-bit', '016', f'{section}.{i:03d}.png')
                        with Image.open(png) as image:
                            old = np.asarray(image.convert('L'), dtype=np.float64)
                        ours = _block_mean(mrc.get_tile_as_numpy(i))
                        oriented = np.flipud(ours) if flip else ours
                        self.assertEqual(old.shape, oriented.shape)
                        self.assertGreater(np.corrcoef(oriented.ravel(), old.ravel())[0, 1], 0.99)
                        self.assertLess(np.corrcoef(np.flipud(oriented).ravel(), old.ravel())[0, 1], 0.2)
            finally:
                mrc.mrc.close()


if __name__ == '__main__':
    unittest.main()
