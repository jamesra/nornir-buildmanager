"""In-place rewrites of existing sidecars keep the old file when the write fails."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from nornir_buildmanager.operations.segmentationtraining.cleanup import (
    CleanupSummary,
    _repair_key,
)
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ImageWatermark,
    SectionWatermark,
    load_section_watermark,
    save_section_watermark,
    section_meta_path,
)
from nornir_buildmanager.operations.segmentationtraining.maskrefresh import (
    replace_image_annotations,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import CropProductIndex
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord
from nornir_buildmanager.operations.segmentationtraining.sam2.write import ensure_sa1b_odata, write_sa1b_json
from nornir_buildmanager.operations.segmentationtraining.update import _drop_annotations


def _fail_replace(src: object, dst: object) -> None:
    raise OSError("simulated crash before the swap")


def _watermark(ids: list[int]) -> SectionWatermark:
    return SectionWatermark(
        ids=ids,
        max_last_modified="2020-01-01T00:00:00",
        tileset_mtime=None,
        params_hash="h",
        downsample=1,
        image_keys=["k"],
        images=[ImageWatermark(key="k", downsample=1, ix0=0, ix1=1, iy0=0, iy1=1, member_ids=ids,
                               max_last_modified="2020-01-01T00:00:00")],
    )


def _leftover_json_temps(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*.tmp")]


def test_save_section_watermark_failed_write_keeps_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_section_watermark(tmp_path, 3, _watermark([1]))
    path = section_meta_path(tmp_path, 3)
    before = path.read_bytes()
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        save_section_watermark(tmp_path, 3, _watermark([1, 2]))
    monkeypatch.undo()
    assert path.read_bytes() == before
    loaded = load_section_watermark(tmp_path, 3)
    assert loaded is not None and loaded.ids == [1]


def test_save_section_watermark_round_trips(tmp_path: Path) -> None:
    save_section_watermark(tmp_path, 3, _watermark([1, 2]))
    loaded = load_section_watermark(tmp_path, 3)
    assert loaded is not None and loaded.ids == [1, 2]
    assert _leftover_json_temps(tmp_path) == []


def test_replace_image_annotations_failed_write_keeps_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    json_path = tmp_path / "k.json"
    original = json.dumps({"image": {}, "annotations": [{"id": 1, "windowIndex": 0, "windowCount": 1}]})
    json_path.write_text(original, encoding="utf-8")
    rle_path = tmp_path / "k_1.rle.json"
    rle_path.write_text(json.dumps({"size": [2, 2], "counts": [0, 4]}), encoding="utf-8")
    record = LocationRecord(
        id=1, z=1, wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))", parent_id=10, off_edge=False,
        last_modified=datetime.fromisoformat("2020-01-01T00:00:00+00:00"), type_id=1,
        type_name="Cell", structure_label="soma",
    )
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        replace_image_annotations(json_path, [(record, rle_path, lambda rle: ([0, 0, 2, 2], 4))])
    monkeypatch.undo()
    assert json_path.read_text(encoding="utf-8") == original


def test_repair_key_failed_rewrite_keeps_old_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    images = tmp_path / "images"
    images.mkdir()
    (tmp_path / "masks").mkdir()
    Image.new("L", (4, 4)).save(images / "k.png")
    json_path = images / "k.json"
    original = json.dumps({"image": {"file_name": "k.png", "width": 4, "height": 4}, "annotations": [{"id": 7}]})
    json_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        _repair_key(tmp_path, "k", CleanupSummary())
    monkeypatch.undo()
    assert json_path.read_text(encoding="utf-8") == original


def test_ensure_sa1b_odata_failed_rewrite_keeps_old_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    json_path = tmp_path / "k.json"
    original = json.dumps({"image": {"file_name": "k.png"}, "annotations": []})
    json_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        ensure_sa1b_odata(json_path, "http://example/odata")
    monkeypatch.undo()
    assert json_path.read_text(encoding="utf-8") == original


def test_ensure_sa1b_odata_rewrite_bytes_and_no_temp(tmp_path: Path) -> None:
    json_path = tmp_path / "k.json"
    json_path.write_text(json.dumps({"image": {"file_name": "k.png"}, "annotations": []}), encoding="utf-8")
    assert ensure_sa1b_odata(json_path, "http://example/odata") is True
    expected = json.dumps({"image": {"file_name": "k.png", "odata": "http://example/odata"}, "annotations": []})
    assert json_path.read_text(encoding="utf-8") == expected
    assert _leftover_json_temps(tmp_path) == []


def test_write_sa1b_json_bytes_and_no_temp(tmp_path: Path) -> None:
    json_path = tmp_path / "images" / "k.json"
    write_sa1b_json(json_path, file_name="k.png", width=4, height=3, annotations=[{"id": 1}], downsample=2, volume="v")
    expected = json.dumps(
        {"image": {"file_name": "k.png", "width": 4, "height": 3, "downsample": 2, "volume": "v"},
         "annotations": [{"id": 1}]}
    )
    assert json_path.read_text(encoding="utf-8") == expected
    assert _leftover_json_temps(tmp_path) == []


def test_write_sa1b_json_failed_write_keeps_old_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    json_path = tmp_path / "k.json"
    original = json.dumps({"image": {"file_name": "old.png"}, "annotations": []})
    json_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        write_sa1b_json(json_path, file_name="k.png", width=4, height=3, annotations=[])
    monkeypatch.undo()
    assert json_path.read_text(encoding="utf-8") == original


def test_drop_annotations_failed_rewrite_keeps_old_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    images = tmp_path / "images"
    images.mkdir()
    json_path = images / "k.json"
    original = json.dumps({"image": {"file_name": "k.png"}, "annotations": [{"id": 1}, {"id": 2}]})
    json_path.write_text(original, encoding="utf-8")
    products = CropProductIndex(tmp_path)
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        _drop_annotations(tmp_path, "k", {1}, products)
    monkeypatch.undo()
    assert json_path.read_text(encoding="utf-8") == original


def test_drop_annotations_rewrite_bytes_and_no_temp(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    json_path = images / "k.json"
    json_path.write_text(json.dumps({"image": {"file_name": "k.png"}, "annotations": [{"id": 1}, {"id": 2}]}), encoding="utf-8")
    _drop_annotations(tmp_path, "k", {1}, CropProductIndex(tmp_path))
    assert json_path.read_text(encoding="utf-8") == json.dumps({"image": {"file_name": "k.png"}, "annotations": [{"id": 2}]})
    assert _leftover_json_temps(tmp_path) == []
