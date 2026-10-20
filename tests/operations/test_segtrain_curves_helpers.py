"""Characterization tests for the private pure-math helpers in ``curves.py``.

These pin the current behaviour of the Viking Catmull-Rom port helpers that
``test_segtrain_curves`` only reaches indirectly through ``fit_curve``.
"""

from __future__ import annotations

import math

import pytest
from nornir_buildmanager.operations.segmentationtraining import curves
from nornir_buildmanager.operations.segmentationtraining.curves import (
    EPSILON,
    _arc_angle,
    _control_points_for_closed_curve,
    _control_points_for_open_curve,
    _ensure_closed,
    _measure_curvature,
    _remove_adjacent_duplicates_preserving,
    _try_add_t_points_above_threshold,
    calculate_closed_curve_points,
    fit_closed_curve_ring,
    fit_curve,
)

# --- _remove_adjacent_duplicates_preserving -------------------------------------------------


def test_remove_duplicates_empty_raises() -> None:
    """An empty sample list is rejected, matching the Viking guard."""
    with pytest.raises(ValueError):
        _remove_adjacent_duplicates_preserving([], [(0.0, 0.0)])


def test_remove_duplicates_single_point_is_returned() -> None:
    """A lone sample never enters the loop and is returned unchanged."""
    assert _remove_adjacent_duplicates_preserving([(3.0, 4.0)], [(3.0, 4.0)]) == [(3.0, 4.0)]


def test_remove_duplicates_collapses_adjacent_without_preserved() -> None:
    """Adjacent near-equal samples collapse to one when no preserved path applies."""
    points = [(0.0, 0.0), (0.0, 0.0), (5.0, 0.0), (5.0, 0.0), (9.0, 0.0)]
    assert _remove_adjacent_duplicates_preserving(points, []) == [(0.0, 0.0), (5.0, 0.0), (9.0, 0.0)]


def test_remove_duplicates_within_epsilon_counts_as_duplicate() -> None:
    """Points closer than EPSILON are duplicates; points further apart are kept."""
    close = (EPSILON / 2.0, 0.0)
    far = (EPSILON * 3.0, 0.0)
    # Of a duplicate pair the earlier point is dropped and the later one is kept.
    assert _remove_adjacent_duplicates_preserving([(0.0, 0.0), close, (10.0, 0.0)], []) == [close, (10.0, 0.0)]
    assert _remove_adjacent_duplicates_preserving([(0.0, 0.0), far, (10.0, 0.0)], []) == [
        (0.0, 0.0),
        far,
        (10.0, 0.0),
    ]


def test_remove_duplicates_exact_preserved_point_is_kept_and_followers_skipped() -> None:
    """An exact preserved control point is emitted and its near-duplicates are skipped."""
    preserved = [(0.0, 0.0), (10.0, 0.0)]
    points = [(0.0, 0.0), (0.0, 0.0001), (4.0, 0.0), (10.0, 0.0)]
    assert _remove_adjacent_duplicates_preserving(points, preserved) == [(0.0, 0.0), (4.0, 0.0), (10.0, 0.0)]


def test_remove_duplicates_near_preserved_point_is_replaced_by_preserved() -> None:
    """A sample only near a preserved point is replaced by the preserved coordinates."""
    preserved = [(0.0, 0.0), (10.0, 0.0)]
    points = [(0.0, 0.0), (5.0, 0.0), (10.0005, 0.0), (20.0, 0.0)]
    assert _remove_adjacent_duplicates_preserving(points, preserved) == [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0), (20.0, 0.0)]


def test_remove_duplicates_near_preserved_with_near_follower_is_dropped() -> None:
    """A near-preserved sample followed by another near-preserved sample defers to the follower."""
    preserved = [(0.0, 0.0), (10.0, 0.0)]
    points = [(0.0, 0.0), (5.0, 0.0), (10.0005, 0.0), (10.0008, 0.0), (20.0, 0.0)]
    result = _remove_adjacent_duplicates_preserving(points, preserved)
    assert result[0] == (0.0, 0.0)
    assert result[-1] == (20.0, 0.0)
    assert (10.0, 0.0) in result
    assert (10.0005, 0.0) not in result


def test_remove_duplicates_last_point_is_always_appended() -> None:
    """The final sample is appended even when it duplicates the previous one's neighbourhood."""
    points = [(0.0, 0.0), (7.0, 0.0)]
    assert _remove_adjacent_duplicates_preserving(points, []) == [(0.0, 0.0), (7.0, 0.0)]


# --- _arc_angle ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ((1.0, 0.0), (0.0, 1.0), -math.pi / 2),  # counter-clockwise is negative
        ((0.0, 1.0), (1.0, 0.0), math.pi / 2),  # clockwise is positive
        ((1.0, 0.0), (1.0, 0.0), 0.0),
        ((1.0, 0.0), (2.0, 0.0), 0.0),
        ((1.0, 0.0), (0.0, -1.0), math.pi / 2),
    ],
)
def test_arc_angle_quadrants(a: tuple[float, float], b: tuple[float, float], expected: float) -> None:
    """Signed turn between two rays from the origin follows the Viking sign convention."""
    assert _arc_angle((0.0, 0.0), a, b) == pytest.approx(expected)


