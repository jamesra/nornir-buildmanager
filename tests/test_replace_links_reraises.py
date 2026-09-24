"""Multi-link ``_replace_links`` must re-raise unexpected load errors (#137).

The single-link path logs and re-raises. The parallel path used to ``continue``,
leaving the unresolved ``*_Link`` stub in the tree while the caller proceeded as
if loading had finished.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from typing import cast
from unittest import mock

from nornir_buildmanager.volumemanager import BlockNode, VolumeManager, XContainerElementWrapper


class TestReplaceLinksReraisesUnexpectedErrors(unittest.TestCase):

    def setUp(self):
        self.root = os.path.join(tempfile.mkdtemp(prefix='nornir_links_raise_'), 'vol')
        os.makedirs(self.root, exist_ok=True)
        volume = VolumeManager.Load(self.root, Create=True)
        assert volume is not None
        self.volume = cast(XContainerElementWrapper, volume)
        for name in ('A', 'B'):
            _, block = self.volume.UpdateOrAddChildByAttrib(BlockNode.Create(name), 'Name')
            os.makedirs(block.FullPath, exist_ok=True)
        VolumeManager.Save(self.volume)
        reloaded = VolumeManager.Load(self.root, Create=False)
        assert reloaded is not None
        self.volume = cast(XContainerElementWrapper, reloaded)

    def tearDown(self):
        shutil.rmtree(os.path.dirname(self.root), ignore_errors=True)

    def test_parallel_unexpected_error_propagates(self):
        link_nodes = [c for c in list(self.volume) if c.tag.endswith('_Link')]
        self.assertGreaterEqual(len(link_nodes), 2)

        def boom(_path):
            raise RuntimeError('simulated link load failure')

        with mock.patch.object(
                XContainerElementWrapper, '_load_wrap_link_element', side_effect=boom):
            with self.assertRaises(RuntimeError):
                self.volume._replace_links(link_nodes)

        remaining_links = [c for c in list(self.volume) if c.tag.endswith('_Link')]
        self.assertGreaterEqual(
            len(remaining_links), 1,
            msg='unexpected error must not silently drop or replace the stub')


if __name__ == '__main__':
    unittest.main()
