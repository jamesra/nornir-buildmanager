"""Characterization tests for ``place_mask_windows`` and its helpers in geometry.py."""

from __future__ import annotations

import pytest
from nornir_buildmanager.operations.segmentationtraining.geometry import (
    CropWindow,
    _nearest_center_window,
    _window_mask_area,
    place_mask_windows,
)

Ring = tuple[tuple[float, float], ...]


def _box(x0: float, y0: float, x1: float, y1: float) -> Ring:
    """Closed axis-aligned rectangle ring."""
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))


def _place(
    polygons: list, padded: tuple[float, float, float, float], *, downsample: int = 1, crop: int = 100
) -> list[CropWindow]:
    """Call place_mask_windows with a square crop."""
    return place_mask_windows(polygons, padded, downsample=downsample, crop_width=crop, crop_height=crop)


@pytest.mark.parametrize("width,height", [(0, 100), (100, 0), (-1, 100)])
def test_non_positive_crop_raises(width: int, height: int) -> None:
    with pytest.raises(ValueError):
        place_mask_windows([(_box(0, 0, 10, 10),)], (0, 0, 10, 10), downsample=1, crop_width=width, crop_height=height)


def test_contained_bbox_returns_single_window_nearest_centroid() -> None:
    windows = _place([(_box(10, 10, 30, 30),)], (10, 10, 30, 30))
    assert len(windows) == 1
    # Lattice step is 50; candidates containing [10,30] have origins -50..0 per axis.
    # Centroid (20, 20): origin 0 has center 50, origin -50 has center 0; both are 30 away,
    # the tie goes to the smaller origin.
    assert (windows[0].origin_x, windows[0].origin_y) == (-50, -50)
    assert (windows[0].width, windows[0].height) == (100, 100)


def test_contained_bbox_prefers_closer_center() -> None:
    windows = _place([(_box(40, 40, 60, 60),)], (40, 40, 60, 60))
    assert len(windows) == 1
    assert (windows[0].origin_x, windows[0].origin_y) == (0, 0)


def test_contained_downsample_scales_padded_bbox() -> None:
    # Mosaic bbox 80..120 at downsample 2 is pixel 40..60, same as the previous case.
    windows = _place([(_box(80, 80, 120, 120),)], (80, 80, 120, 120), downsample=2)
    assert [(w.origin_x, w.origin_y) for w in windows] == [(0, 0)]


def test_contained_centroid_falls_back_to_padded_center_when_mask_area_zero() -> None:
    # Degenerate mask (no area): the padded bbox center (50, 50) decides.
    windows = _place([], (40, 40, 60, 60))
    assert [(w.origin_x, w.origin_y) for w in windows] == [(0, 0)]
    windows = _place([], (60, 60, 90, 90))
    # Padded center is (75, 75); origin 0 center 50 (25 away), origin 50 center 100 (25 away): tie -> 0.
    assert [(w.origin_x, w.origin_y) for w in windows] == [(0, 0)]


def test_oversize_bbox_uses_maximin_phase_and_sorts_windows_by_origin() -> None:
    windows = _place([(_box(0, 0, 150, 50),)], (0, 0, 150, 50))
    origins = [(w.origin_x, w.origin_y) for w in windows]
    assert origins == sorted(origins)
    assert len(windows) == 2
    for window in windows:
        assert _window_mask_area([(_box(0, 0, 150, 50),)], window, 1) > 0.0
    # Phase 0 gives pieces 100 and 50 wide (min area 2500). Phase 50 gives pieces 50, 100 and 0..;
    # phase 0 has the larger smallest piece and fewer windows, so it is kept.
    assert origins == [(0, 0), (100, 0)]


def test_maximin_prefers_phase_with_larger_smallest_piece() -> None:
    ring = _box(30, 0, 180, 50)
    windows = _place([(ring,)], (30, 0, 180, 50))
    # Phase 0: windows at 0 and 100 -> widths 70 and 80 (min 70).
    # Phase 50: windows at -50, 50, 150 -> widths 20, 100, 30 (min 20). Phase 0 wins.
    assert [w.origin_x for w in windows] == [0, 100]


def test_maximin_symmetric_split_keeps_phase_zero() -> None:
    ring = _box(25, 0, 175, 50)
    windows = _place([(ring,)], (25, 0, 175, 50))
    # Phase 0: windows at 0 and 100 -> widths 75 and 75 (min 75).
    # Phase 50: windows at -50, 50, 150 -> widths 25, 100, 25 (min 25). Phase 0 wins.
    assert [w.origin_x for w in windows] == [0, 100]


def test_no_positive_area_returns_empty_list() -> None:
    # Oversize padded bbox but the mask has no area anywhere (empty polygon list).
    assert _place([], (0, 0, 250, 250)) == []


def test_window_mask_area_subtracts_holes_and_clamps_at_zero() -> None:
    window = CropWindow(0, 0, 100, 100)
    outer = _box(0, 0, 100, 100)
    hole = _box(25, 25, 75, 75)
    assert _window_mask_area([(outer, hole)], window, 1) == pytest.approx(10000 - 2500)
    # A hole larger than the exterior drives the raw total negative; the result clamps to 0.
    assert _window_mask_area([(hole, outer)], window, 1) == 0.0


def test_window_mask_area_ignores_missing_and_outside_polygons() -> None:
    window = CropWindow(0, 0, 100, 100)
    assert _window_mask_area([], window, 1) == 0.0
    assert _window_mask_area([(_box(500, 500, 600, 600),)], window, 1) == 0.0


def test_nearest_center_window_breaks_ties_toward_smaller_origin() -> None:
    a = CropWindow(0, 0, 100, 100)
    b = CropWindow(100, 0, 100, 100)
    # Centers (50,50) and (150,50); centroid (100,50) is equidistant.
    assert _nearest_center_window([b, a], (100.0, 50.0)) is a
    assert _nearest_center_window([a, b], (140.0, 50.0)) is b


def test_contained_uses_mask_centroid_not_padded_center_when_mask_has_area() -> None:
    # Mask centroid (15, 15) is nearest the center of the window at -50; the padded center
    # (30, 30) would pick the window at 0 instead.
    windows = _place([(_box(10, 10, 20, 20),)], (10, 10, 50, 50))
    assert [(w.origin_x, w.origin_y) for w in windows] == [(-50, -50)]
