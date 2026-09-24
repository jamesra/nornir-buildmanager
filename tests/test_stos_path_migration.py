"""Regression for #200: do not orphan short-named STOS when FullPath fails."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock

from nornir_buildmanager.operations import block


class TestSetSliceToVolumeStosPath(unittest.TestCase):
    def test_fullpath_error_does_not_orphan_short_named_file(self) -> None:
        """#200: FullPath failure must abort before Path is rewritten."""
        with tempfile.TemporaryDirectory() as temp_dir:
            old_path = os.path.join(temp_dir, '1-2.stos')
            with open(old_path, 'wb') as handle:
                handle.write(b'old')
            sidecar = block._unblended_sidecar_path(old_path)
            with open(sidecar, 'wb') as handle:
                handle.write(b'side')

            node = MagicMock()
            node.Path = '1-2.stos'
            node.ControlChannelName = 'TEM'
            node.ControlFilterName = 'L'
            node.MappedChannelName = 'TEM'
            node.MappedFilterName = 'L'
            type(node).FullPath = property(
                lambda self: (_ for _ in ()).throw(
                    Exception('FullPath could not be generated for resource')))

            with self.assertRaises(Exception) as ctx:
                block._set_slice_to_volume_stos_path(node, 1, 2)

            self.assertIn('FullPath', str(ctx.exception))
            self.assertEqual(node.Path, '1-2.stos')
            self.assertTrue(os.path.isfile(old_path))
            self.assertTrue(os.path.isfile(sidecar))

    def test_migrates_short_name_and_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            old_name = '1-2.stos'
            old_full = os.path.join(temp_dir, old_name)
            with open(old_full, 'wb') as handle:
                handle.write(b'old')
            old_sidecar = block._unblended_sidecar_path(old_full)
            with open(old_sidecar, 'wb') as handle:
                handle.write(b'side')

            node = MagicMock()
            node.Path = old_name
            node.ControlChannelName = 'TEM'
            node.ControlFilterName = 'L'
            node.MappedChannelName = 'TEM'
            node.MappedFilterName = 'L'
            state = {'path': old_name}

            def _set_path(value: str) -> None:
                state['path'] = value

            type(node).Path = property(lambda self: state['path'],
                                       lambda self, value: _set_path(value))
            type(node).FullPath = property(
                lambda self: os.path.join(temp_dir, state['path']))

            block._set_slice_to_volume_stos_path(node, 1, 2)

            self.assertFalse(os.path.exists(old_full))
            self.assertFalse(os.path.exists(old_sidecar))
            self.assertTrue(os.path.isfile(os.path.join(temp_dir, node.Path)))
            self.assertTrue(os.path.isfile(
                block._unblended_sidecar_path(os.path.join(temp_dir, node.Path))))


if __name__ == '__main__':
    unittest.main()
