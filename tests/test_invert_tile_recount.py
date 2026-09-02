"""InvertFilter must not accumulate NumberOfTiles across re-runs (#145)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from nornir_buildmanager.operations import tile


class TestRecountInvertTiles(unittest.TestCase):

    def test_recount_resets_before_adding(self):
        node = SimpleNamespace(NumberOfTiles=100)
        paths = ['a.png', 'b.png', 'missing.png']
        existing = {'a.png', 'b.png'}
        tile._recount_existing_tiles(node, paths, lambda p: p in existing)
        self.assertEqual(2, node.NumberOfTiles)

    def test_second_recount_does_not_double(self):
        node = SimpleNamespace(NumberOfTiles=0)
        paths = ['a.png', 'b.png']
        tile._recount_existing_tiles(node, paths, lambda p: True)
        tile._recount_existing_tiles(node, paths, lambda p: True)
        self.assertEqual(2, node.NumberOfTiles)


if __name__ == '__main__':
    unittest.main()
