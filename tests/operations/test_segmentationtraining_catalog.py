"""Catalog sqlite, ignore.json, Radius ingest, and SAM2 column preservation."""

from __future__ import annotations

import json
import math
import sqlite3
import tempfile
import xml.etree.ElementTree as ETree
from datetime import datetime, timezone
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st
from PIL import Image

from nornir_buildmanager.operations.segmentationtraining.catalog import (
    apply_ignore_moves,
    equivalent_radius,
    ignore_location,
    list_catalog_rows,
    load_ignore_ids,
    rebuild_catalog,
    restore_location,
    save_ignore_ids,
    sqlite_path,
    upsert_catalog,
    upsert_sam2_scores,
)
from nornir_buildmanager.operations.segmentationtraining.ingest import (
    iter_odata_locations,
    location_from_odata_entity,
)
from nornir_buildmanager.operations.segmentationtraining.pipeline import (
    RepairAnnotationOverlays,
)
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord
from nornir_buildmanager.operations.segmentationtraining.sam2 import WriteGallery
from nornir_buildmanager.operations.segmentationtraining.score import (
    ScoreAnnotationCrops,
    box_iou,
)

_PIPELINES = (
    Path(__file__).resolve().parents[2]
    / "nornir_buildmanager"
    / "config"
    / "Pipelines.xml"
)
_WKT = "POLYGON((0 0,10 0,10 10,0 10,0 0))"
_MODIFIED = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _record(*, location_id: int = 42, z: int = 17, radius: float | None = 12.5) -> LocationRecord:
    return LocationRecord(
        id=location_id,
        z=z,
        wkt=_WKT,
        parent_id=7,
        off_edge=False,
        last_modified=_MODIFIED,
        type_id=1,
        type_name="Cell",
        structure_label="soma",
        type_code=4,
        radius=radius,
    )


