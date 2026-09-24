"""Tests for AnnotationCrops stale prune and -Cleanup repair."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from nornir_buildmanager.operations.segmentationtraining.catalog import (
    list_catalog_rows,
    upsert_catalog,
)
from nornir_buildmanager.operations.segmentationtraining.cleanup import (
    CleanupAnnotationCrops,
    cleanup_annotation_crops,
)
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ExportParams,
    load_section_watermark,
    section_meta_path,
)
from nornir_buildmanager.operations.segmentationtraining.pipeline import (
    ExportSectionCrops,
    IngestGeometries,
    export_section_crops,
)
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord, LocationType
from nornir_buildmanager.operations.segmentationtraining.stitch import tile_filename


def _box_wkt(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def _record(
    loc_id: int,
    z: int,
    wkt: str,
    *,
    last_modified: str = "2020-01-01T00:00:00+00:00",
) -> LocationRecord:
    return LocationRecord(
        id=loc_id,
        z=z,
        wkt=wkt,
        parent_id=10,
        off_edge=False,
        last_modified=datetime.fromisoformat(last_modified),
        type_id=1,
        type_name="Cell",
        structure_label="soma",
        type_code=LocationType.POLYGON,
    )


def _params() -> ExportParams:
    return ExportParams(
        pad=0.0,
        downsample=1,
        max_texture=8,
        min_process_pixels=1,
        include_off_edge=False,
        channel="TEM",
        filter_name="Leveled",
        volume="TestVolume",
    )


def _export_one(tmp_path: Path, record: LocationRecord) -> tuple[Path, str]:
    level = tmp_path / "tiles" / "001"
    level.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.full((8, 8), 128, dtype=np.uint8), mode="L").save(
        level / tile_filename("", ".png", 0, 0)
    )
    out = tmp_path / "export"
    keys = export_section_crops(
        output_path=out,
        z=record.z,
        records=[record],
        tile_x_dim=8,
        tile_y_dim=8,
        available=[1],
        level_dirs={1: str(level)},
        file_prefix="",
        file_postfix=".png",
        params=_params(),
        tileset_mtime=1.0,
        max_tiles_x=1,
        max_tiles_y=1,
        workers=1,
        mask_workers=1,
        stage_tiles=None,
        overlay=False,
        force=False,
    )
    assert len(keys) == 1
    return out, keys[0]


def test_export_removes_orphan_member_and_catalog_row(tmp_path: Path) -> None:
    record = _record(11, 7, _box_wkt(1, 1, 6, 6))
    out, key = _export_one(tmp_path, record)
    orphan = out / "masks" / f"{key}_99.png"
    Image.fromarray(np.zeros((8, 8), dtype=np.uint8), mode="L").save(orphan)
    upsert_catalog(out)
    assert any(row["location_id"] == 99 for row in list_catalog_rows(out))
    previous = load_section_watermark(out, 7)
    edited = _record(11, 7, _box_wkt(1, 1, 6, 6), last_modified="2021-01-01T00:00:00+00:00")
    export_section_crops(
        output_path=out,
        z=7,
        records=[edited],
        tile_x_dim=8,
        tile_y_dim=8,
        available=[1],
        level_dirs={1: str(tmp_path / "tiles" / "001")},
        file_prefix="",
        file_postfix=".png",
        params=_params(),
        tileset_mtime=1.0,
        max_tiles_x=1,
        max_tiles_y=1,
        workers=1,
        mask_workers=1,
        stage_tiles=None,
        overlay=False,
        force=False,
        previous=previous,
    )
    assert not orphan.is_file()
    ids = {row["location_id"] for row in list_catalog_rows(out)}
    assert 99 not in ids
    assert 11 in ids


def test_empty_section_removes_products_and_watermark(tmp_path: Path) -> None:
    record = _record(4, 3, _box_wkt(1, 1, 6, 6))
    out, key = _export_one(tmp_path, record)
    assert (out / "images" / f"{key}.png").is_file()
    assert section_meta_path(out, 3).is_file()

    class _Section:
        Number = 3

    ExportSectionCrops(OutputPath=str(out), section_node=_Section())
    assert not (out / "images" / f"{key}.png").is_file()
    assert not (out / "images" / f"{key}.json").is_file()
    assert not (out / "masks" / f"{key}_{record.id}.png").is_file()
    assert not section_meta_path(out, 3).is_file()
    assert list_catalog_rows(out) == []


def test_cleanup_repairs_mismatched_products(tmp_path: Path) -> None:
    images = tmp_path / "images"
    masks = tmp_path / "masks"
    overlays = tmp_path / "overlays"
    images.mkdir()
    masks.mkdir()
    overlays.mkdir()
    paired = "Vol_1_D1_X0-0_Y0-0"
    Image.fromarray(np.full((8, 8), 40, dtype=np.uint8), mode="L").save(images / f"{paired}.png")
    Image.fromarray(np.zeros((8, 8), dtype=np.uint8), mode="L").save(masks / f"{paired}_1.png")
    Image.fromarray(np.zeros((8, 8), dtype=np.uint8), mode="L").save(masks / f"{paired}_2.png")
    (images / f"{paired}.json").write_text(
        json.dumps(
            {
                "image": {"file_name": "wrong.png", "width": 2, "height": 2},
                "annotations": [{"id": 1, "area": 1}, {"id": 3, "area": 1}],
            }
        ),
        encoding="utf-8",
    )
    lone = "Vol_1_D1_X1-1_Y0-0"
    Image.fromarray(np.full((4, 4), 9, dtype=np.uint8), mode="L").save(images / f"{lone}.png")
    Image.fromarray(np.zeros((4, 4), dtype=np.uint8), mode="L").save(masks / f"{lone}_8.png")
    Image.fromarray(np.zeros((4, 4), dtype=np.uint8), mode="L").save(overlays / f"{lone}.png")

    summary = cleanup_annotation_crops(tmp_path)
    assert summary.deleted_keys == 1
    assert summary.rewritten_json == 1
    assert not (images / f"{lone}.png").is_file()
    assert not (masks / f"{lone}_8.png").is_file()
    assert not (overlays / f"{lone}.png").is_file()
    assert not (masks / f"{paired}_2.png").is_file()
    payload = json.loads((images / f"{paired}.json").read_text(encoding="utf-8"))
    assert payload["image"]["file_name"] == f"{paired}.png"
    assert payload["image"]["width"] == 8
    assert payload["image"]["height"] == 8
    assert [item["id"] for item in payload["annotations"]] == [1]
    assert (images / f"{paired}.png").is_file()
    assert (masks / f"{paired}_1.png").is_file()


def test_cleanup_flag_does_not_ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(_url: str) -> dict:
        raise AssertionError("cleanup must not fetch OData")

    monkeypatch.setattr(
        "nornir_buildmanager.operations.segmentationtraining.ingest.http_get_json",
        _fail,
    )
    IngestGeometries(OutputPath=str(tmp_path), Cleanup=True)
    assert CleanupAnnotationCrops(OutputPath=str(tmp_path), Cleanup=False) is None
    lone = tmp_path / "images"
    lone.mkdir()
    Image.fromarray(np.zeros((2, 2), dtype=np.uint8), mode="L").save(lone / "Vol_2_D1_X0-0_Y0-0.png")
    CleanupAnnotationCrops(OutputPath=str(tmp_path), Cleanup=False)
    assert (lone / "Vol_2_D1_X0-0_Y0-0.png").is_file()
    CleanupAnnotationCrops(OutputPath=str(tmp_path), Cleanup=True)
    assert not (lone / "Vol_2_D1_X0-0_Y0-0.png").is_file()