def test_arc_angle_opposite_rays_wrap_to_positive_pi() -> None:
    """A half turn is reported as +pi, never -pi (the lower bound is exclusive)."""
    assert _arc_angle((0.0, 0.0), (1.0, 0.0), (-1.0, 0.0)) == pytest.approx(math.pi)
    assert _arc_angle((0.0, 0.0), (-1.0, 0.0), (1.0, 0.0)) == pytest.approx(math.pi)


def test_arc_angle_wraps_large_differences_into_range() -> None:
    """Differences beyond pi wrap back into (-pi, pi]."""
    angle = _arc_angle((0.0, 0.0), (math.cos(3.0), math.sin(3.0)), (math.cos(-3.0), math.sin(-3.0)))
    assert -math.pi < angle <= math.pi
    assert angle == pytest.approx(6.0 - 2.0 * math.pi)


def test_arc_angle_uses_origin_offset() -> None:
    """Rays are measured from the supplied origin, not the coordinate origin."""
    assert _arc_angle((5.0, 5.0), (6.0, 5.0), (5.0, 6.0)) == pytest.approx(-math.pi / 2)


# --- _measure_curvature ----------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 2])
def test_measure_curvature_fewer_than_three_points_is_all_zero(count: int) -> None:
    """Without an interior vertex there is nothing to measure."""
    points = [(float(i), float(i * i)) for i in range(count)]
    assert _measure_curvature(points) == [0.0] * count


def test_measure_curvature_straight_line_is_zero() -> None:
    """Collinear points have no turn at any interior vertex."""
    angles = _measure_curvature([(0.0, 0.0), (1.0, 0.0), (3.0, 0.0), (4.0, 0.0)])
    assert angles == pytest.approx([0.0, 0.0, 0.0, 0.0])


def test_measure_curvature_right_angle_turn_sign_and_size() -> None:
    """A 90 degree turn is measured at the middle vertex only; ends stay zero."""
    left = _measure_curvature([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
    right = _measure_curvature([(0.0, 0.0), (1.0, 0.0), (1.0, -1.0)])
    assert left[0] == 0.0 and left[2] == 0.0
    assert abs(left[1]) == pytest.approx(math.pi / 2)
    assert left[1] == pytest.approx(-right[1])


def test_measure_curvature_exact_duplicate_raises() -> None:
    """Exactly equal consecutive points raise, mirroring the Viking exception."""
    with pytest.raises(ValueError, match="Duplicate"):
        _measure_curvature([(0.0, 0.0), (0.0, 0.0), (1.0, 1.0)])


def test_measure_curvature_near_duplicate_gives_zero_angle() -> None:
    """Points closer than EPSILON but not identical yield a zero angle instead of raising."""
    angles = _measure_curvature([(0.0, 0.0), (EPSILON / 10.0, 0.0), (1.0, 1.0)])
    assert angles == [0.0, 0.0, 0.0]


# --- _try_add_t_points_above_threshold -------------------------------------------------------


def test_try_add_t_points_straight_line_adds_nothing() -> None:
    """Collinear output never exceeds the angle threshold."""
    output = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
    added, updated = _try_add_t_points_above_threshold(output, [0.0, 1 / 3, 2 / 3, 1.0])
    assert added is False
    assert updated == {0.0, 1 / 3, 2 / 3, 1.0}


def test_try_add_t_points_sharp_turn_adds_midpoints_around_vertex() -> None:
    """A sharp, long-enough turn adds midpoints on both sides of the vertex."""
    output = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (4.0, 2.0)]
    scalars = [0.0, 0.25, 0.5, 1.0]
    added, updated = _try_add_t_points_above_threshold(output, scalars)
    assert added is True
    assert updated == {0.0, 0.25, 0.5, 1.0, 0.125, 0.375, 0.75}


def test_try_add_t_points_turn_below_angle_threshold_adds_nothing() -> None:
    """A turn smaller than the angle threshold is ignored even when segments are long."""
    output = [(0.0, 0.0), (10.0, 0.0), (20.0, 1.0)]  # about 5.7 degrees
    added, _ = _try_add_t_points_above_threshold(output, [0.0, 0.5, 1.0])
    assert added is False


def test_try_add_t_points_angle_threshold_is_a_parameter() -> None:
    """The same turn is accepted once the angle threshold drops below it."""
    output = [(0.0, 0.0), (10.0, 0.0), (20.0, 1.0)]
    added, updated = _try_add_t_points_above_threshold(output, [0.0, 0.5, 1.0], angle_threshold_degrees=1.0)
    assert added is True
    assert updated == {0.0, 0.5, 1.0, 0.25, 0.75}


def test_try_add_t_points_short_segments_below_distance_threshold_add_nothing() -> None:
    """A sharp turn between tiny segments is skipped by the squared-length gate."""
    output = [(0.0, 0.0), (0.1, 0.0), (0.1, 0.1), (0.2, 0.1)]
    added, _ = _try_add_t_points_above_threshold(output, [0.0, 0.25, 0.5, 1.0])
    assert added is False


def test_try_add_t_points_drops_epsilon_duplicates_and_their_scalars() -> None:
    """Near-duplicate interior points are removed from the sample set together with their t."""
    output = [(0.0, 0.0), (EPSILON / 10.0, 0.0), (1.0, 0.0)]
    added, updated = _try_add_t_points_above_threshold(output, [0.0, 0.5, 1.0])
    assert added is False
    assert updated == {0.0, 1.0}


# --- control point padding -------------------------------------------------------------------


def test_closed_control_points_pad_open_ring_with_wraparound() -> None:
    """An unclosed ring A-B-C-D is closed, then padded to C-D-A-B-C-D-A."""
    a, b, c, d = (0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)
    assert _control_points_for_closed_curve([a, b, c, d]) == [c, d, a, b, c, d, a]


def test_closed_control_points_pad_closed_ring() -> None:
    """A ring that already repeats its first point is padded to D-A-B-C-D-A-B."""
    a, b, c, d = (0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)
    assert _control_points_for_closed_curve([a, b, c, d, a]) == [d, a, b, c, d, a, b]


def test_closed_control_points_drop_adjacent_near_duplicates() -> None:
    """Consecutive near-duplicate control points collapse before padding."""
    a, b, c = (0.0, 0.0), (4.0, 0.0), (4.0, 4.0)
    near_b = (4.0 + EPSILON / 10.0, 0.0)
    assert _control_points_for_closed_curve([a, b, near_b, c, a]) == [c, a, b, c, a, b]


def test_closed_control_points_do_not_mutate_input() -> None:
    """The padding works on a copy of the caller's sequence."""
    ring = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)]
    snapshot = list(ring)
    _control_points_for_closed_curve(ring)
    assert ring == snapshot


