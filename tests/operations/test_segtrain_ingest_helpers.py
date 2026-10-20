"""Characterization tests for pure OData helpers in segmentationtraining/ingest.py."""

from __future__ import annotations

import json
import tempfile
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from nornir_buildmanager.operations.segmentationtraining.ingest import (
    ODATA_PAGE_SIZE,
    _consecutive_ranges,
    _entities_from_odata_document,
    _exporter_name,
    _odata_count,
    _odata_next_link,
    _odata_locations_url,
    _parse_odata_dump,
    _source_from_meta,
    _stripped_text,
    cache_meta_path,
    load_export_source,
    odata_filter_from_output,
    odata_url_for_export,
    odata_url_from_output,
    write_export_source,
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


@pytest.mark.parametrize("doc", [None, [], [{"@odata.count": 3}], "text", 5])
def test_odata_accessors_return_none_for_non_dict_documents(doc: object) -> None:
    assert _odata_count(doc) is None
    assert _odata_next_link(doc) is None


@pytest.mark.parametrize(
    ("doc", "expected"),
    [({}, None), ({"@odata.nextLink": "http://x/next"}, "http://x/next"), ({"@odata.nextLink": ""}, "")],
)
def test_odata_next_link(doc: dict, expected: str | None) -> None:
    assert _odata_next_link(doc) == expected


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://x/odata", "http://x/odata"),
        ("  http://x/odata \n", "http://x/odata"),
        ("a b", "a b"),
        ("", None),
        ("   \t\n", None),
        (None, None),
        (5, None),
        (b" x ", None),
        (["x"], None),
    ],
)
def test_stripped_text(value: object, expected: str | None) -> None:
    assert _stripped_text(value) == expected


def test_source_from_meta_strips_url_and_drops_blank() -> None:
    assert _source_from_meta({"source": {"kind": "odata", "url": " http://s/ "}})["odata"] == "http://s/"
    assert "odata" not in _source_from_meta({"source": {"kind": "odata", "url": "  "}})
    assert "odata" not in _source_from_meta({"source": {"kind": "sql", "url": "http://s/"}})


def test_write_export_source_keeps_prior_url_on_sql(tmp_path) -> None:
    (tmp_path / "source.json").write_text(json.dumps({"odata": "  http://prior/ "}))
    write_export_source(tmp_path, {"source": {"kind": "sql"}})
    assert json.loads((tmp_path / "source.json").read_text())["odata"] == "http://prior/"


def test_load_export_source_reconstructs_stripped_url(tmp_path) -> None:
    (tmp_path / "source.json").write_text(json.dumps({"odata": "  http://u/ ", "filter": " $f "}))
    assert odata_url_from_output(tmp_path) == "http://u/"
    assert odata_filter_from_output(tmp_path) == "$f"
    (tmp_path / "source.json").write_text(json.dumps({"odata": "  ", "filter": ""}))
    assert odata_url_from_output(tmp_path) is None
    assert odata_filter_from_output(tmp_path) is None
    assert load_export_source(tmp_path) == {"odata": "  ", "filter": ""}


def test_odata_url_for_export_falls_back_to_image_meta(tmp_path) -> None:
    assert odata_url_for_export(tmp_path, {"odata": " http://m/ "}) == "http://m/"
    assert odata_url_for_export(tmp_path, {"odata": "  "}) is None
    assert odata_url_for_export(tmp_path, {"odata": 3}) is None
    assert odata_url_for_export(tmp_path, None) is None


@pytest.mark.parametrize(
    ("kind", "expected_odata"),
    [("odata", "http://c/"), ("sql", None)],
)
def test_load_export_source_reconstructs_from_cache_meta(tmp_path, kind: str, expected_odata: str | None) -> None:
    from nornir_buildmanager.operations.segmentationtraining.ingest import cache_meta_path

    meta_path = cache_meta_path(tmp_path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps({"source": {"kind": kind, "url": " http://c/ "}}))
    assert load_export_source(tmp_path).get("odata") == expected_odata


def _old_reconstruct(meta: dict[str, Any]) -> dict[str, Any]:
    """Copy of the reconstruction branch load_export_source had before it reused _source_from_meta."""
    raw = meta.get("source")
    source: dict[str, Any] = raw if isinstance(raw, dict) else {}
    if not meta and not source:
        return {}
    reconstructed: dict[str, Any] = {
        "kind": source.get("kind"),
        "filter": meta.get("filter") or source.get("filter"),
        "includeOffEdge": bool(meta.get("includeOffEdge")),
        "ingestedAt": meta.get("ingestedAt"),
    }
    url = _stripped_text(source.get("url"))
    if url and source.get("kind") != "sql":
        reconstructed["odata"] = url
    dump_path = source.get("path")
    if dump_path and source.get("kind") != "sql":
        reconstructed["path"] = str(dump_path)
    exporter = _exporter_name(meta.get("exporter") or source.get("exporter"))
    if exporter:
        reconstructed["exporter"] = exporter
    return {key: value for key, value in reconstructed.items() if value is not None}


def _reconstructed(meta: dict[str, Any]) -> dict[str, Any]:
    """load_export_source for an output folder with no source.json and the given cache meta."""
    with tempfile.TemporaryDirectory() as folder:
        meta_path = cache_meta_path(folder)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta))
        return load_export_source(folder)


def test_load_export_source_reconstruction_examples() -> None:
    assert _reconstructed({}) == {}
    assert _reconstructed({"source": {"kind": "sql", "url": "http://s/", "path": "d.json", "exporter": "Tiled"}}) == {
        "kind": "sql",
        "includeOffEdge": False,
        "exporter": "tiled",
    }
    assert _reconstructed(
        {
            "filter": "Z eq 1",
            "includeOffEdge": 1,
            "ingestedAt": "2026-01-01",
            "exporter": " LEGACY ",
            "source": {"kind": "odata", "url": " http://o/ ", "path": "dump.json", "filter": "ignored"},
        }
    ) == {
        "kind": "odata",
        "filter": "Z eq 1",
        "includeOffEdge": True,
        "ingestedAt": "2026-01-01",
        "odata": "http://o/",
        "path": "dump.json",
        "exporter": "legacy",
    }
    # None values are dropped; a blank filter falls through to source's, and "" from there is kept.
    assert _reconstructed({"filter": "", "source": {"url": "  "}}) == {"includeOffEdge": False}
    assert _reconstructed({"filter": "", "source": {"filter": ""}}) == {"filter": "", "includeOffEdge": False}


_text = st.one_of(st.none(), st.just(""), st.just("  "), st.sampled_from(["tiled", " Legacy ", "x", "Z eq 3"]), st.text(max_size=6))
_source_values = st.fixed_dictionaries(
    {},
    optional={
        "kind": st.sampled_from([None, "odata", "sql", "geometries", ""]),
        "url": _text,
        "path": _text,
        "filter": _text,
        "exporter": _text,
    },
)
_metas = st.fixed_dictionaries(
    {},
    optional={
        "source": st.one_of(_source_values, st.none(), st.just("not a dict")),
        "filter": _text,
        "includeOffEdge": st.one_of(st.booleans(), st.none(), st.integers(0, 2)),
        "ingestedAt": _text,
        "exporter": _text,
    },
)


@settings(max_examples=300, deadline=None)
@given(meta=_metas)
def test_load_export_source_reconstruction_matches_old_body(meta: dict[str, Any]) -> None:
    assert _reconstructed(meta) == _old_reconstruct(meta)
