"""``XElementWrapper.Contains`` must unpack ``attrib.items()`` and use ``Element`` (#134).

Iterating a dict yields keys, so ``for k, v in c.attrib`` raises ``ValueError`` for any
attribute name whose length is not 2. The method also ignored its ``Element`` parameter
and compared children against ``self.attrib`` (the parent).
"""

from __future__ import annotations

import unittest

from nornir_buildmanager.volumemanager.xelementwrapper import XElementWrapper


class TestContainsUsesElementAttribItems(unittest.TestCase):

    def test_attrib_items_unpack_no_longer_raises(self):
        root = XElementWrapper('Root', attrib={'Name': 'Vol'})
        root.append(XElementWrapper('Section', attrib={'Name': 'S1', 'Number': '1'}))
        probe = XElementWrapper('Section', attrib={'Name': 'S1', 'Number': '1'})
        # Old body raised ValueError: too many values to unpack (expected 2).
        self.assertTrue(root.Contains(probe))

    def test_matching_child_ignores_creation_date(self):
        root = XElementWrapper('Root', attrib={'Name': 'Vol'})
        child = XElementWrapper('Section', attrib={'Name': 'S1', 'Number': '1'})
        root.append(child)
        probe = XElementWrapper('Section', attrib={'Name': 'S1', 'Number': '1'})
        probe.attrib['CreationDate'] = '1999-01-01 00:00:00+00:00'
        self.assertNotEqual(child.attrib.get('CreationDate'), probe.attrib.get('CreationDate'))
        self.assertTrue(root.Contains(probe))

    def test_non_matching_attribs_are_not_contained(self):
        root = XElementWrapper('Root', attrib={'Name': 'Vol'})
        root.append(XElementWrapper('Section', attrib={'Name': 'S1', 'Number': '1'}))
        probe = XElementWrapper('Section', attrib={'Name': 'S1', 'Number': '2'})
        self.assertFalse(root.Contains(probe))

    def test_wrong_tag_is_not_contained(self):
        root = XElementWrapper('Root', attrib={'Name': 'Vol'})
        root.append(XElementWrapper('Section', attrib={'Name': 'S1'}))
        probe = XElementWrapper('Channel', attrib={'Name': 'S1'})
        self.assertFalse(root.Contains(probe))

    def test_two_char_attrib_name_is_compared_as_a_key(self):
        """A length-2 key used to unpack into two characters without raising."""
        root = XElementWrapper('Root', attrib={'Name': 'Vol'})
        root.append(XElementWrapper('Section', attrib={'ID': '7', 'Name': 'S1'}))
        probe = XElementWrapper('Section', attrib={'ID': '7', 'Name': 'S1'})
        self.assertTrue(root.Contains(probe))
        probe_miss = XElementWrapper('Section', attrib={'ID': '8', 'Name': 'S1'})
        self.assertFalse(root.Contains(probe_miss))


if __name__ == '__main__':
    unittest.main()
