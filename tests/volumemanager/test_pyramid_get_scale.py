"""Tests for PyramidLevelHandler.GetScale ancestor walk.

The walk must terminate. Re-reading ``self.Parent`` each iteration instead of
advancing to ``Parent.Parent`` hangs the build whenever no ancestor exposes
``Scale``, which is a stall rather than an exception and so is invisible to a
test that only checks the happy path.
"""

from __future__ import annotations

import unittest

from nornir_buildmanager.volumemanager.pyramidlevelhandler import PyramidLevelHandler


class _Ancestor:
    """Minimal stand-in for an XML wrapper node with a Parent link."""

    Parent: object | None

    def __init__(self, parent: object | None = None) -> None:
        self.Parent = parent


class _ScaledAncestor(_Ancestor):
    """Ancestor that exposes Scale, like ChannelNode."""

    def __init__(self, scale: float, parent: object | None = None) -> None:
        super().__init__(parent)
        self.Scale = scale


class _Pyramid(PyramidLevelHandler):
    """PyramidLevelHandler is a mixin; GetScale only needs a Parent chain."""

    Parent: object | None

    def __init__(self, parent: object | None) -> None:
        self.Parent = parent


class TestGetScale(unittest.TestCase):

    def test_returns_scale_from_immediate_parent(self):
        pyramid = _Pyramid(_ScaledAncestor(4.5))
        self.assertEqual(pyramid.GetScale(), 4.5)

    def test_walks_up_to_a_distant_ancestor(self):
        root = _ScaledAncestor(2.25)
        pyramid = _Pyramid(_Ancestor(_Ancestor(root)))
        self.assertEqual(pyramid.GetScale(), 2.25)

    def test_returns_none_when_no_ancestor_has_scale(self):
        """Without advancing the walk variable this spins forever."""
        pyramid = _Pyramid(_Ancestor(_Ancestor(_Ancestor(None))))
        self.assertIsNone(pyramid.GetScale())

    def test_returns_none_when_detached(self):
        self.assertIsNone(_Pyramid(None).GetScale())

    def test_stops_at_the_nearest_scaled_ancestor(self):
        far = _ScaledAncestor(10.0)
        near = _ScaledAncestor(1.0, parent=far)
        pyramid = _Pyramid(near)
        self.assertEqual(pyramid.GetScale(), 1.0)


if __name__ == '__main__':
    unittest.main()
