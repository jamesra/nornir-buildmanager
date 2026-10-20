"""Ingest spills and sidecars are written atomically: a failed write keeps the old file."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from nornir_buildmanager.operations.segmentationtraining.ingest import (
    cache_meta_path,
    circles_jsonl_path,
    export_source_path,
    ingest_to_section_files,
    load_circle_records,
    load_section_records,
    section_jsonl_path,
    write_circle_jsonl,
    write_export_source,
    write_section_jsonl,
)
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord


def _fail_replace(src: object, dst: object) -> None:
    raise OSError("simulated crash before the swap")


def _record(record_id: int, z: int = 3) -> LocationRecord:
    return LocationRecord(
        id=record_id, z=z, wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))", parent_id=10, off_edge=False,
        last_modified=datetime.fromisoformat("2020-01-01T00:00:00+00:00"), type_id=1,
        type_name="Cell", structure_label="soma",
    )


def _expected_text(records: list[LocationRecord]) -> str:
    return "".join(json.dumps(r.to_json(), separators=(",", ":")) + "\n" for r in sorted(records, key=lambda r: r.id))


def _temps(folder: Path) -> list[Path]:
    return list(folder.rglob("*.tmp"))


def test_write_section_jsonl_text_is_sorted_compact_lines(tmp_path: Path) -> None:
    out = tmp_path / "out"
    records = [_record(2), _record(1)]
    write_section_jsonl(out, records)
    assert section_jsonl_path(out, 3).read_text(encoding="utf-8") == _expected_text(records)
    assert [r.id for r in load_section_records(out, 3)] == [1, 2]
    assert _temps(tmp_path) == []


def test_write_circle_jsonl_text_is_sorted_compact_lines(tmp_path: Path) -> None:
    out = tmp_path / "out"
    records = [_record(5), _record(4)]
    write_circle_jsonl(out, records)
    assert circles_jsonl_path(out, 3).read_text(encoding="utf-8") == _expected_text(records)
    assert [r.id for r in load_circle_records(out, 3)] == [4, 5]
    assert _temps(tmp_path) == []


def test_write_section_jsonl_failed_swap_keeps_old_spill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out"
    write_section_jsonl(out, [_record(1)])
    path = section_jsonl_path(out, 3)
    before = path.read_bytes()
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        write_section_jsonl(out, [_record(1), _record(2)])
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert [r.id for r in load_section_records(out, 3)] == [1]


def test_write_section_jsonl_failing_row_leaves_no_partial_spill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out"
    write_section_jsonl(out, [_record(9)])
    path = section_jsonl_path(out, 3)
    before = path.read_bytes()
    original_to_json = LocationRecord.to_json

    def second_row_explodes(self: LocationRecord) -> dict:
        if self.id == 2:
            raise ValueError("bad row")
        return original_to_json(self)

    monkeypatch.setattr(LocationRecord, "to_json", second_row_explodes)
    with pytest.raises(ValueError):
        write_section_jsonl(out, [_record(1), _record(2)])
    assert path.read_bytes() == before
    assert _temps(tmp_path) == []


def test_write_circle_jsonl_failed_swap_keeps_old_spill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out"
    write_circle_jsonl(out, [_record(1)])
    path = circles_jsonl_path(out, 3)
    before = path.read_bytes()
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        write_circle_jsonl(out, [_record(1), _record(2)])
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert [r.id for r in load_circle_records(out, 3)] == [1]


def test_write_export_source_failed_swap_keeps_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out"
    write_export_source(out, {"source": "odata", "odata": "http://example/odata"})
    path = export_source_path(out)
    before = path.read_bytes()
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        write_export_source(out, {"source": "odata", "odata": "http://other/odata"})
    monkeypatch.undo()
    assert path.read_bytes() == before


def test_cache_meta_failed_swap_keeps_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out"

    def getter(url: str, *args: object, **kwargs: object) -> dict:
        return {"value": []}

    ingest_to_section_files(output_path=out, odata="http://example/odata", geometries=None, http_get=getter)  # type: ignore[arg-type]
    path = cache_meta_path(out)
    before = path.read_bytes()
    assert before
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        ingest_to_section_files(output_path=out, odata="http://example/odata", geometries=None, http_get=getter)  # type: ignore[arg-type]
    monkeypatch.undo()
    assert path.read_bytes() == before
