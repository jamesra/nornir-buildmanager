"""Characterization tests for the pixel-ring, area/centroid, and lattice-origin helpers in segmentationtraining geometry."""

from __future__ import annotations

import pytest

from nornir_buildmanager.operations.segmentationtraining.geometry import (
    _clip_ring_to_image,
    _containing_origins,
    _intersect_x,
    _intersect_y,
    _intersecting_origins,
    _mask_centroid_px,
    _polygons_area_centroid,
    _ring_touches_image,
    _simplify_pixel_ring,
)

SQUARE_10 = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
SQUARE_4 = ((2.0, 2.0), (6.0, 2.0), (6.0, 6.0), (2.0, 6.0))


def test_simplify_empty_and_single_vertex_unchanged() -> None:
    assert _simplify_pixel_ring(()) == ()
    assert _simplify_pixel_ring(((3, 4),)) == ((3, 4),)


def test_simplify_drops_consecutive_duplicates() -> None:
    assert _simplify_pixel_ring(((0, 0), (0, 0), (1, 0), (1, 0), (1, 1))) == ((0, 0), (1, 0), (1, 1))


def test_simplify_drops_closing_vertex() -> None:
    assert _simplify_pixel_ring(((0, 0), (5, 0), (5, 5), (0, 0))) == ((0, 0), (5, 0), (5, 5))


def test_simplify_keeps_non_consecutive_repeats() -> None:
    ring = ((0, 0), (1, 0), (0, 0), (1, 1))
    assert _simplify_pixel_ring(ring) == ring


def test_simplify_all_same_point_collapses_to_one() -> None:
    assert _simplify_pixel_ring(((2, 2), (2, 2), (2, 2))) == ((2, 2),)


def test_ring_touches_image_requires_three_vertices() -> None:
    assert not _ring_touches_image(((1, 1), (2, 2)), 10, 10)


def test_ring_touches_image_inside_and_edge() -> None:
    assert _ring_touches_image(((1, 1), (3, 1), (3, 3)), 10, 10)
    # min x == width - 1 still touches; min x == width does not
    assert _ring_touches_image(((9, 1), (12, 1), (12, 3)), 10, 10)
    assert not _ring_touches_image(((10, 1), (12, 1), (12, 3)), 10, 10)


@pytest.mark.parametrize(
    "ring",
    [
        ((-5, 1), (-1, 1), (-1, 3)),  # left (max x < 0)
        ((10, 1), (12, 1), (12, 3)),  # right (min x >= width)
        ((1, -5), (3, -5), (3, -1)),  # above (max y < 0)
        ((1, 10), (3, 10), (3, 12)),  # below (min y >= height)
    ],
)
def test_ring_touches_image_fully_outside_each_side(ring: tuple[tuple[int, int], ...]) -> None:
    assert not _ring_touches_image(ring, 10, 10)


def test_ring_touches_image_max_equal_zero_touches() -> None:
    assert _ring_touches_image(((-3, 1), (0, 1), (0, 3)), 10, 10)


def test_intersect_x_interpolates_and_rounds() -> None:
    assert _intersect_x((0, 0), (10, 10), 5) == (5, 5)
    assert _intersect_x((0, 0), (10, 5), 5) == (5, 2)  # 2.5 rounds half-to-even


def test_intersect_x_vertical_edge_keeps_a_y() -> None:
    assert _intersect_x((3, 7), (3, 20), 0) == (0, 7)


def test_intersect_y_interpolates_and_horizontal_edge_keeps_a_x() -> None:
    assert _intersect_y((0, 0), (10, 10), 4) == (4, 4)
    assert _intersect_y((7, 3), (20, 3), 0) == (7, 0)


def test_clip_fully_inside_is_unchanged() -> None:
    ring = ((1, 1), (8, 1), (8, 8), (1, 8))
    assert _clip_ring_to_image(ring, 10, 10) == ring


def test_clip_fully_outside_is_empty() -> None:
    assert _clip_ring_to_image(((20, 20), (30, 20), (30, 30), (20, 30)), 10, 10) == ()


@pytest.mark.parametrize(
    "ring, expected_vertices",
    [
        (((-5, 2), (5, 2), (5, 8), (-5, 8)), {(0, 2), (5, 2), (5, 8), (0, 8)}),  # left
        (((5, 2), (15, 2), (15, 8), (5, 8)), {(5, 2), (10, 2), (10, 8), (5, 8)}),  # right
        (((2, -5), (8, -5), (8, 5), (2, 5)), {(2, 0), (8, 0), (8, 5), (2, 5)}),  # top
        (((2, 5), (8, 5), (8, 15), (2, 15)), {(2, 5), (8, 5), (8, 10), (2, 10)}),  # bottom
    ],
)
def test_clip_straddling_each_edge(ring: tuple[tuple[int, int], ...], expected_vertices: set[tuple[int, int]]) -> None:
    clipped = _clip_ring_to_image(ring, 10, 10)
    assert set(clipped) == expected_vertices
    assert len(clipped) == 4


