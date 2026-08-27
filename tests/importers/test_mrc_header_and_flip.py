"""Tests for MRC extended-header parsing and the FlipList-driven tile flip.

The SerialEM extended header stores signed shorts except for piece
coordinates (see https://bio3d.colorado.edu/imod/doc/mrc_format.txt), so a
negative stage position must not wrap to a large positive one.
"""

from __future__ import annotations

import struct
import unittest

import numpy as np

from nornir_buildmanager.importers.mrc import (
    DEFAULT_FLIP_UD,
    MRCTileHeader,
    MRCTileHeaderFlags,
    flip_ud_for_section,
)

NM_PER_PIXEL = 2.0


def _stage_header(stage_x_units: int, stage_y_units: int) -> bytes:
    """Build a stage-coordinate-only extended header record (microns * 25)."""
    return struct.pack('<hh', stage_x_units, stage_y_units)


class TestStageCoordSignedParse(unittest.TestCase):
    """Stage position is a signed short; negatives must stay negative."""

    def _stage_coords(self, x_units: int, y_units: int) -> np.ndarray:
        header = MRCTileHeader.Load(
            tile_id=0,
            header=_stage_header(x_units, y_units),
            tile_flags=MRCTileHeaderFlags.StageCoord,
            nm_per_pixel=NM_PER_PIXEL)
        self.assertIsNotNone(header.stage_coords)
        return np.asarray(header.stage_coords)

    def _pixel_coords(self, x_units: int, y_units: int) -> np.ndarray:
        header = MRCTileHeader.Load(
            tile_id=0,
            header=_stage_header(x_units, y_units),
            tile_flags=MRCTileHeaderFlags.StageCoord,
            nm_per_pixel=NM_PER_PIXEL)
        self.assertIsNotNone(header.pixel_coords)
        return np.asarray(header.pixel_coords)

    def test_positive_stage_coords_round_trip(self):
        np.testing.assert_allclose(self._stage_coords(2500, 5000), (100.0, 200.0))

    def test_negative_stage_coords_stay_negative(self):
        """Unsigned parsing turned -100 um into roughly +2521 um."""
        np.testing.assert_allclose(self._stage_coords(-2500, -5000), (-100.0, -200.0))

    def test_mixed_sign_pixel_coords_bracket_origin(self):
        """A section straddling the stage origin must not scatter its tiles."""
        coords = self._pixel_coords(-2500, 5000)
        self.assertLess(float(coords[0]), 0.0)
        self.assertGreater(float(coords[1]), 0.0)
        # 100 um -> 100000 nm / 2 nm per pixel.
        np.testing.assert_allclose(coords, (-50000.0, 100000.0))

    def test_piece_coords_remain_unsigned(self):
        """Piece coordinates are the documented unsigned exception."""
        raw = struct.pack('<HHH', 40000, 50000, 3)
        header = MRCTileHeader.Load(
            tile_id=0, header=raw,
            tile_flags=MRCTileHeaderFlags.PieceCoord,
            nm_per_pixel=NM_PER_PIXEL)
        np.testing.assert_array_equal(header.piece_coords, (40000, 50000, 3))


class TestRealWorldStageRecords(unittest.TestCase):
    """Byte patterns taken from production .mrc files.

    Two regimes matter. When every stage short in a section wraps by the same
    amount the unsigned parse was harmless, because TranslateToZeroOrigin
    removes the shared offset - those sections must keep importing exactly as
    before. When a section straddles the stage origin only some tiles wrap, and
    the unsigned parse throws them roughly 1.15e6 pixels out of place.
    """

    # SmallMouse/0001 tiles 0 and 3: stage Y negative for every tile.
    UNIFORM_WRAP = ((1454, 59356), (1468, 59894))
    # RABBIT_SALVAGE/0226 tiles 0 and 486: stage Y crosses zero mid-section.
    STRADDLING = ((49910, 63738), (51893, 1039))
    RABBIT_NM_PER_PIXEL = 2.17600011

    def _pixels(self, raw: tuple[int, int], nm_per_pixel: float) -> np.ndarray:
        header = MRCTileHeader.Load(
            tile_id=0,
            header=struct.pack('<HH', *raw),
            tile_flags=MRCTileHeaderFlags.StageCoord,
            nm_per_pixel=nm_per_pixel)
        self.assertIsNotNone(header.pixel_coords)
        return np.asarray(header.pixel_coords)

    def test_uniform_wrap_preserves_relative_layout(self):
        """Bit-identical to the old output once the origin offset is removed."""
        first, last = (self._pixels(raw, self.RABBIT_NM_PER_PIXEL)
                       for raw in self.UNIFORM_WRAP)
        unsigned = [np.asarray(raw, dtype=np.float64) / 25.0 * 1000.0 / self.RABBIT_NM_PER_PIXEL
                    for raw in self.UNIFORM_WRAP]
        # rtol covers the float32 the importer stores stage coordinates in.
        np.testing.assert_allclose(last - first, unsigned[1] - unsigned[0], rtol=1e-5)

    def test_straddling_section_no_longer_scatters(self):
        """The two tiles are neighbours in Y, not a section-height apart."""
        first, mid = (self._pixels(raw, self.RABBIT_NM_PER_PIXEL)
                      for raw in self.STRADDLING)
        signed_gap = abs(float(mid[1] - first[1]))

        unsigned_first_y = self.STRADDLING[0][1] / 25.0 * 1000.0 / self.RABBIT_NM_PER_PIXEL
        unsigned_mid_y = self.STRADDLING[1][1] / 25.0 * 1000.0 / self.RABBIT_NM_PER_PIXEL
        unsigned_gap = abs(unsigned_mid_y - unsigned_first_y)

        self.assertLess(signed_gap, 60000.0)
        self.assertGreater(unsigned_gap, 1.1e6)


class TestTiltAngleSignedParse(unittest.TestCase):
    """Tilt angle is degrees * 100 in a signed short."""

    def test_negative_tilt_angle(self):
        header = MRCTileHeader.Load(
            tile_id=0, header=struct.pack('<h', -3000),
            tile_flags=MRCTileHeaderFlags.TiltAngle,
            nm_per_pixel=NM_PER_PIXEL)
        self.assertIsNotNone(header.tilt_angle)
        self.assertAlmostEqual(float(header.tilt_angle or 0.0), -30.0)


class TestFlipListWiring(unittest.TestCase):
    """FlipList.txt was accepted by the importer but never read."""

    def test_unlisted_section_keeps_baseline(self):
        self.assertEqual(flip_ud_for_section(690, None), DEFAULT_FLIP_UD)
        self.assertEqual(flip_ud_for_section(690, []), DEFAULT_FLIP_UD)
        self.assertEqual(flip_ud_for_section(690, [691]), DEFAULT_FLIP_UD)

    def test_listed_section_inverts_baseline(self):
        self.assertEqual(flip_ud_for_section(690, [690]), not DEFAULT_FLIP_UD)
        self.assertEqual(flip_ud_for_section(690, [689, 690, 691]),
                         not DEFAULT_FLIP_UD)


if __name__ == '__main__':
    unittest.main()
