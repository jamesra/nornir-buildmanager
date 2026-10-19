"""Characterization tests for the OData and WKT helpers in segmentationtraining wkt."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from nornir_buildmanager.operations.segmentationtraining.wkt import (
    _geojson_polygon_to_wkt,
    _optional_text,
    _split_top_level,
    circle_from_odata_entity,
    is_skippable_location,
    is_skippable_wkt,
    location_from_odata_entity,
    mosaic_wkt_from_odata_shape,
    parse_wkt_polygons,
)

_SQUARE = "POLYGON((0 0,4 0,4 4,0 4,0 0))"


def test_shape_none_and_unsupported_types_give_none() -> None:
    assert mosaic_wkt_from_odata_shape(None) is None
    assert mosaic_wkt_from_odata_shape(5) is None
    assert mosaic_wkt_from_odata_shape(["POINT(1 2)"]) is None


def test_shape_string_is_returned_verbatim() -> None:
    assert mosaic_wkt_from_odata_shape(_SQUARE) == _SQUARE
    assert mosaic_wkt_from_odata_shape("") == ""


def test_shape_geometry_string_under_either_key_case() -> None:
    assert mosaic_wkt_from_odata_shape({"Geometry": _SQUARE}) == _SQUARE
    assert mosaic_wkt_from_odata_shape({"geometry": _SQUARE}) == _SQUARE


def test_shape_well_known_text_under_either_key_case() -> None:
    assert mosaic_wkt_from_odata_shape({"Geometry": {"WellKnownText": _SQUARE}}) == _SQUARE
    assert mosaic_wkt_from_odata_shape({"geometry": {"wellKnownText": _SQUARE}}) == _SQUARE


def test_shape_without_geometry_key_treats_the_dict_itself_as_geometry() -> None:
    assert mosaic_wkt_from_odata_shape({"WellKnownText": _SQUARE}) == _SQUARE


def test_shape_empty_well_known_text_falls_through_to_geojson() -> None:
    geometry = {"WellKnownText": "", "type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    assert mosaic_wkt_from_odata_shape({"Geometry": geometry}) == "POLYGON((0 0,1 0,1 1,0 0))"


def test_shape_geojson_polygon_with_hole() -> None:
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [10, 0], [10, 10], [0, 0]],
            [[1, 1], [2, 1], [2, 2], [1, 1]],
        ],
    }
    assert (
        mosaic_wkt_from_odata_shape({"Geometry": geometry})
        == "POLYGON((0 0,10 0,10 10,0 0),(1 1,2 1,2 2,1 1))"
    )


def test_shape_geojson_multipolygon_strips_the_polygon_keyword_per_part() -> None:
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0, 0], [1, 0], [1, 1], [0, 0]]],
            [[[5, 5], [6, 5], [6, 6], [5, 5]]],
        ],
    }
    assert (
        mosaic_wkt_from_odata_shape({"Geometry": geometry})
        == "MULTIPOLYGON(((0 0,1 0,1 1,0 0)),((5 5,6 5,6 6,5 5)))"
    )


def test_shape_geojson_without_coordinates_or_unknown_type_gives_none() -> None:
    assert mosaic_wkt_from_odata_shape({"Geometry": {"type": "Polygon"}}) is None
    assert mosaic_wkt_from_odata_shape({"Geometry": {"type": "Point", "coordinates": [1, 2]}}) is None
    assert mosaic_wkt_from_odata_shape({"Geometry": 7}) is None


def test_geojson_polygon_to_wkt_keeps_float_text_and_ignores_extra_dimensions() -> None:
    assert _geojson_polygon_to_wkt([[[0.5, 1.25, 9.0], [2, 3, 9.0]]]) == "POLYGON((0.5 1.25,2 3))"
    assert _geojson_polygon_to_wkt([]) == "POLYGON()"


@pytest.mark.parametrize(
    "wkt",
    [
        "POINT(1 2)",
        "  multipoint((1 2))",
        "LINESTRING(0 0,1 1)",
        "POLYLINE(0 0,1 1)",
        "CIRCLE(1 2 3)",
        "CURVEPOLYGON(CIRCULARSTRING(0 0,1 1,2 0,1 -1,0 0))",
        "polygon(circularstring(0 0))",
    ],
)
def test_skippable_wkt_true(wkt: str) -> None:
    assert is_skippable_wkt(wkt)


@pytest.mark.parametrize("wkt", [_SQUARE, "MULTIPOLYGON(((0 0,1 0,1 1,0 0)))", "", "GEOMETRYCOLLECTION()"])
def test_skippable_wkt_false(wkt: str) -> None:
    assert not is_skippable_wkt(wkt)


def test_skippable_location_by_type_code_regardless_of_wkt() -> None:
    for code in (0, 1, 2, 3):
        assert is_skippable_location(code, _SQUARE)
    for code in (4, 5, 6, 7):
        assert not is_skippable_location(code, _SQUARE)


def test_skippable_location_none_type_code_defers_to_wkt() -> None:
    assert not is_skippable_location(None, _SQUARE)
    assert is_skippable_location(None, "POINT(1 2)")
    assert is_skippable_location(4, "POINT(1 2)")


def test_split_top_level_returns_each_depth_zero_group() -> None:
    assert _split_top_level("(0 0,1 1),(2 2,3 3)") == ["(0 0,1 1)", "(2 2,3 3)"]


def test_split_top_level_keeps_nested_parens_inside_one_part() -> None:
    assert _split_top_level("((0 0,1 1),(2 2,3 3)),((4 4,5 5))") == [
        "((0 0,1 1),(2 2,3 3))",
        "((4 4,5 5))",
    ]


def test_split_top_level_empty_and_unbalanced() -> None:
    assert _split_top_level("") == []
    assert _split_top_level("no parens here") == []
    assert _split_top_level("(1 2") == []


def test_optional_text_strips_and_collapses_blank_to_none() -> None:
    assert _optional_text(None) is None
    assert _optional_text("") is None
    assert _optional_text("   ") is None
    assert _optional_text("  Axon ") == "Axon"
    assert _optional_text(12) == "12"
    assert _optional_text(0) == "0"


def _entity(**overrides: object) -> dict[str, object]:
    entity: dict[str, object] = {
        "ID": "11",
        "Z": "42",
        "MosaicShape": {"Geometry": {"WellKnownText": "POLYGON((0 0,1 0,1 1,0 0))"}},
        "TypeCode": 4,
    }
    entity.update(overrides)
    return entity


def test_circle_from_entity_maps_fields_for_a_circle() -> None:
    entity = _entity(
        TypeCode=1,
        MosaicShape="CURVEPOLYGON(CIRCULARSTRING(0 0,1 1,2 0,1 -1,0 0))",
        Radius="12.5",
        Username=" alice ",
        ParentID="7",
        Parent={"Type": {"ID": 3, "Name": " Cell "}, "Label": "  L1 "},
        LastModified="2024-01-02T03:04:05Z",
    )
    record = circle_from_odata_entity(entity, include_off_edge=False)
    assert record is not None
    assert record.id == 11
    assert record.z == 42
    assert record.type_code == 1
    assert record.radius == 12.5
    assert record.username == "alice"
    assert record.parent_id == 7
    assert record.type_id == 3
    assert record.type_name == "Cell"
    assert record.structure_label == "L1"
    assert record.off_edge is False
    assert record.last_modified == datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_circle_from_entity_rejects_non_circle_missing_wkt_and_off_edge() -> None:
    assert circle_from_odata_entity(_entity(TypeCode=4), include_off_edge=True) is None
    assert circle_from_odata_entity(_entity(TypeCode=None), include_off_edge=True) is None
    assert circle_from_odata_entity(_entity(TypeCode=1, MosaicShape=None), include_off_edge=True) is None
    assert circle_from_odata_entity(_entity(TypeCode=1, OffEdge=True), include_off_edge=False) is None
    kept = circle_from_odata_entity(_entity(TypeCode=1, OffEdge=True), include_off_edge=True)
    assert kept is not None and kept.off_edge is True


def test_location_from_entity_maps_polygon_and_defaults() -> None:
    record = location_from_odata_entity(_entity(), include_off_edge=False)
    assert record is not None
    assert record.wkt == "POLYGON((0 0,1 0,1 1,0 0))"
    assert record.type_code == 4
    assert record.radius is None
    assert record.username is None
    assert record.parent_id is None
    assert record.type_id is None
    assert record.type_name is None
    assert record.structure_label is None
    assert record.last_modified == datetime.min.replace(tzinfo=UTC)


def test_location_from_entity_skips_circles_points_empty_wkt_and_off_edge() -> None:
    assert location_from_odata_entity(_entity(TypeCode=1), include_off_edge=True) is None
    assert location_from_odata_entity(_entity(TypeCode=4, MosaicShape="POINT(1 2)"), include_off_edge=True) is None
    assert location_from_odata_entity(_entity(MosaicShape=None), include_off_edge=True) is None
    assert location_from_odata_entity(_entity(MosaicShape=""), include_off_edge=True) is None
    assert location_from_odata_entity(_entity(OffEdge=True), include_off_edge=False) is None
    assert location_from_odata_entity(_entity(OffEdge=True), include_off_edge=True) is not None


def test_location_from_entity_blank_type_code_and_radius_become_none() -> None:
    record = location_from_odata_entity(_entity(TypeCode="", Radius=""), include_off_edge=False)
    assert record is not None
    assert record.type_code is None
    assert record.radius is None


def test_location_from_entity_parent_id_prefers_entity_over_parent_and_type_id_over_nested() -> None:
    entity = _entity(ParentID=5, Parent={"ID": 9, "TypeID": 2, "Type": {"ID": 8}})
    record = location_from_odata_entity(entity, include_off_edge=False)
    assert record is not None
    assert record.parent_id == 5
    assert record.type_id == 2
    fallback = location_from_odata_entity(_entity(Parent={"ID": 9, "Type": {"ID": 8}}), include_off_edge=False)
    assert fallback is not None
    assert fallback.parent_id == 9
    assert fallback.type_id == 8


def test_parse_wkt_polygons_multipolygon_yields_one_polygon_per_part() -> None:
    wkt = "MULTIPOLYGON(((0 0,9 0,9 9,0 9,0 0)),((20 20,30 20,30 30,20 20)))"
    polygons = parse_wkt_polygons(wkt)
    assert [len(rings) for rings in polygons] == [1, 1]
    assert polygons[1][0] == ((20.0, 20.0), (30.0, 20.0), (30.0, 30.0), (20.0, 20.0))


def test_parse_wkt_polygons_polygon_with_hole_gives_exterior_then_hole() -> None:
    polygons = parse_wkt_polygons("POLYGON((0 0,9 0,9 9,0 0),(2 2,3 2,3 3,2 2))")
    assert [len(rings) for rings in polygons] == [2]
    assert polygons[0][1] == ((2.0, 2.0), (3.0, 2.0), (3.0, 3.0), (2.0, 2.0))
