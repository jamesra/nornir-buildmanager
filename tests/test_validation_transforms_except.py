"""Bare ``except:`` around ``float(...)`` must not swallow ``KeyboardInterrupt`` (#136).

Both conversion sites intended ``except ValueError`` (see the comments). A bare
``except:`` also catches ``KeyboardInterrupt`` and ``SystemExit``, so Ctrl-C during
attribute matching would be reported as a non-numeric string instead of stopping.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from nornir_buildmanager.validation import transforms

# Double-underscore names are mangled inside class bodies; bind once at module scope.
_get_attrib_or_default = transforms.__dict__['__GetAttribOrDefault']


class TestFloatConversionDoesNotSwallowInterrupts(unittest.TestCase):

    def test_keyboard_interrupt_from_float_propagates_in_getattr(self):
        node = SimpleNamespace(Threshold='1.5')
        with mock.patch('builtins.float', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                _get_attrib_or_default(node, 'Threshold', None)

    def test_keyboard_interrupt_from_float_propagates_in_is_value_matched(self):
        # Patch only the conversion helper so isinstance(..., float) still sees the real type.
        node = SimpleNamespace(Threshold='1.5')
        with mock.patch.object(
            transforms,
            '__GetAttribOrDefault',
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                transforms.IsValueMatched(node, 'Threshold', '2.0')

    def test_value_error_and_type_error_leave_string(self):
        node = SimpleNamespace(Name='Grid')
        self.assertEqual('Grid', _get_attrib_or_default(node, 'Name', None))
        self.assertTrue(transforms.IsValueMatched(node, 'Name', 'Grid'))
        self.assertFalse(transforms.IsValueMatched(node, 'Name', 'Stage'))

        node_bad = SimpleNamespace(Tag=object())
        # Non-string attribute is returned as-is (no float conversion).
        self.assertIs(node_bad.Tag, _get_attrib_or_default(node_bad, 'Tag', None))

    def test_numeric_strings_still_compare_as_floats(self):
        node = SimpleNamespace(Threshold='1.50')
        self.assertEqual(1.5, _get_attrib_or_default(node, 'Threshold', None))
        self.assertTrue(transforms.IsValueMatched(node, 'Threshold', '1.5'))
        self.assertTrue(transforms.IsValueMatched(node, 'Threshold', 1.5))

    def test_type_error_from_float_is_caught_not_bare_base_exception(self):
        """TypeError during conversion stays on the string path; BaseException must not."""
        node = SimpleNamespace(Threshold='1.5')
        with mock.patch('builtins.float', side_effect=TypeError('boom')):
            self.assertEqual('1.5', _get_attrib_or_default(node, 'Threshold', None))


if __name__ == '__main__':
    unittest.main()
