"""Characterization tests for segmentationtraining geometry.window_tile_rect (overlapping tile windows)."""

from __future__ import annotations

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from nornir_buildmanager.operations.segmentationtraining.geometry import (
    TileRect,
    window_tile_rect,
)


def _covered(windows: list[TileRect]) -> set[tuple[int, int]]:
    return {(x, y) for w in windows for x in range(w.ix0, w.ix1) for y in range(w.iy0, w.iy1)}


def _all_tiles(rect: TileRect) -> set[tuple[int, int]]:
    return {(x, y) for x in range(rect.ix0, rect.ix1) for y in range(rect.iy0, rect.iy1)}


def test_rect_that_fits_is_returned_unsplit() -> None:
    rect = TileRect(3, 7, 2, 5)
    assert window_tile_rect(rect, max_tiles_x=4, max_tiles_y=3) == [rect]
    assert window_tile_rect(rect, max_tiles_x=10, max_tiles_y=10, overlap_tiles=5) == [rect]


def test_2d_grid_exact_windows_with_overlap() -> None:
    rect = TileRect(0, 10, 0, 6)
    windows = window_tile_rect(rect, max_tiles_x=4, max_tiles_y=4, overlap_tiles=1)
    # step 3: x starts 0,3,6 (x1 = 4,7,10 stops at rect.ix1); y starts 0,3 (y1 = 4,6)
    assert windows == [
        TileRect(0, 4, 0, 4),
        TileRect(0, 4, 3, 6),
        TileRect(3, 7, 0, 4),
        TileRect(3, 7, 3, 6),
        TileRect(6, 10, 0, 4),
        TileRect(6, 10, 3, 6),
    ]


def test_2d_grid_emits_no_redundant_trailing_window() -> None:
    # The y window starting at 6 already reaches iy1=10; a further start at 9 would be fully redundant.
    windows = window_tile_rect(TileRect(0, 5, 0, 10), max_tiles_x=4, max_tiles_y=4, overlap_tiles=1)
    assert [(w.iy0, w.iy1) for w in windows if w.ix0 == 0] == [(0, 4), (3, 7), (6, 10)]


def test_2d_grid_last_window_is_clipped_to_rect_end() -> None:
    windows = window_tile_rect(TileRect(0, 9, 0, 9), max_tiles_x=5, max_tiles_y=5, overlap_tiles=2)
    assert windows[-1] == TileRect(6, 9, 6, 9)
    assert max(w.ix1 for w in windows) == 9
    assert max(w.iy1 for w in windows) == 9


def test_x_only_split_keeps_full_height() -> None:
    rect = TileRect(0, 10, 0, 2)
    windows = window_tile_rect(rect, max_tiles_x=4, max_tiles_y=4, overlap_tiles=1)
    assert windows == [TileRect(0, 4, 0, 2), TileRect(3, 7, 0, 2), TileRect(6, 10, 0, 2)]


def test_y_only_split_keeps_full_width() -> None:
    rect = TileRect(0, 2, 0, 10)
    windows = window_tile_rect(rect, max_tiles_x=4, max_tiles_y=4, overlap_tiles=1)
    assert windows == [TileRect(0, 2, 0, 4), TileRect(0, 2, 3, 7), TileRect(0, 2, 6, 10)]


def test_non_zero_origin_is_respected() -> None:
    rect = TileRect(100, 107, 50, 52)
    windows = window_tile_rect(rect, max_tiles_x=3, max_tiles_y=3, overlap_tiles=1)
    assert windows == [TileRect(100, 103, 50, 52), TileRect(102, 105, 50, 52), TileRect(104, 107, 50, 52)]


def test_zero_overlap_tiles_windows_abut_exactly() -> None:
    windows = window_tile_rect(TileRect(0, 8, 0, 1), max_tiles_x=4, max_tiles_y=4, overlap_tiles=0)
    assert windows == [TileRect(0, 4, 0, 1), TileRect(4, 8, 0, 1)]


