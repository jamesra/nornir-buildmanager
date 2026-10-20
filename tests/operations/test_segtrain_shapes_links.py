"""Characterization tests for the Viking link helpers in ``shapes`` (fetch, cache, spill listing)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from nornir_buildmanager.operations.segmentationtraining.shapes import (
    LINK_MAX_DZ,
    LINKS_CACHE_VERSION,
    _ids_digest,
    _links_cache_path,
    _spilled_sections,
    collect_links,
    fetch_links,
)
from nornir_buildmanager.operations.segmentationtraining.workdir import work_root

ROOT = "http://lab.example/RC1/OData"


class FakeOData:
    """Serves one page of locations whose expanded link lists are given; records requested urls."""

    def __init__(self, entities: list[dict[str, Any]]) -> None:
        self.entities = entities
        self.urls: list[str] = []

    def __call__(self, url: str) -> dict[str, Any]:
        self.urls.append(url)
        return {"value": self.entities}


def _entity(loc_id: int, a: Sequence[tuple[Any, Any]] = (), b: Sequence[tuple[Any, Any]] = ()) -> dict[str, Any]:
    return {
        "ID": loc_id,
        "LocationLinksA": [{"A": x, "B": y} for x, y in a],
        "LocationLinksB": [{"A": x, "B": y} for x, y in b],
    }


def _output(tmp_path: Path, *, odata: str | None = ROOT) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    if odata is not None:
        (out / "source.json").write_text(json.dumps({"odata": odata, "filter": "ID gt 0"}), encoding="utf-8")
    return out


#: location id -> (z, structure id)
KNOWN: dict[int, tuple[int, int | None]] = {
    1: (10, 7),
    2: (11, 7),
    3: (12, 7),
    4: (11, 8),  # different structure
    5: (11, None),  # no structure
    6: (10, 7),  # same z as 1
    7: (20, 7),  # too far from 1
}


class TestIdsDigest:
    def test_order_independent(self) -> None:
        assert _ids_digest([3, 1, 2]) == _ids_digest([1, 2, 3]) == _ids_digest({2, 3, 1})

    def test_differs_by_content(self) -> None:
        assert _ids_digest([1, 2]) != _ids_digest([1, 3])
        assert _ids_digest([1, 2]) != _ids_digest([1, 2, 3])

    def test_numeric_sort_comma_separated_sha1(self) -> None:
        assert _ids_digest([10, 2, 1]) == hashlib.sha1(b"1,2,10").hexdigest()


def test_links_cache_path_in_work_folder(tmp_path: Path) -> None:
    out = _output(tmp_path)
    path = _links_cache_path(out)
    assert path.name == "location_links.json"
    assert path.parent == work_root(out)
    assert path.parent.is_dir()


class TestFetchLinks:
    def test_filters_dedups_and_orders(self) -> None:
        fake = FakeOData(
            [
                _entity(
                    1,
                    a=[(1, 2), (2, 1)],  # duplicate, reversed
                    b=[(3, 1), (1, 1), (1, 4), (1, 5), (1, 6), (1, 7)],
                ),
                _entity(2, a=[(2, 3)]),
            ]
        )
        # (1,1) a==b; (1,4) other structure; (1,5) no structure; (1,6) dz 0; (1,7) dz too big.
        assert fetch_links(ROOT, KNOWN, http_get=fake) == [(1, 2), (1, 3), (2, 3)]

    def test_skips_malformed_links(self) -> None:
        fake = FakeOData(
            [
                {
                    "ID": 1,
                    "LocationLinksA": [{"A": 1}, {"A": "x", "B": 2}, {"A": None, "B": 2}, {"A": 1, "B": 2}],
                    "LocationLinksB": None,
                }
            ]
        )
        assert fetch_links(ROOT, KNOWN, http_get=fake) == [(1, 2)]

    def test_two_locations_without_structure_are_not_linked(self) -> None:
        known = {**KNOWN, 8: (12, None)}
        assert fetch_links(ROOT, known, http_get=FakeOData([_entity(5, a=[(5, 8)])])) == []

    def test_unknown_location_ignored(self) -> None:
        assert fetch_links(ROOT, KNOWN, http_get=FakeOData([_entity(1, a=[(1, 99)])])) == []

    def test_dz_bounds(self) -> None:
        known: dict[int, tuple[int, int | None]] = {1: (0, 1), 2: (LINK_MAX_DZ, 1), 3: (LINK_MAX_DZ + 1, 1), 4: (0, 1)}
        fake = FakeOData([_entity(1, a=[(1, 2), (1, 3), (1, 4)])])
        assert fetch_links(ROOT, known, http_get=fake) == [(1, 2)]

    def test_requests_one_expanded_query(self) -> None:
        fake = FakeOData([])
        fetch_links(ROOT + "/", KNOWN, filter_text="Z lt 5", http_get=fake)
        assert len(fake.urls) == 1
        url = fake.urls[0]
        assert url.startswith(ROOT + "/")
        assert "LocationLinksA" in url and "LocationLinksB" in url

    def test_filter_text_goes_into_url_and_default_differs(self) -> None:
        with_filter, default = FakeOData([]), FakeOData([])
        fetch_links(ROOT, KNOWN, filter_text="Z lt 5", http_get=with_filter)
        fetch_links(ROOT, KNOWN, http_get=default)
        assert with_filter.urls != default.urls


class TestCollectLinks:
    @staticmethod
    def _fake() -> FakeOData:
        return FakeOData([_entity(1, a=[(1, 3), (1, 2)], b=[(6, 2)]), _entity(2, a=[(2, 3)])])

    def test_no_root_no_cache(self, tmp_path: Path) -> None:
        out = _output(tmp_path, odata=None)
        assert collect_links(out, KNOWN, refresh=False) == {}

    def test_no_root_uses_existing_cache_even_with_refresh(self, tmp_path: Path) -> None:
        out = _output(tmp_path, odata=None)
        _links_cache_path(out).write_text(json.dumps({"links": [[1, 2], [2, 3]]}), encoding="utf-8")
        assert collect_links(out, KNOWN, refresh=True) == {1: [2], 2: [1, 3], 3: [2]}

    def test_cached_links_between_unknown_locations_dropped(self, tmp_path: Path) -> None:
        out = _output(tmp_path, odata=None)
        _links_cache_path(out).write_text(json.dumps({"links": [[98, 99]]}), encoding="utf-8")
        assert collect_links(out, KNOWN, refresh=False) == {}

    def test_no_root_corrupt_cache(self, tmp_path: Path) -> None:
        out = _output(tmp_path, odata=None)
        _links_cache_path(out).write_text("{not json", encoding="utf-8")
        assert collect_links(out, KNOWN, refresh=False) == {}

    def test_fetch_writes_cache_and_sorts_by_z_then_id(self, tmp_path: Path) -> None:
        out = _output(tmp_path)
        fake = self._fake()
        result = collect_links(out, KNOWN, refresh=False, http_get=fake)
        # Neighbours of 2 sort by (z, id): 1 and 6 (z=10) before 3 (z=12).
        assert result == {1: [2, 3], 2: [1, 6, 3], 3: [1, 2], 6: [2]}
        payload = json.loads(_links_cache_path(out).read_text(encoding="utf-8"))
        assert payload["version"] == LINKS_CACHE_VERSION
        assert payload["root"] == ROOT
        assert payload["ids"] == _ids_digest(KNOWN)
        assert payload["links"] == [[1, 2], [1, 3], [2, 3], [2, 6]]
        assert len(fake.urls) == 1

    def test_cache_hit_skips_fetch(self, tmp_path: Path) -> None:
        out = _output(tmp_path)
        first = collect_links(out, KNOWN, refresh=False, http_get=self._fake())
        again = FakeOData([])
        assert collect_links(out, KNOWN, refresh=False, http_get=again) == first
        assert again.urls == []

    def test_refresh_refetches(self, tmp_path: Path) -> None:
        out = _output(tmp_path)
        collect_links(out, KNOWN, refresh=False, http_get=self._fake())
        again = self._fake()
        collect_links(out, KNOWN, refresh=True, http_get=again)
        assert len(again.urls) == 1

    @pytest.mark.parametrize(
        "field,value",
        [("version", -1), ("ids", "stale"), ("root", "http://other/OData"), ("links", "oops")],
    )
    def test_stale_cache_refetched_and_rewritten(self, tmp_path: Path, field: str, value: Any) -> None:
        out = _output(tmp_path)
        collect_links(out, KNOWN, refresh=False, http_get=self._fake())
        cache = _links_cache_path(out)
        payload = json.loads(cache.read_text(encoding="utf-8"))
        payload[field] = value
        if field != "links":
            payload["links"] = []
        cache.write_text(json.dumps(payload), encoding="utf-8")
        again = self._fake()
        result = collect_links(out, KNOWN, refresh=False, http_get=again)
        assert len(again.urls) == 1
        assert result[1] == [2, 3]
        fresh = json.loads(cache.read_text(encoding="utf-8"))
        assert fresh["version"] == LINKS_CACHE_VERSION and fresh["root"] == ROOT and fresh["links"]

    def test_changed_known_ids_refetch(self, tmp_path: Path) -> None:
        out = _output(tmp_path)
        collect_links(out, KNOWN, refresh=False, http_get=self._fake())
        again = self._fake()
        collect_links(out, {**KNOWN, 8: (10, 7)}, refresh=False, http_get=again)
        assert len(again.urls) == 1

    def test_locations_without_links_are_absent(self, tmp_path: Path) -> None:
        out = _output(tmp_path)
        result = collect_links(out, KNOWN, refresh=False, http_get=self._fake())
        assert set(result) == {1, 2, 3, 6}


class TestSpilledSections:
    def test_missing_dir(self, tmp_path: Path) -> None:
        assert _spilled_sections(tmp_path / "nowhere") == []

    def test_sorted_numeric_and_ignores_junk(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        out.mkdir()
        work = work_root(out)
        work.mkdir(parents=True)
        for name in ("section_10.jsonl", "section_2.jsonl", "section_x.jsonl", "section_.jsonl", "other_3.jsonl"):
            (work / name).write_text("", encoding="utf-8")
        assert _spilled_sections(out) == [2, 10]
