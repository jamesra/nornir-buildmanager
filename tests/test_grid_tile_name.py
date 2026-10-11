"""Unit tests for templates.Current.GridTileName, the grid tile file-name rule."""

from __future__ import annotations

import unittest

from hypothesis import example, given
from hypothesis import strategies as st

from nornir_buildmanager.templates import Current as Templates

_names = st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="%"), max_size=12)
_coords = st.integers(min_value=-5000, max_value=100_000)


def _reference_name(prefix: str, x: int, y: int, postfix: str) -> str:
    """Independent spelling of the on-disk grid tile name (three-digit zero pad)."""
    return f"{prefix}X{x:03d}_Y{y:03d}{postfix}"


class TestGridTileName(unittest.TestCase):

    def test_known_names(self) -> None:
        """Pinned names that existing tile pyramids on disk use."""
        self.assertEqual(Templates.GridTileName("", 0, 0, ".png"), "X000_Y000.png")
        self.assertEqual(Templates.GridTileName("p_", 7, 12, ".png"), "p_X007_Y012.png")
        self.assertEqual(Templates.GridTileName("", 1000, 12345, ""), "X1000_Y12345")
        self.assertEqual(Templates.GridTileName("a", 3, 4, "_mask.png"), "aX003_Y004_mask.png")

    def test_x_is_column_and_y_is_row(self) -> None:
        """X and Y are not swapped."""
        self.assertEqual(Templates.GridTileName("", 1, 2, ""), "X001_Y002")

    @given(prefix=_names, x=_coords, y=_coords, postfix=_names)
    @example(prefix="", x=999, y=1000, postfix=".png")
    @example(prefix="", x=-1, y=-42, postfix="")
    def test_matches_template_and_reference(self, prefix: str, x: int, y: int, postfix: str) -> None:
        """Helper equals the raw template expression and the independent reference."""
        expected = Templates.GridTileNameTemplate % {'prefix': prefix, 'X': x, 'Y': y, 'postfix': postfix}
        actual = Templates.GridTileName(prefix, x, y, postfix)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, _reference_name(prefix, x, y, postfix))


if __name__ == "__main__":
    unittest.main()
