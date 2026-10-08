"""Unit tests for volumemanager element wrapping helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock
from xml.etree import ElementTree

import nornir_buildmanager.volumemanager as vm
from nornir_buildmanager.volumemanager.elementwrapping import (
    SetElementParent,
    WrapElement,
)
from nornir_buildmanager.volumemanager.sectionmappingsnode import SectionMappingsNode
from nornir_buildmanager.volumemanager.xelementwrapper import XElementWrapper


class TestWrapElement(unittest.TestCase):
    def test_rejects_link_tag_before_load(self) -> None:
        link = ElementTree.Element('Volume_Link')
        with self.assertRaises(AssertionError):
            WrapElement(link)

    def test_uses_node_override_class_for_known_tag(self) -> None:
        raw = ElementTree.Element('SectionMappings', attrib={'MappedSectionNumber': '1'})
        wrapped, element = WrapElement(raw)
        self.assertTrue(wrapped)
        self.assertIsInstance(element, SectionMappingsNode)

    def test_returns_same_instance_when_already_wrapped(self) -> None:
        existing = XElementWrapper('Generic', attrib={'Name': 'x'})
        wrapped, element = WrapElement(existing)
        self.assertFalse(wrapped)
        self.assertIs(element, existing)

    def test_path_attrib_selects_file_vs_container_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            existing_file = os.path.join(tmp, 'tile.png')
            open(existing_file, 'wb').close()
            missing_path = os.path.join(tmp, 'missing', 'VolumeData.xml')

            file_raw = ElementTree.Element('Data', attrib={'Path': existing_file})
            _, file_wrapped = WrapElement(file_raw)
            self.assertIsInstance(file_wrapped, vm.XFileElementWrapper)

            container_raw = ElementTree.Element('Level', attrib={'Path': missing_path})
            _, container_wrapped = WrapElement(container_raw)
            self.assertIsInstance(container_wrapped, vm.XContainerElementWrapper)

    def test_no_path_uses_base_element_wrapper(self) -> None:
        raw = ElementTree.Element('Notes')
        _, element = WrapElement(raw)
        self.assertIsInstance(element, XElementWrapper)
        self.assertNotIsInstance(element, vm.XFileElementWrapper)
        self.assertNotIsInstance(element, vm.XContainerElementWrapper)


class TestSetElementParent(unittest.TestCase):
    def test_strips_deprecated_child_nodes(self) -> None:
        parent = XElementWrapper('Parent')
        super(XElementWrapper, parent).append(ElementTree.Element('PruneData'))
        super(XElementWrapper, parent).append(ElementTree.Element('HistogramData'))
        parent.append(XElementWrapper('KeepMe'))

        SetElementParent(parent, None)

        tags = [child.tag for child in parent]
        self.assertEqual(tags, ['KeepMe'])

    def test_calls_on_parent_changed_for_wrapped_children(self) -> None:
        parent = XElementWrapper('Parent')
        child = XElementWrapper('Child')
        parent.append(child)

        with mock.patch.object(XElementWrapper, 'OnParentChanged', autospec=True) as on_changed:
            SetElementParent(parent, None)
            child_calls = [c for c in on_changed.call_args_list if c[0][0] is child]
            self.assertGreaterEqual(len(child_calls), 1)

    def test_empty_element_only_sets_parent(self) -> None:
        element = XElementWrapper('Leaf')
        with mock.patch.object(element, 'SetParentNoChangeFlag') as set_parent:
            SetElementParent(element, None)
            set_parent.assert_called_once_with(None)


if __name__ == '__main__':
    unittest.main()
