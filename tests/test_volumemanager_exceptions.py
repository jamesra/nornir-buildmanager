"""Unit tests for volumemanager XML exception types."""

from __future__ import annotations

import unittest
from xml.etree import ElementTree

from hypothesis import given, settings
from hypothesis import strategies as st

from nornir_buildmanager.volumemanager.exceptions import (
    DuplicateElementError,
    MissingAttributeError,
    MissingElementError,
)
from nornir_buildmanager.volumemanager.xelementwrapper import XElementWrapper


class TestVolumeManagerExceptions(unittest.TestCase):
    """Construction and message args for volumemanager exceptions."""

    def test_duplicate_element_error_stores_element_and_message(self) -> None:
        element = XElementWrapper('Tile', attrib={'Path': 'tile-1'})
        message = 'duplicate link when saving'
        exc = DuplicateElementError(element, message)
        self.assertIs(exc.element, element)
        self.assertEqual(exc.args, (message,))

    def test_missing_element_error_stores_element_and_message(self) -> None:
        element = XElementWrapper('Section', attrib={'Path': 'sec-a'})
        message = 'element is not a child'
        exc = MissingElementError(element, message)
        self.assertIs(exc.element, element)
        self.assertEqual(exc.args, (message,))

    def test_missing_attribute_error_stores_fields_and_message(self) -> None:
        element = ElementTree.Element('Meta')
        attribute = 'Checksum'
        message = 'required attribute absent'
        exc = MissingAttributeError(element, attribute, message)
        self.assertIs(exc.element, element)
        self.assertEqual(exc._attribute, attribute)
        self.assertEqual(exc.args, (message,))

    @given(message=st.text(min_size=1, max_size=120))
    @settings(max_examples=25, deadline=None)
    def test_duplicate_element_error_message_round_trip_in_args(self, message: str) -> None:
        element = XElementWrapper('Channel', attrib={'Path': 'ch'})
        exc = DuplicateElementError(element, message)
        self.assertEqual(exc.args[0], message)

    @given(
        attribute=st.from_regex(r'[A-Za-z][A-Za-z0-9_]{0,31}', fullmatch=True),
        message=st.text(min_size=1, max_size=80),
    )
    @settings(max_examples=25, deadline=None)
    def test_missing_attribute_error_attribute_round_trip(self, attribute: str, message: str) -> None:
        element = ElementTree.Element('Node')
        exc = MissingAttributeError(element, attribute, message)
        self.assertEqual(exc._attribute, attribute)
        self.assertEqual(exc.args[0], message)


if __name__ == '__main__':
    unittest.main()
