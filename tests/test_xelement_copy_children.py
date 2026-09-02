"""Regression for #175: XElementWrapper.Copy must deep-copy children."""
from __future__ import annotations

import unittest

from nornir_buildmanager.volumemanager.xelementwrapper import XElementWrapper


class TestXElementWrapperCopyChildren(unittest.TestCase):
    def test_copy_includes_nested_children(self) -> None:
        """#175: Copy previously dropped children and only constructed a Warning."""
        parent = XElementWrapper('Parent', attrib={'Name': 'p'})
        child = XElementWrapper('Child', attrib={'Name': 'c'})
        grand = XElementWrapper('Grand', attrib={'Name': 'g'})
        child.append(grand)
        parent.append(child)

        copied = parent.Copy()

        self.assertEqual(len(copied), 1)
        copied_child = copied[0]
        self.assertIsInstance(copied_child, XElementWrapper)
        self.assertIsNot(copied_child, child)
        self.assertEqual(copied_child.attrib['Name'], 'c')
        self.assertEqual(len(copied_child), 1)
        self.assertEqual(copied_child[0].attrib['Name'], 'g')
        self.assertIsNot(copied_child[0], grand)

        copied_child.attrib['Name'] = 'mutated'
        self.assertEqual(child.attrib['Name'], 'c')
        self.assertIs(copied_child.Parent, copied)
        self.assertIsNone(copied.Parent)

    def test_copy_without_children_still_works(self) -> None:
        leaf = XElementWrapper('Leaf', attrib={'Path': 'a.xml'})
        leaf.text = 'note'
        copied = leaf.Copy()
        self.assertEqual(len(copied), 0)
        self.assertEqual(copied.attrib['Path'], 'a.xml')
        self.assertEqual(copied.text, 'note')
        self.assertIsNot(copied.attrib, leaf.attrib)


if __name__ == '__main__':
    unittest.main()
