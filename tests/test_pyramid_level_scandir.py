"""Regression for #203: pyramid level listing uses scandir basenames, not glob lists."""
from __future__ import annotations

import glob
import inspect
import os
import tempfile
import unittest

from nornir_buildmanager.operations import tile


class TestImageBasenamesInDir(unittest.TestCase):
    def test_matches_glob_basenames(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for name in ('a.png', 'b.png', 'skip.txt'):
                with open(os.path.join(temp_dir, name), 'wb') as handle:
                    handle.write(b'x')
            expected = frozenset(
                os.path.basename(path)
                for path in glob.glob(os.path.join(temp_dir, '*.png')))
            got = tile._image_basenames_in_dir(temp_dir, '.png')
            self.assertEqual(got, expected)
            # Old path kept full paths plus a basename frozenset; we keep names only.
            self.assertLess(
                sum(len(n) for n in got),
                sum(len(p) for p in glob.glob(os.path.join(temp_dir, '*.png'))))

    def test_missing_dir_is_empty(self) -> None:
        self.assertEqual(tile._image_basenames_in_dir('/no/such/dir', '.png'), frozenset())

    def test_build_tile_pyramids_uses_scandir_helper(self) -> None:
        """#203: level scan must not reintroduce glob.glob path lists."""
        source = inspect.getsource(tile.BuildTilePyramids)
        self.assertIn('_image_basenames_in_dir', source)
        self.assertNotIn('glob.glob', source)


if __name__ == '__main__':
    unittest.main()
