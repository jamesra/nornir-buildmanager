"""Regression for #201: NaN StosGroup Downsample must not silently force rescale."""
from __future__ import annotations

import unittest
from unittest import mock
from unittest.mock import MagicMock

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations import block


class TestRequireFiniteDownsample(unittest.TestCase):
    def test_nan_raises(self) -> None:
        """#201: XML default NaN must not compare equal / drive ChangeTransformPixelSpacing."""
        with self.assertRaises(NornirUserException) as ctx:
            block._require_finite_downsample(float('NaN'), source="StosGroup 'Grid'")
        self.assertIn('Downsample', str(ctx.exception))
        self.assertFalse(float('NaN') == 4)

    def test_int_and_float_match(self) -> None:
        self.assertEqual(block._require_finite_downsample(4, source='t'), 4.0)
        self.assertEqual(block._require_finite_downsample(4.0, source='t'), 4.0)
        self.assertTrue(float(4) == block._require_finite_downsample(4.0, source='t'))

    def test_none_raises(self) -> None:
        with self.assertRaises(NornirUserException):
            block._require_finite_downsample(None, source='missing parent')

    def test_generate_stos_file_rejects_nan_group_downsample(self) -> None:
        generate = getattr(block, '__GenerateStosFile')
        transform = MagicMock()
        group = MagicMock()
        group.Name = 'Grid'
        group.Downsample = float('NaN')
        transform.FindParent.return_value = group

        with mock.patch.object(block.stosfile.StosFile, 'Load') as load:
            with self.assertRaises(NornirUserException) as ctx:
                generate(transform, 'out.stos', 4, MagicMock(), MagicMock(), None)
            self.assertIn('Downsample', str(ctx.exception))
            load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
