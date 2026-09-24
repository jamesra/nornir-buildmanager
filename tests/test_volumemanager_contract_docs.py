"""Pin the volumemanager dirty/save + create-on-read contract (#173)."""
from __future__ import annotations

import unittest

import nornir_buildmanager.volumemanager as volumemanager
from nornir_buildmanager.volumemanager.xelementwrapper import XElementWrapper


class TestVolumeManagerContractDocs(unittest.TestCase):
    def test_package_documents_dirty_and_create_on_read(self) -> None:
        doc = volumemanager.__doc__ or ''
        self.assertIn('SaveAsLinkedElement', doc)
        self.assertIn('_AttributesChanged', doc)
        self.assertIn('HasImageset', doc)
        self.assertIn('create', doc.lower())

    def test_xelementwrapper_documents_dirty_flags(self) -> None:
        doc = XElementWrapper.__doc__ or ''
        # Class body comment block is not __doc__; AttributesChanged property is.
        prop_doc = XElementWrapper.AttributesChanged.__doc__ or ''
        self.assertIn('ResetElementChangeFlags', prop_doc)
        children_doc = XElementWrapper.ChildrenChanged.__doc__ or ''
        self.assertIn('Linked children', children_doc)


if __name__ == '__main__':
    unittest.main()
