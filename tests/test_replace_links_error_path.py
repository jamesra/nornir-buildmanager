"""Parallel link-load error messages must name the failing child path (#138).

Previously the loop rebound ``fullpath``, so every except-handler message used
the last path in the batch. Fixed by stashing ``t.fullpath = sub_container_path``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from typing import cast
from unittest import mock

from nornir_buildmanager.volumemanager import BlockNode, VolumeManager, XContainerElementWrapper


class TestReplaceLinksErrorPathIsPerTask(unittest.TestCase):

    def setUp(self):
        self.root = os.path.join(tempfile.mkdtemp(prefix='nornir_links_path_'), 'vol')
        os.makedirs(self.root, exist_ok=True)
        volume = VolumeManager.Load(self.root, Create=True)
        assert volume is not None
        self.volume = cast(XContainerElementWrapper, volume)
        for name in ('Alpha', 'Beta', 'Gamma'):
            _, block = self.volume.UpdateOrAddChildByAttrib(BlockNode.Create(name), 'Name')
            os.makedirs(block.FullPath, exist_ok=True)
        VolumeManager.Save(self.volume)
        reloaded = VolumeManager.Load(self.root, Create=False)
        assert reloaded is not None
        self.volume = cast(XContainerElementWrapper, reloaded)

    def tearDown(self):
        shutil.rmtree(os.path.dirname(self.root), ignore_errors=True)

    def test_ioerror_message_names_the_failing_child_not_the_last(self):
        link_nodes = [c for c in list(self.volume) if c.tag.endswith('_Link')]
        self.assertEqual(3, len(link_nodes))
        paths = [os.path.join(self.volume.FullPath, n.attrib['Path']) for n in link_nodes]
        fail_path = os.path.abspath(paths[0])
        last_path = os.path.abspath(paths[-1])
        self.assertNotEqual(fail_path, last_path)

        real = XContainerElementWrapper._load_wrap_link_element

        def selective(path):
            if os.path.normcase(os.path.abspath(path)) == os.path.normcase(fail_path):
                raise IOError('missing')
            return real(path)

        messages: list[str] = []

        with mock.patch.object(XContainerElementWrapper, '_load_wrap_link_element', side_effect=selective):
            with mock.patch('nornir_shared.prettyoutput.LogErr', side_effect=lambda m: messages.append(str(m))):
                self.volume._replace_links(link_nodes)

        joined = '\n'.join(messages)
        self.assertIn(fail_path, joined)
        self.assertNotIn(
            f'Removing link node after IOError loading linked XML file: {last_path}',
            joined)


if __name__ == '__main__':
    unittest.main()
