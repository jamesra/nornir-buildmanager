"""Pins the per-image SA-1B annotation rewrite shared by maskrefresh and update."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from annotation_crops.rle import bbox_area_from_rle

from nornir_buildmanager.operations.segmentationtraining import maskrefresh, update
from nornir_buildmanager.operations.segmentationtraining.masks import MaskJob
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord, LocationType
from nornir_buildmanager.operations.segmentationtraining.sam2.write import sa1b_annotation

RLE = {"size": [4, 4], "counts": [4, 4, 8]}


def _record(loc_id: int) -> LocationRecord:
    return LocationRecord(
        id=loc_id, z=1, wkt="POLYGON((0 0,4 0,4 4,0 4,0 0))", parent_id=None, off_edge=False,
        last_modified=datetime(2020, 1, 1), type_id=1, type_name="Cell", structure_label=None,
        type_code=LocationType.POLYGON,
    )


def _setup(tmp_path: Path) -> tuple[Path, str]:
    """Image json with ids 7 (windowed) and 8, plus a new RLE for id 7."""
    images = tmp_path / "images"
    images.mkdir()
    old = sa1b_annotation(_record(7), rle={"size": [4, 4], "counts": [16]}, bbox=[0, 0, 0, 0], area=0, window_index=2, window_count=3)
    other = sa1b_annotation(_record(8), rle={"size": [4, 4], "counts": [16]}, bbox=[0, 0, 0, 0], area=0)
    (images / "k1.json").write_text(json.dumps({"image": {"width": 4}, "annotations": [old, other]}), encoding="utf-8")
    rle_path = tmp_path / "k1_7.json"
    rle_path.write_text(json.dumps(RLE), encoding="utf-8")
    return rle_path, "k1"


def _expected(tmp_path: Path) -> str:
    bbox, area = bbox_area_from_rle(RLE)
    old = sa1b_annotation(_record(7), rle={"size": [4, 4], "counts": [16]}, bbox=[0, 0, 0, 0], area=0, window_index=2, window_count=3)
    other = sa1b_annotation(_record(8), rle={"size": [4, 4], "counts": [16]}, bbox=[0, 0, 0, 0], area=0)
    new = sa1b_annotation(_record(7), rle=RLE, bbox=bbox, area=area, window_index=2, window_count=3)
    assert old["id"] == new["id"]
    return json.dumps({"image": {"width": 4}, "annotations": [new, other]})


class _Products:
    def __init__(self) -> None:
        self.noted: list[Any] = []

    def note_path(self, path: Any) -> None:
        self.noted.append(path)


def test_maskrefresh_replace_keeps_window_and_other_rows(tmp_path: Path) -> None:
    rle_path, key = _setup(tmp_path)
    bbox, area = bbox_area_from_rle(RLE)
    results = [{"image_key": key, "rle_path": str(rle_path), "bbox": bbox, "area": area}]
    maskrefresh._replace_annotations(tmp_path, results, _record(7))
    assert (tmp_path / "images" / "k1.json").read_text(encoding="utf-8") == _expected(tmp_path)


def test_maskrefresh_replace_skips_missing_json_and_rle(tmp_path: Path) -> None:
    rle_path, key = _setup(tmp_path)
    before = (tmp_path / "images" / "k1.json").read_text(encoding="utf-8")
    results = [
        {"image_key": "nope", "rle_path": str(rle_path), "bbox": [0, 0, 1, 1], "area": 1},
        {"image_key": key, "rle_path": str(tmp_path / "missing.json"), "bbox": [0, 0, 1, 1], "area": 1},
    ]
    maskrefresh._replace_annotations(tmp_path, results, _record(7))
    assert (tmp_path / "images" / "k1.json").read_text(encoding="utf-8") == before
    assert not (tmp_path / "images" / "nope.json").exists()


def test_update_replace_keeps_window_and_other_rows(tmp_path: Path) -> None:
    rle_path, key = _setup(tmp_path)
    job = MaskJob(location_id=7, image_key=key, width=4, height=4, rings=(), mask_path=str(tmp_path / "m.png"), rle_path=str(rle_path))
    products = _Products()
    update._replace_changed_annotations(tmp_path, [job], {7: _record(7)}, products, False)  # type: ignore[arg-type]
    json_path = tmp_path / "images" / "k1.json"
    assert json_path.read_text(encoding="utf-8") == _expected(tmp_path)
    assert products.noted == [json_path]


def test_update_replace_skips_missing_json_and_rle(tmp_path: Path) -> None:
    rle_path, key = _setup(tmp_path)
    before = (tmp_path / "images" / "k1.json").read_text(encoding="utf-8")
    jobs = [
        MaskJob(location_id=7, image_key="nope", width=4, height=4, rings=(), mask_path="m", rle_path=str(rle_path)),
        MaskJob(location_id=7, image_key=key, width=4, height=4, rings=(), mask_path="m", rle_path=str(tmp_path / "missing.json")),
    ]
    products = _Products()
    update._replace_changed_annotations(tmp_path, jobs, {7: _record(7)}, products, False)  # type: ignore[arg-type]
    assert (tmp_path / "images" / "k1.json").read_text(encoding="utf-8") == before
