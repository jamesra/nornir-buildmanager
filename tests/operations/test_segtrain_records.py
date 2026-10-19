"""Characterization tests for segmentationtraining records: timestamps, coercion, JSON round trip, OData filters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from nornir_buildmanager.operations.segmentationtraining.records import (
    CIRCLE_TYPE_FILTER,
    CLOSED_POLYGON_TYPE_FILTER,
    DEFAULT_CIRCLE_ODATA_FILTER,
    DEFAULT_ODATA_FILTER,
    LocationRecord,
    _optional_float,
    _optional_int,
    _optional_str,
    circle_odata_filter,
    odata_filter_for_structure_type_ids,
    parse_datetime,
    resolve_odata_filter,
)


def _record(**overrides: object) -> LocationRecord:
    """Build a fully populated record, overriding the named fields."""
    fields: dict[str, object] = {
        "id": 7,
        "z": 12,
        "wkt": "POLYGON((0 0,1 0,1 1,0 0))",
        "parent_id": 3,
        "off_edge": True,
        "last_modified": datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC),
        "type_id": 1,
        "type_name": "Cell",
        "structure_label": "Nucleus",
        "type_code": 6,
        "radius": 2.5,
        "username": "alice",
    }
    fields.update(overrides)
    return LocationRecord(**fields)  # type: ignore[arg-type]


# --- parse_datetime -------------------------------------------------------------------------


def test_parse_datetime_none_is_min_utc() -> None:
    assert parse_datetime(None) == datetime.min.replace(tzinfo=UTC)


def test_parse_datetime_naive_datetime_assumed_utc() -> None:
    result = parse_datetime(datetime(2020, 1, 2, 3, 4, 5))
    assert result == datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert result.utcoffset() == timedelta(0)


def test_parse_datetime_aware_datetime_converted_to_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    result = parse_datetime(datetime(2020, 1, 2, 3, 0, 0, tzinfo=plus_two))
    assert result == datetime(2020, 1, 2, 1, 0, 0, tzinfo=UTC)
    assert result.utcoffset() == timedelta(0)


def test_parse_datetime_odata_date_form() -> None:
    assert parse_datetime("/Date(1700000000000)/") == datetime.fromtimestamp(1700000000, tz=UTC)


def test_parse_datetime_odata_date_ignores_positive_offset_suffix() -> None:
    assert parse_datetime("/Date(1700000000000+0200)/") == datetime.fromtimestamp(1700000000, tz=UTC)


def test_parse_datetime_odata_date_ignores_negative_offset_suffix() -> None:
    assert parse_datetime("/Date(1700000000000-0700)/") == datetime.fromtimestamp(1700000000, tz=UTC)


def test_parse_datetime_odata_date_keeps_millisecond_fraction() -> None:
    assert parse_datetime("/Date(1500)/") == datetime(1970, 1, 1, 0, 0, 1, 500000, tzinfo=UTC)


def test_parse_datetime_odata_date_strips_whitespace() -> None:
    assert parse_datetime("  /Date(0)/  ") == datetime(1970, 1, 1, tzinfo=UTC)


def test_parse_datetime_iso_z_suffix() -> None:
    assert parse_datetime("2024-05-06T07:08:09Z") == datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)


def test_parse_datetime_naive_iso_assumed_utc() -> None:
    assert parse_datetime("2024-05-06T07:08:09") == datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)


def test_parse_datetime_iso_offset_converted_to_utc() -> None:
    result = parse_datetime("2024-05-06T07:08:09-07:00")
    assert result == datetime(2024, 5, 6, 14, 8, 9, tzinfo=UTC)
    assert result.utcoffset() == timedelta(0)


def test_parse_datetime_garbage_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_datetime("not a date")


# --- _optional_* coercion -------------------------------------------------------------------


@pytest.mark.parametrize("value, expected", [(None, None), ("", None), ("5", 5), (5, 5), (5.9, 5), ("-3", -3), (0, 0)])
def test_optional_int(value: object, expected: int | None) -> None:
    assert _optional_int(value) == expected


def test_optional_int_does_not_treat_whitespace_as_empty() -> None:
    with pytest.raises(ValueError):
        _optional_int("  ")


@pytest.mark.parametrize(
    "value, expected", [(None, None), ("", None), ("2.5", 2.5), (3, 3.0), (0, 0.0), ("-1e3", -1000.0)]
)
def test_optional_float(value: object, expected: float | None) -> None:
    assert _optional_float(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [(None, None), ("", None), ("   ", None), ("  abc \n", "abc"), (12, "12"), ("a b", "a b")],
)
def test_optional_str(value: object, expected: str | None) -> None:
    assert _optional_str(value) == expected


# --- LocationRecord JSON --------------------------------------------------------------------


def test_to_json_uses_camel_case_keys_and_utc_iso() -> None:
    plus_two = timezone(timedelta(hours=2))
    payload = _record(last_modified=datetime(2024, 5, 6, 9, 8, 9, tzinfo=plus_two)).to_json()
    assert payload == {
        "id": 7,
        "z": 12,
        "wkt": "POLYGON((0 0,1 0,1 1,0 0))",
        "parentId": 3,
        "offEdge": True,
        "lastModified": "2024-05-06T07:08:09+00:00",
        "typeId": 1,
        "typeName": "Cell",
        "structureLabel": "Nucleus",
        "typeCode": 6,
        "radius": 2.5,
        "username": "alice",
    }


def test_to_json_omits_username_only_when_none() -> None:
    assert "username" not in _record(username=None).to_json()
    assert _record(username="").to_json()["username"] == ""


def test_to_json_keeps_other_none_fields() -> None:
    payload = _record(
        parent_id=None, type_id=None, type_name=None, structure_label=None, type_code=None, radius=None
    ).to_json()
    for key in ("parentId", "typeId", "typeName", "structureLabel", "typeCode", "radius"):
        assert key in payload and payload[key] is None


def test_json_round_trip() -> None:
    record = _record()
    assert LocationRecord.from_json(record.to_json()) == record


def test_json_round_trip_with_all_optionals_none() -> None:
    record = _record(
        parent_id=None,
        type_id=None,
        type_name=None,
        structure_label=None,
        type_code=None,
        radius=None,
        username=None,
        off_edge=False,
    )
    assert LocationRecord.from_json(record.to_json()) == record


def test_from_json_accepts_snake_case_keys() -> None:
    record = LocationRecord.from_json(
        {
            "id": "7",
            "z": "12",
            "wkt": "W",
            "parent_id": "3",
            "off_edge": 1,
            "last_modified": "2024-05-06T07:08:09Z",
            "type_id": "1",
            "type_name": "Cell",
            "structure_label": "Nucleus",
            "type_code": "6",
            "radius": "2.5",
        }
    )
    assert record == _record(wkt="W", username=None)


def test_from_json_camel_case_wins_over_snake_case() -> None:
    record = LocationRecord.from_json(
        {"id": 1, "z": 1, "wkt": "W", "parentId": 5, "parent_id": 9, "lastModified": "2024-01-01T00:00:00Z"}
    )
    assert record.parent_id == 5


def test_from_json_defaults_and_missing_timestamp() -> None:
    record = LocationRecord.from_json({"id": 1, "z": 2, "wkt": "W"})
    assert record.off_edge is False
    assert record.last_modified == datetime.min.replace(tzinfo=UTC)
    assert record.parent_id is None and record.type_code is None
    assert record.radius is None and record.username is None


def test_from_json_blank_strings_become_none() -> None:
    record = LocationRecord.from_json(
        {"id": 1, "z": 2, "wkt": "W", "parentId": "", "typeName": "  ", "structureLabel": "", "radius": "", "username": " "}
    )
    assert record.parent_id is None and record.type_name is None
    assert record.structure_label is None and record.radius is None and record.username is None


def test_from_json_missing_required_key_raises() -> None:
    with pytest.raises(KeyError):
        LocationRecord.from_json({"id": 1, "z": 2})


# --- category_name --------------------------------------------------------------------------


def test_category_name_precedence() -> None:
    assert _record(structure_label="L", type_name="T").category_name() == "L"
    assert _record(structure_label=None, type_name="T").category_name() == "T"
    assert _record(structure_label="", type_name="T").category_name() == "T"
    assert _record(structure_label=None, type_name=None).category_name() == "unlabeled"
    assert _record(structure_label="", type_name="").category_name() == "unlabeled"


# --- OData filters --------------------------------------------------------------------------


def test_default_circle_filter_swaps_only_type_clause() -> None:
    assert CLOSED_POLYGON_TYPE_FILTER not in DEFAULT_CIRCLE_ODATA_FILTER
    assert DEFAULT_CIRCLE_ODATA_FILTER == "(TypeCode eq 1) and (Parent/TypeID eq 1 or Parent/Type/ParentID eq 1)"


def test_structure_type_filter_joins_ids_with_or() -> None:
    assert odata_filter_for_structure_type_ids([4, 9]) == (
        f"{CLOSED_POLYGON_TYPE_FILTER} and (Parent/TypeID eq 4 or Parent/TypeID eq 9)"
    )


def test_structure_type_filter_empty_raises() -> None:
    with pytest.raises(ValueError, match="at least one"):
        odata_filter_for_structure_type_ids([])


@pytest.mark.parametrize("bad", [0, -2])
def test_structure_type_filter_non_positive_raises(bad: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        odata_filter_for_structure_type_ids([3, bad])


def test_resolve_prefers_explicit_filter_then_ids_then_default() -> None:
    assert resolve_odata_filter("X eq 1", [5]) == "X eq 1"
    assert resolve_odata_filter("", [5]) == odata_filter_for_structure_type_ids([5])
    assert resolve_odata_filter(None, [5]) == odata_filter_for_structure_type_ids([5])
    assert resolve_odata_filter(None, None) == DEFAULT_ODATA_FILTER
    assert resolve_odata_filter(None, []) == DEFAULT_ODATA_FILTER


def test_circle_filter_default_and_ids() -> None:
    assert circle_odata_filter(None, None) == DEFAULT_CIRCLE_ODATA_FILTER
    assert circle_odata_filter(None, [5]) == f"{CIRCLE_TYPE_FILTER} and (Parent/TypeID eq 5)"


def test_circle_filter_rewrites_explicit_filter_containing_standard_clause() -> None:
    custom = f"{CLOSED_POLYGON_TYPE_FILTER} and Z eq 3"
    assert circle_odata_filter(custom, None) == f"{CIRCLE_TYPE_FILTER} and Z eq 3"


def test_circle_filter_custom_filter_without_standard_clause_is_none() -> None:
    assert circle_odata_filter("TypeCode eq 4 and Z eq 3", None) is None
