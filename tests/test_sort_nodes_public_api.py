"""Regression for #198: VolumeManager.__SortNodes__ without element._children."""
from __future__ import annotations

import unittest
from xml.etree import ElementTree as ET

import nornir_buildmanager.volumemanager as vm
from nornir_buildmanager.volumemanager.volumemanager import VolumeManager


class TestSortNodesPublicApi(unittest.TestCase):
    def test_sort_nodes_on_wrapped_volume(self) -> None:
        """#198: must not AttributeError on C ElementTree (no _children)."""
        root = ET.Element('Volume', {'Path': '.', 'Name': 'v'})
        for index, name in enumerate(('c', 'a', 'b')):
            ET.SubElement(
                root, 'Section',
                {'Name': name, 'Path': name, 'Number': str(index)})
        _wrapped, root = vm.WrapElement(root)

        VolumeManager.__SortNodes__(root)

        names = [child.get('Name') for child in root]
        self.assertEqual(names, ['b', 'a', 'c'])
        self.assertFalse(hasattr(root, '_children'))


if __name__ == '__main__':
    unittest.main()
