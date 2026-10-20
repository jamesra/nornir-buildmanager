"""Characterization tests for the pure circle helpers in segmentationtraining circles."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from nornir_buildmanager.operations.segmentationtraining.circles import (
    CIRCLE_ANNOTATIONS_VERSION,
    FAITHFUL_DIAMETER_SCALE,
    _circumcenter,
    annotated_circle,
    build_circle_document,
    circle_geometry_from_wkt,
    circle_intersects_window,
)
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord

_RING = "CURVEPOLYGON(CIRCULARSTRING(1 0, 0 1, -1 0, 0 -1, 1 0))"


def _record(
    wkt: str = _RING,
    *,
    type_code: int | None = 1,
    radius: float | None = None,
    structure_label: str | None = None,
    type_name: str | None = None,
) -> LocationRecord:
    return LocationRecord(
        id=11,
        z=3,
        wkt=wkt,
        parent_id=None,
        off_edge=False,
        last_modified=datetime(2026, 1, 1, tzinfo=UTC),
        type_id=None,
        type_name=type_name,
        structure_label=structure_label,
        type_code=type_code,
        radius=radius,
    )


# circle_intersects_window


def test_disc_inside_window_intersects() -> None:
    assert circle_intersects_window(50.0, 50.0, 10.0, width=100, height=100)


def test_disc_far_outside_window_does_not_intersect() -> None:
    assert not circle_intersects_window(500.0, 50.0, 10.0, width=100, height=100)


def test_disc_touching_an_edge_at_a_point_does_not_intersect() -> None:
    assert not circle_intersects_window(110.0, 50.0, 10.0, width=100, height=100)
    assert not circle_intersects_window(50.0, -10.0, 10.0, width=100, height=100)


def test_disc_overlapping_an_edge_intersects() -> None:
    assert circle_intersects_window(109.0, 50.0, 10.0, width=100, height=100)


def test_disc_near_a_corner_uses_euclidean_distance() -> None:
    # Corner (100, 100); center 7,7 away is ~9.9 from it, so a radius 10 disc overlaps and radius 9 does not.
    assert circle_intersects_window(107.0, 107.0, 10.0, width=100, height=100)
    assert not circle_intersects_window(107.0, 107.0, 9.0, width=100, height=100)


def test_window_is_not_square() -> None:
    assert circle_intersects_window(150.0, 10.0, 60.0, width=100, height=20)
    assert not circle_intersects_window(10.0, 150.0, 60.0, width=100, height=20)


# circle_geometry_from_wkt


def test_circle_keyword_form() -> None:
    assert circle_geometry_from_wkt("CIRCLE((10 20), 5)") == (10.0, 20.0, 5.0)


def test_circle_keyword_form_accepts_signs_and_exponents() -> None:
    assert circle_geometry_from_wkt("circle((-1.5 +2.5e1), 4)") == (-1.5, 25.0, 4.0)


def test_circle_keyword_form_rejects_nonpositive_radius_and_missing_numbers() -> None:
    assert circle_geometry_from_wkt("CIRCLE((10 20), 0)") is None
    assert circle_geometry_from_wkt("CIRCLE((10 20), -3)") is None
    assert circle_geometry_from_wkt("CIRCLE((10 20))") is None


def test_circularstring_gives_circumcenter_and_radius() -> None:
    assert circle_geometry_from_wkt(_RING) == pytest.approx((0.0, 0.0, 1.0))


def test_circularstring_offset_circle() -> None:
    wkt = "CURVEPOLYGON(CIRCULARSTRING(110 50, 100 60, 90 50, 100 40, 110 50))"
    assert circle_geometry_from_wkt(wkt) == pytest.approx((100.0, 50.0, 10.0))


def test_circularstring_ignores_z_values() -> None:
    wkt = "CURVEPOLYGON(CIRCULARSTRING(1 0 7, 0 1 7, -1 0 7, 0 -1 7, 1 0 7))"
    assert circle_geometry_from_wkt(wkt) == pytest.approx((0.0, 0.0, 1.0))


def test_circularstring_is_case_insensitive() -> None:
    assert circle_geometry_from_wkt(_RING.lower()) == pytest.approx((0.0, 0.0, 1.0))


def test_circularstring_with_collinear_start_falls_back_to_bbox_center() -> None:
    assert circle_geometry_from_wkt("CIRCULARSTRING(0 0, 1 0, 2 0)") == pytest.approx((1.0, 0.0, 1.0))


def test_circularstring_with_fewer_than_three_points_is_none() -> None:
    assert circle_geometry_from_wkt("CIRCULARSTRING(0 0, 1 1)") is None


def test_circularstring_with_zero_radius_is_none() -> None:
    assert circle_geometry_from_wkt("CIRCULARSTRING(5 5, 5 5, 5 5)") is None


@pytest.mark.parametrize("wkt", ["", "garbage", "POLYGON((0 0, 1 0, 1 1, 0 0))", "CURVEPOLYGON(EMPTY)"])
def test_unparseable_shapes_give_none(wkt: str) -> None:
    assert circle_geometry_from_wkt(wkt) is None


# _circumcenter


def test_circumcenter_of_a_right_triangle_is_the_hypotenuse_midpoint() -> None:
    assert _circumcenter((0.0, 0.0), (4.0, 0.0), (0.0, 3.0)) == pytest.approx((2.0, 1.5))


def test_circumcenter_is_equidistant_from_the_three_points() -> None:
    a, b, c = (1.0, 2.0), (7.0, 3.0), (4.0, 9.0)
    center = _circumcenter(a, b, c)
    assert center is not None
    distances = [math.hypot(p[0] - center[0], p[1] - center[1]) for p in (a, b, c)]
    assert distances == pytest.approx([distances[0]] * 3)


def test_circumcenter_of_collinear_points_is_none() -> None:
    assert _circumcenter((0.0, 0.0), (1.0, 1.0), (2.0, 2.0)) is None


def test_circumcenter_of_nearly_collinear_points_is_none() -> None:
    # Determinant is 2e-10, below the 1e-9 cutoff, so the circle is treated as degenerate.
    assert _circumcenter((0.0, 0.0), (1.0, 0.0), (2.0, 1e-10)) is None


def test_circumcenter_of_repeated_point_is_none() -> None:
    assert _circumcenter((3.0, 3.0), (3.0, 3.0), (3.0, 3.0)) is None


# annotated_circle


def test_annotated_circle_uses_wkt_radius_without_stored_radius() -> None:
    circle = annotated_circle(_record())
    assert circle is not None
    assert (circle.center_x, circle.center_y, circle.radius) == pytest.approx((0.0, 0.0, 1.0))
    assert circle.location_id == 11
    assert circle.source == "annotated"
    assert circle.has_polygon_mask is False


def test_annotated_circle_prefers_positive_stored_radius() -> None:
    circle = annotated_circle(_record(radius=42.5))
    assert circle is not None
    assert circle.radius == 42.5
    assert (circle.center_x, circle.center_y) == pytest.approx((0.0, 0.0))


@pytest.mark.parametrize("stored", [0.0, -5.0])
def test_annotated_circle_ignores_nonpositive_stored_radius(stored: float) -> None:
    circle = annotated_circle(_record(radius=stored))
    assert circle is not None
    assert circle.radius == pytest.approx(1.0)


def test_annotated_circle_rejects_other_type_codes() -> None:
    assert annotated_circle(_record(type_code=4)) is None
    assert annotated_circle(_record(type_code=0)) is None


def test_annotated_circle_accepts_missing_type_code() -> None:
    assert annotated_circle(_record(type_code=None)) is not None


def test_annotated_circle_unparseable_shape_is_none() -> None:
    assert annotated_circle(_record("not a shape")) is None


def test_annotated_circle_label_prefers_structure_label_then_type_name_then_empty() -> None:
    labeled = annotated_circle(_record(structure_label="mito", type_name="Circle"))
    named = annotated_circle(_record(type_name="Circle"))
    bare = annotated_circle(_record())
    assert labeled is not None and named is not None and bare is not None
    assert labeled.structure_label == "mito"
    assert named.structure_label == "Circle"
    assert bare.structure_label == ""


# build_circle_document


def test_build_circle_document_layout_and_sorted_keys() -> None:
    cells = {"b": [{"location_id": 2}], "a": [{"location_id": 1}]}
    document = build_circle_document(cells)
    assert document["version"] == CIRCLE_ANNOTATIONS_VERSION == 1
    assert document["diameter_scale"] == FAITHFUL_DIAMETER_SCALE == 1.0
    assert list(document["grid_cells"]) == ["a", "b"]
    assert document["grid_cells"]["a"] == [{"location_id": 1}]


def test_build_circle_document_empty() -> None:
    assert build_circle_document({}) == {"version": 1, "diameter_scale": 1.0, "grid_cells": {}}
