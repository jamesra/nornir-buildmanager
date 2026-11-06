"""The pyramid tile mover copies locally built tiles on its own threads and reports failures when it closes."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from nornir_buildmanager.operations import tile as tile_ops


class TestTileMover(unittest.TestCase):

    def test_close_waits_for_every_move(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            dest = os.path.join(root, 'dest')
            os.makedirs(dest)
            mover = tile_ops._TileMover(3, dest)
            for i in range(20):
                local = os.path.join(root, f'{i}.png')
                with open(local, 'wb') as f:
                    f.write(b'tile%d' % i)
                mover.submit(local, os.path.join(dest, f'{i}.png'))
            mover.close()
            self.assertEqual(len(os.listdir(dest)), 20)
            with open(os.path.join(dest, '7.png'), 'rb') as f:
                self.assertEqual(f.read(), b'tile7')
            self.assertTrue(os.path.exists(os.path.join(root, '7.png')), 'the local tile stays for the next level')

    def test_close_raises_the_first_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            mover = tile_ops._TileMover(2, root)
            with mock.patch.object(tile_ops.tileset_functions, 'copy_tile_to_destination',
                                   side_effect=PermissionError('boom')):
                mover.submit(os.path.join(root, 'a.png'), os.path.join(root, 'out', 'a.png'))
                with self.assertRaises(PermissionError):
                    mover.close()


if __name__ == '__main__':
    unittest.main()