def test_open_control_points_extend_both_ends_along_end_segments() -> None:
    """Open curves get phantom points half a segment before the start and after the end."""
    padded = _control_points_for_open_curve([(0.0, 0.0), (2.0, 0.0), (2.0, 4.0)])
    assert padded[0] == pytest.approx((-1.0, 0.0))
    assert padded[1:-1] == [(0.0, 0.0), (2.0, 0.0), (2.0, 4.0)]
    assert padded[-1] == pytest.approx((2.0, 6.0))


# --- ring-level behaviour that depends on the helpers ----------------------------------------


def test_ensure_closed_appends_first_point_only_when_open() -> None:
    """_ensure_closed is idempotent and leaves an empty list alone."""
    assert _ensure_closed([]) == []
    assert _ensure_closed([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]) == [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
    closed = [(0.0, 0.0), (1.0, 0.0), (0.0, 0.0)]
    assert _ensure_closed(list(closed)) == closed


def test_calculate_closed_curve_points_small_and_zero_interpolation() -> None:
    """Two or fewer points pass through; zero interpolations only close the ring."""
    assert calculate_closed_curve_points([(0.0, 0.0), (1.0, 1.0)], 10) == [(0.0, 0.0), (1.0, 1.0)]
    square = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    assert calculate_closed_curve_points(square, 0) == square + [(0.0, 0.0)]


def test_fit_curve_small_inputs_return_control_points_unchanged() -> None:
    """fit_curve short-circuits for <=2 points or zero interpolations."""
    assert fit_curve([(0.0, 0.0), (1.0, 1.0)], 10, closed=False) == [(0.0, 0.0), (1.0, 1.0)]
    assert fit_curve([(0.0, 0.0), (1.0, 0.0), (2.0, 1.0)], 0, closed=True) == [(0.0, 0.0), (1.0, 0.0), (2.0, 1.0)]


def test_fit_curve_open_curve_passes_through_end_control_points() -> None:
    """An open fit starts and ends exactly on the first and last control points."""
    control = [(0.0, 0.0), (10.0, 5.0), (20.0, 0.0)]
    fitted = fit_curve(control, 10, closed=False)
    assert fitted[0] == pytest.approx(control[0])
    assert fitted[-1] == pytest.approx(control[-1])
    assert len(fitted) > len(control)


def test_fit_closed_curve_ring_short_ring_returned_as_is() -> None:
    """Rings with fewer than three points are returned untouched."""
    ring = ((0.0, 0.0), (1.0, 1.0))
    assert fit_closed_curve_ring(ring) is ring


def test_recursion_depth_cap_constant_is_finite() -> None:
    """The recursion cap that stops runaway subdivision is a positive bound."""
    assert 0 < curves._MAX_CURVE_RECURSION <= 64


def test_closed_control_points_keep_points_just_beyond_epsilon_apart() -> None:
    """Dedup compares squared distance with EPSILON squared, so points 0.01 apart survive."""
    a, b, c = (0.0, 0.0), (4.0, 0.0), (4.0, 4.0)
    close_b = (4.01, 0.0)
    assert _control_points_for_closed_curve([a, b, close_b, c, a]) == [c, a, b, close_b, c, a, b]
