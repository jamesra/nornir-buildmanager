"""C13-B009: prune scores written on Windows (UNC keys) must still prune on Linux."""
import os
import tempfile
import unittest

from nornir_buildmanager.operations.pruneobj import PruneObj
from nornir_imageregistration.files import mosaicfile

_MOSAIC = """format_version_number: 1
number_of_images: 3
pixel_spacing: 1
use_std_mask: 0
image:
000.png
Rigid2DTransform_double_2_2 vp 3 0 0 0 fp 2 0 0
image:
001.png
Rigid2DTransform_double_2_2 vp 3 0 0 4000 fp 2 0 0
image:
002.png
Rigid2DTransform_double_2_2 vp 3 0 4000 0 fp 2 0 0
"""


class TestPruneWindowsKeys(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, 'in.mosaic'), 'w') as f:
            f.write(_MOSAIC)

    def _write(self, scores: dict[str, float], tolerance: float) -> mosaicfile.MosaicFile:
        PruneObj(scores).WritePruneMosaic(self.dir, 'in.mosaic', 'out.mosaic', Tolerance=tolerance)
        return mosaicfile.MosaicFile.Load(os.path.join(self.dir, 'out.mosaic'))

    def test_unc_keys_match_mosaic_entries(self):
        base = r'\\OpR-Marc-SYN2\Volumes\RC3\0800\TEM\Leveled\TilePyramid\004' + '\\'
        result = self._write({base + '000.png': 10.0, base + '001.png': 1.0, base + '002.png': 10.0}, 5.0)
        self.assertEqual(sorted(result.ImageToTransformString), ['000.png', '002.png'])

    def test_posix_keys_still_match(self):
        result = self._write({'/data/004/000.png': 10.0, '/data/004/001.png': 10.0}, 5.0)
        self.assertEqual(sorted(result.ImageToTransformString), ['000.png', '001.png'])

    def test_scored_image_missing_from_mosaic_is_skipped(self):
        result = self._write({'000.png': 10.0, '999.png': 10.0}, 5.0)
        self.assertEqual(sorted(result.ImageToTransformString), ['000.png'])

    def test_threshold_removing_every_tile_raises(self):
        with self.assertRaises(ValueError):
            self._write({'000.png': 1.0, '001.png': 1.0}, 5.0)


if __name__ == '__main__':
    unittest.main()