def test_overlap_at_or_above_max_clamps_step_to_one() -> None:
    expected = [TileRect(0, 2, 0, 1), TileRect(1, 3, 0, 1), TileRect(2, 4, 0, 1)]
    assert window_tile_rect(TileRect(0, 4, 0, 1), max_tiles_x=2, max_tiles_y=2, overlap_tiles=2) == expected
    assert window_tile_rect(TileRect(0, 4, 0, 1), max_tiles_x=2, max_tiles_y=2, overlap_tiles=9) == expected


def test_negative_overlap_behaves_like_zero() -> None:
    rect = TileRect(0, 8, 0, 1)
    assert window_tile_rect(rect, max_tiles_x=4, max_tiles_y=4, overlap_tiles=-3) == window_tile_rect(
        rect, max_tiles_x=4, max_tiles_y=4, overlap_tiles=0
    )


def test_non_positive_max_tiles_clamp_to_one() -> None:
    rect = TileRect(0, 3, 0, 1)
    expected = [TileRect(0, 1, 0, 1), TileRect(1, 2, 0, 1), TileRect(2, 3, 0, 1)]
    assert window_tile_rect(rect, max_tiles_x=0, max_tiles_y=0, overlap_tiles=0) == expected
    assert window_tile_rect(rect, max_tiles_x=-5, max_tiles_y=-5, overlap_tiles=0) == expected


@pytest.mark.xfail(
    strict=True,
    reason="1-D branch picks the axis by n_x >= n_y, not by which axis exceeds its max; "
    "a rect only too wide (n_x > max_x) but taller than wide (n_y > n_x) is returned as one oversized window",
)
def test_too_wide_but_taller_rect_is_split_in_x() -> None:
    windows = window_tile_rect(TileRect(0, 3, 0, 8), max_tiles_x=2, max_tiles_y=10, overlap_tiles=0)
    assert all(w.n_x <= 2 for w in windows)


_dims = st.integers(min_value=1, max_value=40)


@given(
    ix0=st.integers(-50, 50),
    iy0=st.integers(-50, 50),
    n_x=_dims,
    n_y=_dims,
    max_x=st.integers(-2, 12),
    max_y=st.integers(-2, 12),
    overlap=st.integers(-2, 14),
)
@example(ix0=0, iy0=0, n_x=10, n_y=6, max_x=4, max_y=4, overlap=1)
def test_windows_cover_rect_and_stay_inside_it(
    ix0: int, iy0: int, n_x: int, n_y: int, max_x: int, max_y: int, overlap: int
) -> None:
    rect = TileRect(ix0, ix0 + n_x, iy0, iy0 + n_y)
    windows = window_tile_rect(rect, max_tiles_x=max_x, max_tiles_y=max_y, overlap_tiles=overlap)
    assert windows
    assert all(rect.contains(w) and w.n_x >= 1 and w.n_y >= 1 for w in windows)
    assert _covered(windows) == _all_tiles(rect)


@given(
    n_x=_dims,
    n_y=_dims,
    max_x=st.integers(1, 12),
    max_y=st.integers(1, 12),
    overlap=st.integers(0, 14),
)
def test_windows_respect_max_tiles_when_split_axis_is_the_wider_one(
    n_x: int, n_y: int, max_x: int, max_y: int, overlap: int
) -> None:
    # Excludes the known 1-D axis-selection quirk (see the xfail test): only too-wide-and-wider or both-too-big.
    too_x, too_y = n_x > max_x, n_y > max_y
    if too_x != too_y and (too_x and n_x < n_y or too_y and n_y <= n_x):
        return
    windows = window_tile_rect(TileRect(0, n_x, 0, n_y), max_tiles_x=max_x, max_tiles_y=max_y, overlap_tiles=overlap)
    assert all(w.n_x <= max_x and w.n_y <= max_y for w in windows)