def test_clip_surrounding_window_returns_window_corners() -> None:
    clipped = _clip_ring_to_image(((-5, -5), (15, -5), (15, 15), (-5, 15)), 10, 10)
    assert set(clipped) == {(0, 0), (10, 0), (10, 10), (0, 10)}


def test_polygons_area_centroid_single_square() -> None:
    area, sum_x, sum_y = _polygons_area_centroid([(SQUARE_10,)])
    assert area == pytest.approx(100.0)
    assert sum_x == pytest.approx(500.0)
    assert sum_y == pytest.approx(500.0)


def test_polygons_area_centroid_subtracts_holes() -> None:
    area, sum_x, sum_y = _polygons_area_centroid([(SQUARE_10, SQUARE_4)])
    assert area == pytest.approx(100.0 - 16.0)
    assert sum_x == pytest.approx(500.0 - 16.0 * 4.0)
    assert sum_y == pytest.approx(500.0 - 16.0 * 4.0)


def test_polygons_area_centroid_orientation_independent() -> None:
    reversed_ring = tuple(reversed(SQUARE_10))
    assert _polygons_area_centroid([(reversed_ring,)]) == pytest.approx(_polygons_area_centroid([(SQUARE_10,)]))


def test_polygons_area_centroid_skips_empty_entries_and_empty_list() -> None:
    assert _polygons_area_centroid([]) == (0.0, 0.0, 0.0)
    assert _polygons_area_centroid([(), (SQUARE_10,)])[0] == pytest.approx(100.0)


def test_polygons_area_centroid_degenerate_ring_counts_zero() -> None:
    collinear = ((0.0, 0.0), (5.0, 5.0), (10.0, 10.0))
    assert _polygons_area_centroid([(collinear,)]) == (0.0, 0.0, 0.0)


def test_mask_centroid_px_divides_by_downsample() -> None:
    assert _mask_centroid_px([(SQUARE_10,)], 2, (0.0, 0.0, 99.0, 99.0)) == pytest.approx((2.5, 2.5))


def test_mask_centroid_px_zero_downsample_treated_as_one() -> None:
    assert _mask_centroid_px([(SQUARE_10,)], 0, (0.0, 0.0, 99.0, 99.0)) == pytest.approx((5.0, 5.0))


def test_mask_centroid_px_falls_back_to_bbox_center_when_no_area() -> None:
    assert _mask_centroid_px([], 4, (10.0, 20.0, 30.0, 60.0)) == pytest.approx((5.0, 10.0))
    assert _mask_centroid_px([], 0, (10.0, 20.0, 30.0, 60.0)) == pytest.approx((20.0, 40.0))


def test_containing_origins_rejects_nonpositive_step_and_too_wide_span() -> None:
    assert _containing_origins(0.0, 5.0, 10, 0) == []
    assert _containing_origins(0.0, 5.0, 10, -2) == []
    assert _containing_origins(0.0, 11.0, 10, 5) == []


def test_containing_origins_lists_lattice_origins_covering_span() -> None:
    # windows of 10 on a step-5 lattice that contain [6, 9]: origins 0 and 5
    assert _containing_origins(6.0, 9.0, 10, 5) == [0, 5]


def test_containing_origins_boundary_epsilon_and_exact_fit() -> None:
    assert _containing_origins(0.0, 10.0, 10, 5) == [0]
    assert _containing_origins(0.0, 10.0 + 1e-10, 10, 5) == [0]


def test_containing_origins_negative_span() -> None:
    assert _containing_origins(-7.0, -3.0, 10, 5) == [-10]


def test_intersecting_origins_nonpositive_crop_is_empty() -> None:
    assert _intersecting_origins(0.0, 5.0, 0, 0) == []
    assert _intersecting_origins(0.0, 5.0, -4, 0) == []


def test_intersecting_origins_phase_zero() -> None:
    assert _intersecting_origins(5.0, 25.0, 10, 0) == [0, 10, 20]


def test_intersecting_origins_phase_shift_and_touching_edges_excluded() -> None:
    assert _intersecting_origins(5.0, 25.0, 10, 5) == [5, 15]
    # a window ending exactly at edge_min, or starting exactly at edge_max, does not meet the span
    assert _intersecting_origins(10.0, 20.0, 10, 0) == [10]
