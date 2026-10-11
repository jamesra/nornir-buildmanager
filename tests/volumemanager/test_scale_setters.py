"""Characterization tests for the Scale X/Y/Z setters and Scale.__init__.

Each axis setter accepts ``None``, a ``float`` (wrapped as nanometres), or a
``ScaleAxis`` (stored as is), and raises ``NotImplementedError`` for anything
else, including ``int``.
"""

from __future__ import annotations

import unittest

import numpy as np
from hypothesis import example, given
from hypothesis import strategies as st
from nornir_buildmanager.volumemanager import Scale, ScaleAxis

_AXES: tuple[str, ...] = ('X', 'Y', 'Z')

_finite_floats = st.floats(allow_nan=False, allow_infinity=False, min_value=-1e12, max_value=1e12)
_units = st.sampled_from(['nm', 'um', 'mm', 'px'])


def _blank_scale() -> Scale:
    """A Scale whose three axes start as None."""
    return Scale(None)


class TestScaleAxisSetters(unittest.TestCase):
    """Every axis setter follows the same None / float / ScaleAxis / reject rule."""

    def test_none_clears_axis(self):
        for axis in _AXES:
            with self.subTest(axis=axis):
                scale = Scale(1.0, 2.0, 3.0)
                setattr(scale, axis, None)
                self.assertIsNone(getattr(scale, axis))

    @given(value=_finite_floats)
    @example(value=0.0)
    @example(value=2.176)
    def test_float_becomes_nanometre_axis(self, value: float):
        for axis in _AXES:
            scale = _blank_scale()
            setattr(scale, axis, value)
            got = getattr(scale, axis)
            self.assertIsInstance(got, ScaleAxis)
            self.assertEqual(got.UnitsPerPixel, value)
            self.assertEqual(got.UnitsOfMeasure, 'nm')

    def test_numpy_float64_is_accepted_as_float(self):
        for axis in _AXES:
            with self.subTest(axis=axis):
                scale = _blank_scale()
                setattr(scale, axis, np.float64(4.5))
                got = getattr(scale, axis)
                self.assertEqual(got.UnitsPerPixel, 4.5)
                self.assertEqual(got.UnitsOfMeasure, 'nm')

    @given(value=_finite_floats, units=_units)
    def test_scale_axis_stored_by_identity(self, value: float, units: str):
        for axis in _AXES:
            scale = _blank_scale()
            scale_axis = ScaleAxis(value, units)
            setattr(scale, axis, scale_axis)
            self.assertIs(getattr(scale, axis), scale_axis)
            self.assertEqual(getattr(scale, axis).UnitsOfMeasure, units)

    def test_other_types_raise_with_message(self):
        for axis in _AXES:
            for bad in (3, '3.0', [1.0], np.int64(2)):
                with self.subTest(axis=axis, bad=bad):
                    scale = Scale(1.0, 2.0, 3.0)
                    before = getattr(scale, axis)
                    with self.assertRaises(NotImplementedError) as ctx:
                        setattr(scale, axis, bad)
                    self.assertEqual(str(ctx.exception), f'Unknown type passed to Scale setter {bad}')
                    self.assertIs(getattr(scale, axis), before)

    def test_setting_one_axis_leaves_others_alone(self):
        for axis in _AXES:
            with self.subTest(axis=axis):
                scale = Scale(1.0, 2.0, 3.0)
                setattr(scale, axis, 9.0)
                expected = {'X': 1.0, 'Y': 2.0, 'Z': 3.0, axis: 9.0}
                for other in _AXES:
                    self.assertEqual(getattr(scale, other).UnitsPerPixel, expected[other])


class TestScaleInit(unittest.TestCase):
    """Scale.__init__ routes its arguments through the axis setters."""

    @given(value=_finite_floats)
    def test_single_value_sets_x_and_y_not_z(self, value: float):
        scale = Scale(value)
        assert scale.X is not None and scale.Y is not None
        self.assertEqual(scale.X.UnitsPerPixel, value)
        self.assertEqual(scale.Y.UnitsPerPixel, value)
        self.assertEqual(scale.X.UnitsOfMeasure, 'nm')
        self.assertIsNone(scale.Z)

    def test_three_values(self):
        scale = Scale(1.0, 2.0, 3.0)
        assert scale.X is not None and scale.Y is not None and scale.Z is not None
        self.assertEqual((scale.X.UnitsPerPixel, scale.Y.UnitsPerPixel, scale.Z.UnitsPerPixel), (1.0, 2.0, 3.0))

    def test_scale_axis_arguments_kept(self):
        x, y, z = ScaleAxis(1.0, 'um'), ScaleAxis(2.0, 'um'), ScaleAxis(3.0, 'um')
        scale = Scale(x, y, z)
        self.assertIs(scale.X, x)
        self.assertIs(scale.Y, y)
        self.assertIs(scale.Z, z)

    def test_single_scale_axis_shared_by_x_and_y(self):
        x = ScaleAxis(4.0, 'nm')
        scale = Scale(x)
        self.assertIs(scale.X, x)
        self.assertIs(scale.Y, x)

    def test_all_none(self):
        scale = Scale(None)
        self.assertIsNone(scale.X)
        self.assertIsNone(scale.Y)
        self.assertIsNone(scale.Z)

    def test_int_argument_raises(self):
        with self.assertRaises(NotImplementedError) as ctx:
            Scale(4)
        self.assertEqual(str(ctx.exception), 'Unknown type passed to Scale setter 4')

    def test_int_z_raises(self):
        with self.assertRaises(NotImplementedError):
            Scale(1.0, 2.0, 3)


if __name__ == '__main__':
    unittest.main()
