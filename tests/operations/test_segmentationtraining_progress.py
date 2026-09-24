"""Tests for ExportAnnotationCrops iterate_progress telemetry."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import numpy as np
import pytest
from PIL import Image

from nornir_buildmanager.operations.segmentationtraining.catalog import rebuild_catalog
from nornir_buildmanager.operations.segmentationtraining.freshness import ExportParams
from nornir_buildmanager.operations.segmentationtraining.ingest import (
    ingest_to_section_files,
)
from nornir_buildmanager.operations.segmentationtraining.pipeline import (
    ExportSectionCrops,
    export_section_crops,
)
from nornir_buildmanager.operations.segmentationtraining.poolutil import run_process_jobs
from nornir_buildmanager.operations.segmentationtraining.progress import (
    GALLERY_TRACK_ID,
    INGEST_TRACK_ID,
    MASKS_TRACK_ID,
    SECTIONS_TRACK_ID,
    STITCH_TRACK_ID,
)
from nornir_buildmanager.operations.segmentationtraining.records import (
    LocationRecord,
    LocationType,
)
from nornir_buildmanager.operations.segmentationtraining.sam2 import WriteGallery
from nornir_buildmanager.operations.segmentationtraining.stitch import tile_filename


class _RecordingReporter:
    """Stand-in for TaskProgressReporter that records start/update/complete."""

    instances: list[_RecordingReporter] = []

    def __init__(
        self,
        task_key: str,
        total: int,
        *,
        name: str | None = None,
        min_interval_s: float = 0.25,
        section: int | str | None = None,
    ) -> None:
        del min_interval_s
        self.task_key = task_key
        self.total = total
        self.name = name
        self.section = section
        self.updates: list[int] = []
        self.started = False
        self.completed = False
        type(self).instances.append(self)

    def start(self) -> None:
        self.started = True

    def update(self, current: int, **kwargs: Any) -> None:
        del kwargs
        self.updates.append(int(current))

    def complete(self) -> None:
        self.completed = True


def _box_wkt(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def _record(
    loc_id: int,
    z: int,
    wkt: str,
    *,
    last_modified: str = "2020-01-01T00:00:00+00:00",
    label: str | None = "soma",
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
        structure_label=label,
        type_code=LocationType.POLYGON,
    )


def _entity(
    loc_id: int,
    z: int,
    wkt: str,
    *,
    last_modified: str = "2020-01-01T00:00:00Z",
) -> dict[str, Any]:
    return {
        "ID": loc_id,
        "Z": z,
        "ParentID": 10,
        "TypeCode": LocationType.POLYGON,
        "OffEdge": False,
        "LastModified": last_modified,
        "MosaicShape": {"Geometry": {"WellKnownText": wkt}},
        "Parent": {
            "ID": 10,
            "TypeID": 1,
            "Label": "soma",
            "Type": {"ID": 1, "Name": "Cell", "ParentID": None},
        },
    }


def _getter(entities: list[dict[str, Any]]) -> Any:
    calls: list[str] = []

    def getter(url: str) -> dict[str, Any]:
        calls.append(url)
        decoded = unquote(url)
        if "MosaicShape" in decoded:
            return {"value": entities, "@odata.count": len(entities)}
        thin = [
            {key: item[key] for key in ("ID", "Z", "LastModified", "ParentID", "OffEdge")}
            for item in entities
        ]
        return {"value": thin, "@odata.count": len(thin)}

    getter.calls = calls  # type: ignore[attr-defined]
    return getter


def _track_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    def report_iterate(
        track_id: str,
        current: int,
        total: int,
        label: str,
        depth: int = 0,
        **fields: Any,
    ) -> None:
        events.append(
            (
                "iterate_progress",
                {
                    "track_id": track_id,
                    "current": current,
                    "total": total,
                    "label": label,
                    "depth": depth,
                    **fields,
                },
            )
        )

    def report_iterate_complete(track_id: str, total: int) -> None:
        events.append(
            (
                "iterate_progress_complete",
                {"track_id": str(track_id), "total": int(total)},
            )
        )

    monkeypatch.setattr(
        "nornir_buildmanager.operations.segmentationtraining.progress.report_iterate",
        report_iterate,
    )
    monkeypatch.setattr(
        "nornir_buildmanager.operations.segmentationtraining.progress.report_iterate_complete",
        report_iterate_complete,
    )
    monkeypatch.setattr(
        "nornir_buildmanager.operations.segmentationtraining.pipeline.report_iterate",
        report_iterate,
        raising=False,
    )
    monkeypatch.setattr(
        "nornir_buildmanager.operations.segmentationtraining.sam2.report_iterate_complete",
        report_iterate_complete,
        raising=False,
    )
    return events


def _events_for(
    events: list[tuple[str, dict[str, Any]]],
    track_id: str,
    event_type: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    matched = [item for item in events if item[1].get("track_id") == track_id]
    if event_type is None:
        return matched
    return [item for item in matched if item[0] == event_type]


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
                "annotations": [{"id": location_id, "area": 64}],
            }
        ),
        encoding="utf-8",
    )
    record = _record(location_id, z, _box_wkt(0, 0, 10, 10))
    second_id = location_id + 1
    second_key = f"RC2_{z}_D1_X1_Y0"
    Image.new("L", (16, 16), 20).save(images / f"{second_key}.png")
    mask.save(masks / f"{second_key}_{second_id}.png")
    (images / f"{second_key}.json").write_text(
        json.dumps(
            {
                "image": {"file_name": f"{second_key}.png", "width": 16, "height": 16},
                "annotations": [{"id": second_id, "area": 64}],
            }
        ),
        encoding="utf-8",
    )
    second = _record(second_id, z, _box_wkt(16, 0, 26, 10))
    (work / f"section_{z}.jsonl").write_text(
        json.dumps(record.to_json()) + "\n" + json.dumps(second.to_json()) + "\n",
        encoding="utf-8",
    )
    return output


def test_odata_ingest_reports_count_and_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _track_events(monkeypatch)
    entities = [
        _entity(1, 4, _box_wkt(0, 0, 4, 4)),
        _entity(2, 5, _box_wkt(0, 0, 4, 4)),
    ]
    ingest_to_section_files(
        output_path=tmp_path / "out",
        odata="http://example/odata",
        geometries=None,
        http_get=_getter(entities),
    )
    progress = _events_for(events, INGEST_TRACK_ID, "iterate_progress")
    completes = _events_for(events, INGEST_TRACK_ID, "iterate_progress_complete")
    totals = {item[1]["total"] for item in progress}
    assert 2 in totals
    assert progress
    assert all(item[1]["depth"] == 0 for item in progress)
    assert completes
    assert completes[-1][1]["total"] == 2
    meta = json.loads(
        (tmp_path / "out" / "_work" / "cache.meta.json").read_text(encoding="utf-8")
    )
    assert meta["sections"] == [4, 5]


def test_geometries_ingest_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _track_events(monkeypatch)
    rows = [
        _entity(1, 3, _box_wkt(0, 0, 4, 4)),
        _entity(2, 3, _box_wkt(5, 5, 9, 9)),
        _entity(3, 8, _box_wkt(0, 0, 4, 4)),
    ]
    dump = tmp_path / "dump.jsonl"
    dump.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    ingest_to_section_files(
        output_path=tmp_path / "out",
        odata=None,
        geometries=dump,
    )
    progress = _events_for(events, INGEST_TRACK_ID, "iterate_progress")
    completes = _events_for(events, INGEST_TRACK_ID, "iterate_progress_complete")
    assert any(item[1]["total"] == 3 for item in progress)
    assert any(item[1]["current"] == 3 for item in progress)
    assert completes
    assert completes[0][1]["total"] == 3


def test_export_section_crops_reports_section_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _track_events(monkeypatch)
    work = tmp_path / "_work"
    work.mkdir()
    (work / "cache.meta.json").write_text(
        json.dumps({"sections": [4, 7]}), encoding="utf-8"
    )

    class _Section:
        Number = 4

    ExportSectionCrops(OutputPath=str(tmp_path), section_node=_Section())
    progress = _events_for(events, SECTIONS_TRACK_ID, "iterate_progress")
    currents = [item[1]["current"] for item in progress]
    assert currents[0] == 0
    assert currents[-1] == 1
    assert all(item[1]["total"] == 2 for item in progress)
    assert all(item[1]["depth"] == 0 for item in progress)
    assert all(item[1]["section"] == 4 for item in progress)


def test_export_reports_mask_and_stitch_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _RecordingReporter.instances = []
    monkeypatch.setattr(
        "nornir_buildmanager.operations.segmentationtraining.pipeline.prettyoutput.TaskProgressReporter",
        _RecordingReporter,
    )
    level = tmp_path / "tiles" / "001"
    level.mkdir(parents=True)
    for ix in (0, 1):
        Image.fromarray(np.full((8, 8), 128, dtype=np.uint8), mode="L").save(
            level / tile_filename("", ".png", ix, 0)
        )
    records = [
        _record(11, 7, _box_wkt(1, 1, 6, 6)),
        _record(12, 7, _box_wkt(9, 1, 14, 6)),
    ]
    params = ExportParams(
        pad=0.0,
        downsample=1,
        max_texture=8,
        min_process_pixels=1,
        include_off_edge=False,
        channel="TEM",
        filter_name="Leveled",
        volume="TestVolume",
    )
    keys = export_section_crops(
        output_path=tmp_path / "export",
        z=7,
        records=records,
        tile_x_dim=8,
        tile_y_dim=8,
        available=[1],
        level_dirs={1: str(level)},
        file_prefix="",
        file_postfix=".png",
        params=params,
        tileset_mtime=1.0,
        max_tiles_x=8,
        max_tiles_y=8,
        workers=1,
        mask_workers=1,
        stage_tiles=None,
        overlay=False,
        force=False,
    )
    assert len(keys) == 2
    by_key = {item.task_key: item for item in _RecordingReporter.instances}
    assert MASKS_TRACK_ID in by_key
    assert STITCH_TRACK_ID in by_key
    masks = by_key[MASKS_TRACK_ID]
    stitch = by_key[STITCH_TRACK_ID]
    assert masks.total == 2
    assert stitch.total == 2
    assert masks.started and masks.completed
    assert stitch.started and stitch.completed
    assert masks.section == 7
    assert stitch.section == 7
    assert masks.updates[-1] == 2
    assert stitch.updates[-1] == 2


def test_rebuild_catalog_reports_gallery_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _track_events(monkeypatch)
    output = _mini_crops(tmp_path)
    count = rebuild_catalog(output)
    assert count == 2
    progress = _events_for(events, GALLERY_TRACK_ID, "iterate_progress")
    completes = _events_for(events, GALLERY_TRACK_ID, "iterate_progress_complete")
    assert any(item[1]["total"] == 2 for item in progress)
    assert any(item[1]["current"] == 2 for item in progress)
    assert all(item[1]["depth"] == 0 for item in progress)
    assert completes
    assert completes[0][1]["total"] == 2


def test_write_gallery_completes_sections_track(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _track_events(monkeypatch)
    output = _mini_crops(tmp_path)
    (output / "_work" / "cache.meta.json").write_text(
        json.dumps({"sections": [17]}), encoding="utf-8"
    )
    WriteGallery(OutputPath=str(output))
    completes = _events_for(events, SECTIONS_TRACK_ID, "iterate_progress_complete")
    assert completes
    assert completes[0][1]["total"] == 1


def test_run_process_jobs_invokes_on_complete() -> None:
    seen: list[int] = []
    results = run_process_jobs(
        lambda value: value * 2,
        [1, 2, 3],
        workers=1,
        name_prefix="progress-test",
        on_complete=seen.append,
    )
    assert results == [2, 4, 6]
    assert seen == [1, 2, 3]
