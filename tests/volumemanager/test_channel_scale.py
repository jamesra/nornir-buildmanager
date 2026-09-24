"""Tests for ChannelNode.Scale reading its value back from the XML.

The lazy-load guard used ``hasattr(self, '_scale')``, but ``__init__`` always
assigns ``_scale``, so the guard could never fire and every channel loaded from
VolumeData.xml reported no scale until something called SetScale in the same
process.
"""

from __future__ import annotations

import unittest

from nornir_buildmanager.volumemanager import ChannelNode, Scale, ScaleAxis, ScaleNode, XElementWrapper


def _scale_node(units_per_pixel: float, units: str = 'nm') -> ScaleNode:
    node = ScaleNode.Create()
    for axis in ('X', 'Y'):
        node.UpdateOrAddChild(XElementWrapper(axis, {'UnitsOfMeasure': units,
                                                    'UnitsPerPixel': str(units_per_pixel)}))
    return node


class TestChannelScaleFromXml(unittest.TestCase):
    """A Scale child present in the XML must be visible through the property."""

    def setUp(self) -> None:
        self.channel = ChannelNode.Create('TEM')

    def test_scale_child_is_read_on_first_access(self):
        """Appending the child directly mimics loading it from VolumeData.xml."""
        self.channel.append(_scale_node(4.5))

        scale = self.channel.Scale

        self.assertIsNotNone(scale)
        assert scale is not None and scale.X is not None and scale.Y is not None
        self.assertAlmostEqual(scale.X.UnitsPerPixel, 4.5)
        self.assertAlmostEqual(scale.Y.UnitsPerPixel, 4.5)
        self.assertEqual(scale.X.UnitsOfMeasure, 'nm')

    def test_get_scale_matches_property(self):
        self.channel.append(_scale_node(2.176))

        got = self.channel.GetScale()

        self.assertIsNotNone(got)
        assert got is not None and got.X is not None
        self.assertAlmostEqual(got.X.UnitsPerPixel, 2.176)

    def test_channel_without_scale_child_returns_none(self):
        self.assertIsNone(self.channel.Scale)

    def test_missing_scale_is_not_rescanned(self):
        """The absent case must cache too, or every access rescans the tree."""
        self.assertIsNone(self.channel.Scale)

        find_calls = {'count': 0}
        original_find = type(self.channel).find

        def counting_find(self_node, *args, **kwargs):
            find_calls['count'] += 1
            return original_find(self_node, *args, **kwargs)

        type(self.channel).find = counting_find  # type: ignore[method-assign]
        try:
            self.assertIsNone(self.channel.Scale)
            self.assertIsNone(self.channel.Scale)
        finally:
            type(self.channel).find = original_find  # type: ignore[method-assign]

        self.assertEqual(find_calls['count'], 0)


class TestChannelScaleCacheInvalidation(unittest.TestCase):
    """SetScale replaces the node, so the cache must follow it."""

    def setUp(self) -> None:
        self.channel = ChannelNode.Create('TEM')

    def test_set_scale_is_visible_immediately(self):
        self.channel.SetScale(8.0)

        scale = self.channel.Scale
        self.assertIsNotNone(scale)
        assert scale is not None and scale.X is not None
        self.assertAlmostEqual(scale.X.UnitsPerPixel, 8.0)

    def test_set_scale_overwrites_value_read_from_xml(self):
        self.channel.append(_scale_node(4.5))
        self.assertIsNotNone(self.channel.Scale)

        self.channel.SetScale(1.5)

        scale = self.channel.Scale
        assert scale is not None and scale.X is not None
        self.assertAlmostEqual(scale.X.UnitsPerPixel, 1.5)

    def test_removing_the_node_clears_the_cache(self):
        self.channel.SetScale(4.5)
        self.assertIsNotNone(self.channel.Scale)

        self.channel._try_remove_scale_node()

        self.assertIsNone(self.channel.Scale)

    def test_set_scale_from_scale_object(self):
        self.channel.SetScale(Scale(ScaleAxis(3.0, 'nm'), ScaleAxis(3.0, 'nm')))

        scale = self.channel.Scale
        assert scale is not None and scale.X is not None
        self.assertAlmostEqual(scale.X.UnitsPerPixel, 3.0)


if __name__ == '__main__':
    unittest.main()