def _mini_crops(tmp_path: Path, *, location_id: int = 42, z: int = 17) -> Path:
    output = tmp_path / "AnnotationCrops"
    images = output / "images"
    masks = output / "masks"
    work = output / "_work"
    images.mkdir(parents=True)
    masks.mkdir()
    work.mkdir()
    key = f"RC2_{z}_D1_X0_Y0"
    Image.new("L", (16, 16), 20).save(images / f"{key}.png")
    mask = Image.new("L", (16, 16), 0)
    for x in range(4, 12):
        for y in range(4, 12):
            mask.putpixel((x, y), 255)
    mask.save(masks / f"{key}_{location_id}.png")
    (images / f"{key}.json").write_text(
        json.dumps(
            {
                "image": {"file_name": f"{key}.png", "width": 16, "height": 16},
                "annotations": [
                    {
                        "id": location_id,
                        "area": 64,
                        "category_id": 1,
                        "category_name": "soma",
                        "structure_label": "soma",
                        "type_name": "Cell",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    record = _record(location_id=location_id, z=z)
    (work / f"section_{z}.jsonl").write_text(
        json.dumps(record.to_json()) + "\n", encoding="utf-8"
    )
    return output


def test_location_record_round_trips_radius() -> None:
    payload = _record().to_json()
    assert payload["radius"] == 12.5
    loaded = LocationRecord.from_json(payload)
    assert loaded.radius == 12.5


def test_location_from_odata_entity_maps_radius() -> None:
    entity = {
        "ID": 9,
        "Z": 3,
        "TypeCode": 4,
        "OffEdge": False,
        "LastModified": "2020-01-01T00:00:00Z",
        "Radius": 8.25,
        "MosaicShape": {"WellKnownText": _WKT},
        "Parent": {"ID": 1, "TypeID": 1, "Label": "soma", "Type": {"ID": 1, "Name": "Cell"}},
    }
    record = location_from_odata_entity(entity, include_off_edge=False)
    assert record is not None
    assert record.radius == 8.25


def test_odata_pass_b_selects_radius(tmp_path: Path) -> None:
    urls: list[str] = []

    def http_get(url: str) -> dict:
        urls.append(url)
        if "MosaicShape" in url:
            return {"value": []}
        return {
            "value": [
                {
                    "ID": 1,
                    "Z": 5,
                    "LastModified": "2020-01-01T00:00:00Z",
                    "ParentID": 2,
                    "OffEdge": False,
                }
            ]
        }

    list(
        iter_odata_locations(
            "http://host/vol/OData",
            filter_text="TypeCode eq 4",
            include_off_edge=False,
            sections=[5],
            http_get=http_get,
            output_path=tmp_path,
            force=True,
        )
    )
    pass_b = [url for url in urls if "MosaicShape" in url]
    assert pass_b
    assert "Radius" in pass_b[0]


def test_equivalent_radius_prefers_stored() -> None:
    assert equivalent_radius(100.0, 3.0) == 3.0
    assert equivalent_radius(math.pi * 16.0, None) == pytest.approx(4.0)


def test_ignore_moves_mask_and_catalog(tmp_path: Path) -> None:
    output = _mini_crops(tmp_path)
    rebuild_catalog(output)
    key = "RC2_17_D1_X0_Y0"
    mask_name = f"{key}_42.png"
    ignore_location(output, 42)
    assert load_ignore_ids(output) == {42}
    assert not (output / "masks" / mask_name).is_file()
    assert (output / "ignored" / mask_name).is_file()
    rows = list_catalog_rows(output)
    assert rows[0]["ignored"] == 1
    restore_location(output, 42)
    assert load_ignore_ids(output) == set()
    assert (output / "masks" / mask_name).is_file()
    assert list_catalog_rows(output)[0]["ignored"] == 0


@given(st.lists(st.integers(min_value=1, max_value=10_000), unique=True, max_size=15))
@settings(max_examples=20, deadline=None)
def test_ignore_json_roundtrip(ids: list[int]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp)
        save_ignore_ids(output, ids)
        assert load_ignore_ids(output) == set(ids)


def test_write_gallery_rebuilds_sqlite_not_html_list(tmp_path: Path) -> None:
    output = _mini_crops(tmp_path)
    WriteGallery(OutputPath=str(output))
    assert sqlite_path(output).is_file()
    rows = list_catalog_rows(output)
    assert len(rows) == 1
    assert rows[0]["location_id"] == 42
    assert rows[0]["radius"] == pytest.approx(12.5)
    assert rows[0]["image_relpath"] == "images/RC2_17_D1_X0_Y0.png"
    assert "jpeg_relpath" not in rows[0]
    index = output / "index.html"
    if index.is_file():
        assert "<ul>" not in index.read_text(encoding="utf-8")


def test_catalog_renames_legacy_jpeg_relpath(tmp_path: Path) -> None:
    output = tmp_path / "AnnotationCrops"
    output.mkdir()
    db = sqlite_path(output)
    connection = sqlite3.connect(str(db))
    connection.execute(
        "CREATE TABLE locations ("
        "location_id INTEGER PRIMARY KEY, z INTEGER NOT NULL, image_key TEXT NOT NULL, "
        "jpeg_relpath TEXT, ignored INTEGER NOT NULL DEFAULT 0)"
    )
    connection.execute(
        "INSERT INTO locations (location_id, z, image_key, jpeg_relpath) VALUES (1, 3, 'k', 'images/k.jpg')"
    )
    connection.commit()
    connection.close()
    row = list_catalog_rows(output)[0]
    assert row["image_relpath"] == "images/k.jpg"
    assert "jpeg_relpath" not in row


def test_sam2_columns_survive_repair(tmp_path: Path) -> None:
    output = _mini_crops(tmp_path)
    rebuild_catalog(output)
    upsert_sam2_scores(
        output,
        42,
        {
            "sam2PredIou": 0.4,
            "sam2ObjectScore": 0.5,
            "sam2Stability": 0.6,
            "sam2GtIou": 0.91,
            "sam2Checkpoint": "ckpt",
            "sam2ScoredAt": "2026-01-01T00:00:00+00:00",
        },
    )
    RepairAnnotationOverlays(OutputPath=str(output))
    row = list_catalog_rows(output)[0]
    assert row["sam2GtIou"] == pytest.approx(0.91)
    assert row["sam2Checkpoint"] == "ckpt"
    assert (output / "overlays" / "RC2_17_D1_X0_Y0.png").is_file()


def test_upsert_catalog_does_not_wipe_sam2(tmp_path: Path) -> None:
    output = _mini_crops(tmp_path)
    rebuild_catalog(output)
    upsert_sam2_scores(
        output,
        42,
        {
            "sam2PredIou": None,
            "sam2ObjectScore": None,
            "sam2Stability": None,
            "sam2GtIou": 0.33,
            "sam2Checkpoint": None,
            "sam2ScoredAt": None,
        },
    )
    upsert_catalog(output, [42])
    assert list_catalog_rows(output)[0]["sam2GtIou"] == pytest.approx(0.33)


def test_score_annotation_crops_stub_predictor(tmp_path: Path) -> None:
    output = _mini_crops(tmp_path)
    rebuild_catalog(output)

    def predictor(**kwargs: object) -> dict[str, object]:
        del kwargs
        return {
            "sam2PredIou": 0.8,
            "sam2ObjectScore": 0.7,
            "sam2Stability": 0.9,
            "sam2GtIou": 0.85,
            "sam2Checkpoint": "stub",
        }

    ScoreAnnotationCrops(OutputPath=str(output), Checkpoint="stub", Predictor=predictor)
    row = list_catalog_rows(output)[0]
    assert row["sam2GtIou"] == pytest.approx(0.85)
    assert row["sam2Checkpoint"] == "stub"
    assert row["sam2ScoredAt"]


def test_box_iou_identity_and_disjoint() -> None:
    assert box_iou([0, 0, 10, 10], [0, 0, 10, 10]) == pytest.approx(1.0)
    assert box_iou([0, 0, 2, 2], [5, 5, 2, 2]) == 0.0


def test_pipelines_xml_gallery_and_score() -> None:
    root = ETree.parse(_PIPELINES).getroot()
    names = {node.get("Name") for node in root.findall("Pipeline")}
    assert "ScoreAnnotationCrops" in names
    assert "RepairAnnotationOverlays" in names
    xml = _PIPELINES.read_text(encoding="utf-8")
    assert "ForceOverlays" not in xml
    write = root.find("Pipeline[@Name='ExportAnnotationCrops']/PythonCall[@Function='WriteGallery']")
    assert write is not None
    assert "segmentationtraining.sam2" in (write.get("Module") or "")
