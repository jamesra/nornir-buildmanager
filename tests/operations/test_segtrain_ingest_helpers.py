"""Characterization tests for pure OData helpers in segmentationtraining/ingest.py."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest
from nornir_buildmanager.operations.segmentationtraining.ingest import (
    ODATA_PAGE_SIZE,
    _consecutive_ranges,
    _entities_from_odata_document,
    _odata_count,
    _odata_locations_url,
    _parse_odata_dump,
)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], []),
        ([5], [(5, 5)]),
        ([3, 1, 2], [(1, 3)]),
        ([1, 1, 2, 2], [(1, 2)]),
        ([1, 2, 4, 5, 9], [(1, 2), (4, 5), (9, 9)]),
        ([10, 1, 3], [(1, 1), (3, 3), (10, 10)]),
        ([-2, -1, 0, 1], [(-2, 1)]),
    ],
)
def test_consecutive_ranges(values: list[int], expected: list[tuple[int, int]]) -> None:
    assert _consecutive_ranges(values) == expected


def test_consecutive_ranges_does_not_mutate_input() -> None:
    values = [3, 1, 2]
    _consecutive_ranges(values)
    assert values == [3, 1, 2]


def test_odata_locations_url_basic() -> None:
    url = _odata_locations_url(
        "http://host/odata/", filter_text="Z eq 5", select="ID,Z", expand=None
    )
    assert url.startswith("http://host/odata/Locations?")
    assert "//Locations" not in url
    query = parse_qs(urlparse(url).query)
    assert query["$filter"] == ["Z eq 5"]
    assert query["$orderby"] == ["Z,ID"]
    assert query["$select"] == ["ID,Z"]
    assert query["$count"] == ["true"]
    assert query["$top"] == [str(ODATA_PAGE_SIZE)]
    assert "$expand" not in query


def test_odata_locations_url_quoting_and_expand() -> None:
    url = _odata_locations_url(
        "http://host/odata", filter_text="Z ge 1 and Z le 2", select="ID", expand="Tags($select=ID;$top=2)"
    )
    assert "$filter=Z%20ge%201%20and%20Z%20le%202" in url
    assert "&$expand=Tags($select%3DID;$top%3D2)" in url


def test_odata_locations_url_empty_expand_omitted() -> None:
    url = _odata_locations_url("http://h", filter_text="x", select="ID", expand="")
    assert "$expand" not in url


@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        ({}, None),
        ({"@odata.count": None}, None),
        ({"@odata.count": 7}, 7),
        ({"@odata.count": "12"}, 12),
        ({"@odata.count": 0}, None),
        ({"@odata.count": -3}, None),
        ({"@odata.count": "abc"}, None),
        ({"@odata.count": [1]}, None),
    ],
)
def test_odata_count(doc: dict, expected: int | None) -> None:
    assert _odata_count(doc) == expected


def test_entities_from_document_variants() -> None:
    a, b = {"ID": 1}, {"ID": 2}
    assert _entities_from_odata_document([a, "junk", 3, b]) == [a, b]
    assert _entities_from_odata_document({"value": [a, None, b]}) == [a, b]
    assert _entities_from_odata_document({"ID": 9, "x": 1}) == [{"ID": 9, "x": 1}]
    assert _entities_from_odata_document({"other": 1}) == []
    assert _entities_from_odata_document("text") == []
    assert _entities_from_odata_document(None) == []


def test_entities_non_list_value_falls_through_to_id() -> None:
    doc = {"value": "scalar", "ID": 4}
    assert _entities_from_odata_document(doc) == [doc]
    assert _entities_from_odata_document({"value": "scalar"}) == []


def test_parse_odata_dump_empty() -> None:
    assert list(_parse_odata_dump("")) == []
    assert list(_parse_odata_dump("  \n\t ")) == []


def test_parse_odata_dump_concatenated_documents() -> None:
    text = json.dumps({"value": [{"ID": 1}, {"ID": 2}]}) + "\n\n" + json.dumps({"value": [{"ID": 3}]})
    assert [e["ID"] for e in _parse_odata_dump(text)] == [1, 2, 3]


def test_parse_odata_dump_jsonl_fallback() -> None:
    text = '{"ID": 1}\n\n{"ID": 2}\n'
    assert [e["ID"] for e in _parse_odata_dump(text)] == [1, 2]


def test_parse_odata_dump_array_text_is_not_json_lines() -> None:
    # A leading "[" skips the concatenated-document branch; each line must be a JSON value.
    with pytest.raises(json.JSONDecodeError):
        list(_parse_odata_dump("[1,\n2]"))
