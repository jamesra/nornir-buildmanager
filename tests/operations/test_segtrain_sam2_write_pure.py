"""Characterization tests for the pure SA-1B / manifest / gallery writers in ``sam2.write``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord
from nornir_buildmanager.operations.segmentationtraining.sam2.write import (
    append_manifest,
    sa1b_annotation,
    write_gallery,
)

_STAMP = datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)
_RLE = {"size": [4, 4], "counts": [16]}


def _record(type_id: int | None = 3, label: str | None = "Mito", type_name: str | None = "Organelle") -> LocationRecord:
    return LocationRecord(
        id=11,
        z=5,
        wkt="POLYGON((0 0,1 0,1 1,0 0))",
        parent_id=None,
        off_edge=False,
        last_modified=_STAMP,
        type_id=type_id,
        type_name=type_name,
        structure_label=label,
    )


def _annotation(record: LocationRecord | None = None, **kwargs: object) -> dict:
    return sa1b_annotation(record or _record(), rle=_RLE, bbox=[1, 2, 3, 4], area=9, **kwargs)  # type: ignore[arg-type]


def test_sa1b_annotation_base_keys_and_values() -> None:
    item = _annotation()
    assert item == {
        "id": 11,
        "segmentation": _RLE,
        "bbox": [1, 2, 3, 4],
        "area": 9,
        "category_id": 3,
        "category_name": "Mito",
        "structure_label": "Mito",
        "type_name": "Organelle",
        "last_modified": "2024-05-06T07:08:09+00:00",
    }


def test_sa1b_annotation_category_id_zero_when_type_id_none() -> None:
    assert _annotation(_record(type_id=None))["category_id"] == 0


def test_sa1b_annotation_category_name_falls_back_to_type_name_then_unlabeled() -> None:
    assert _annotation(_record(label=None))["category_name"] == "Organelle"
    assert _annotation(_record(label=None, type_name=None))["category_name"] == "unlabeled"


def test_sa1b_annotation_window_keys_only_when_window_index_set() -> None:
    assert "windowIndex" not in _annotation(window_count=4)
    assert "windowCount" not in _annotation(window_count=4)
    item = _annotation(window_index=0, window_count=4)
    assert item["windowIndex"] == 0
    assert item["windowCount"] == 4


def test_sa1b_annotation_origin_requires_both_and_casts_to_int() -> None:
    assert "originX" not in _annotation(origin_x=5)
    assert "originY" not in _annotation(origin_y=6)
    item = _annotation(origin_x=5.9, origin_y=-6.9)  # type: ignore[arg-type]
    assert item["originX"] == 5 and isinstance(item["originX"], int)
    assert item["originY"] == -6 and isinstance(item["originY"], int)
    zero = _annotation(origin_x=0, origin_y=0)
    assert zero["originX"] == 0 and zero["originY"] == 0


def test_append_manifest_writes_compact_jsonl_and_appends(tmp_path: Path) -> None:
    append_manifest(tmp_path, {"key": "a", "z": 1})
    append_manifest(tmp_path, {"key": "b", "z": 2})
    text = (tmp_path / "manifest.jsonl").read_text(encoding="utf-8")
    assert text == '{"key":"a","z":1}\n{"key":"b","z":2}\n'


def test_write_gallery_lists_only_existing_overlays(tmp_path: Path) -> None:
    (tmp_path / "overlays").mkdir()
    (tmp_path / "overlays" / "a.png").write_bytes(b"x")
    (tmp_path / "overlays" / "c.png").write_bytes(b"x")
    write_gallery(tmp_path, ["a", "b", "c"])
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    lines = text.split("\n")
    assert lines[0].startswith("<!DOCTYPE html>") and "<meta charset='utf-8'>" in lines[0]
    assert lines[1] == "<h1>SegmentationTraining overlays</h1>"
    assert lines[2] == "<ul>"
    assert lines[3:-1] == ["<li><a href='overlays/a.png'>a</a></li>", "<li><a href='overlays/c.png'>c</a></li>"]
    assert lines[-1] == "</ul></body></html>"


def test_write_gallery_empty_list_and_utf8(tmp_path: Path) -> None:
    (tmp_path / "overlays").mkdir()
    (tmp_path / "overlays" / "café.png").write_bytes(b"x")
    write_gallery(tmp_path, ["café"])
    assert "café" in (tmp_path / "index.html").read_bytes().decode("utf-8")
    write_gallery(tmp_path, [])
    assert "<li>" not in (tmp_path / "index.html").read_text(encoding="utf-8")
