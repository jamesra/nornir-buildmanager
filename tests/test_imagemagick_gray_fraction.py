"""Regression for #202: ImageMagick gray() fraction must honor bits-per-pixel."""
from __future__ import annotations

import unittest

from nornir_buildmanager.operations import tile


class TestImagemagickGrayFraction(unittest.TestCase):
    def test_eight_bit_matches_historical_scale(self) -> None:
        self.assertAlmostEqual(tile._imagemagick_gray_fraction(128.0, 8), 128.0 / 256.0)
        self.assertAlmostEqual(tile._imagemagick_gray_fraction(None, None), 0.0)

    def test_sixteen_bit_uses_65536_denominator(self) -> None:
        self.assertAlmostEqual(tile._imagemagick_gray_fraction(32768.0, 16), 32768.0 / 65536.0)
        self.assertNotAlmostEqual(
            tile._imagemagick_gray_fraction(32768.0, 16),
            32768.0 / 256.0,
        )


if __name__ == '__main__':
    unittest.main()
