"""Characterization tests for how export_section_crops sorts plans into rebuild work.

Pins, through the public ``export_section_crops`` entry, which plans get stitch
jobs and mask jobs, the (plan, mode) list handed to the rebuild-plan log, the
manifest row order, and the tracked mask-job run.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from nornir_imageregistration.type_info import Shape
from PIL import Image

from nornir_buildmanager.operations.segmentationtraining import pipeline as pipeline_mod
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    EXPORTER_TILED,
    REBUILD_FULL,
    REBUILD_MASK_ONLY,
    REBUILD_SKIP,
    ExportParams,
    ResolvedTileset,
    SectionCropRun,
    load_section_watermark,
)
from nornir_buildmanager.operations.segmentationtraining.pipeline import (
    export_section_crops,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    IMAGES_FOLDER,
    get_product_index,
)
from nornir_buildmanager.operations.segmentationtraining.records import (
    LocationRecord,
    LocationType,
)
from nornir_buildmanager.operations.segmentationtraining.stitch import tile_filename

_TILE = 8
_TILE_COLUMNS = (0, 4, 8, 12)
_Z = 2


def _record(loc_id: int, tile_ix: int) -> LocationRecord:
    """A small square polygon inside tile column *tile_ix*."""
    x0 = tile_ix * _TILE + 1
    x1 = x0 + 3
    return LocationRecord(
        id=loc_id,
        z=_Z,
        wkt=f"POLYGON(({x0} 1,{x1} 1,{x1} 4,{x0} 4,{x0} 1))",
        parent_id=10,
        off_edge=False,
        last_modified=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
        type_id=1,
        type_name="Cell",
        structure_label=None,
        type_code=LocationType.POLYGON,
    )


def _run(level: Path) -> SectionCropRun:
    """A one-worker tiled run over *level* with 8-pixel tiles and crops."""
    params = ExportParams(
        pad=0.0,
        downsample=1,
        max_texture=8,
        min_process_pixels=16,
        include_off_edge=False,
        channel="TEM",
        filter_name="Leveled",
        volume="TestVolume",
        tile_shape=Shape.from_xy(x=0, y=0),
    )
    tileset = ResolvedTileset(
        tile_shape=Shape.from_xy(x=_TILE, y=_TILE),
        available=[1],
        level_dirs={1: str(level)},
        prefix="",
        postfix=".png",
        mtime=3.0,
    )
    return SectionCropRun(
        params=params,
        tileset=tileset,
        exporter=EXPORTER_TILED,
        max_tiles_x=8,
        max_tiles_y=8,
        workers=1,
        mask_workers=1,
        stage_tiles=None,
        overlay=False,
    )


class _Spy:
    """Records the work export_section_crops hands to its stages, then runs them."""

    stitch_jobs: list[Any]
    mask_calls: list[tuple[list[Any], dict[str, Any]]]
    join_specs: list[tuple[str, str]]
    manifest_keys: list[str]
    reporters: list[tuple[tuple[Any, ...], dict[str, Any]]]
    mask_results: list[dict[str, Any]]
    annotation_stats: list[tuple[int, Any, int]]

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.stitch_jobs = []
        self.mask_calls = []
        self.join_specs = []
        self.manifest_keys = []
        self.reporters = []
        self.mask_results = []
        self.annotation_stats = []
        real_stitch = pipeline_mod._run_stitch_jobs
        real_masks = pipeline_mod.run_mask_jobs
        real_log = pipeline_mod._log_section_rebuild_plan
        real_manifest = pipeline_mod.replace_manifest_rows
        real_reporter = pipeline_mod.prettyoutput.TaskProgressReporter
        real_annotation = pipeline_mod.sa1b_annotation

        def stitch(jobs: list[Any], **kwargs: Any) -> Any:
            self.stitch_jobs.extend(jobs)
            return real_stitch(jobs, **kwargs)

        def masks(jobs: list[Any], **kwargs: Any) -> Any:
            self.mask_calls.append((list(jobs), dict(kwargs)))
            results = real_masks(jobs, **kwargs)
            self.mask_results.extend(results)
            return results

        def log(z: int, specs: list[Any]) -> None:
            self.join_specs.extend((plan.image_key, mode) for plan, mode in specs)
            real_log(z, specs)

        def manifest(output: Path, z: int, rows: list[dict[str, Any]]) -> None:
            self.manifest_keys.extend(str(row["imageKey"]) for row in rows)
            real_manifest(output, z, rows)

        def reporter(*args: Any, **kwargs: Any) -> Any:
            self.reporters.append((args, kwargs))
            return real_reporter(*args, **kwargs)

        def annotation(record: Any, **kwargs: Any) -> Any:
            self.annotation_stats.append((record.id, kwargs["bbox"], kwargs["area"]))
            return real_annotation(record, **kwargs)

        monkeypatch.setattr(pipeline_mod, "_run_stitch_jobs", stitch)
        monkeypatch.setattr(pipeline_mod, "sa1b_annotation", annotation)
        monkeypatch.setattr(pipeline_mod, "run_mask_jobs", masks)
        monkeypatch.setattr(pipeline_mod, "_log_section_rebuild_plan", log)
        monkeypatch.setattr(pipeline_mod, "replace_manifest_rows", manifest)
        monkeypatch.setattr(pipeline_mod.prettyoutput, "TaskProgressReporter", reporter)


def _first_export(tmp_path: Path) -> tuple[Path, SectionCropRun, list[LocationRecord], list[str]]:
    """Export four one-member crops from scratch; return output, run, records, keys."""
    level = tmp_path / "tiles" / "001"
    level.mkdir(parents=True)
    for ix in _TILE_COLUMNS:
        Image.fromarray(np.full((_TILE, _TILE), 40, dtype=np.uint8), mode="L").save(
            level / tile_filename("", ".png", ix, 0)
        )
    out = tmp_path / "export"
    run = _run(level)
    records = [_record(100 + i, ix) for i, ix in enumerate(_TILE_COLUMNS)]
    keys = export_section_crops(output_path=out, z=_Z, records=records, run=run, force=False)
    assert len(keys) == len(_TILE_COLUMNS)
    return out, run, records, keys


def _force_modes(monkeypatch: pytest.MonkeyPatch, modes: dict[str, str]) -> list[dict[str, Any]]:
    """Make image_rebuild_mode return *modes* by image key; return the recorded calls."""
    calls: list[dict[str, Any]] = []

    def fake(current: Any, previous: Any, **kwargs: Any) -> str:
        calls.append({"current": current, "previous": previous, **kwargs})
        return modes[current.key]

    monkeypatch.setattr(pipeline_mod, "image_rebuild_mode", fake)
    return calls


def test_first_export_stitches_and_masks_every_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No previous watermark: every plan is a full rebuild with one stitch and one mask job."""
    spy = _Spy(monkeypatch)
    out, run, _, keys = _first_export(tmp_path)
    assert [job.image_key for job in spy.stitch_jobs] == keys
    for job in spy.stitch_jobs:
        assert (job.crop_width, job.crop_height) == (_TILE, _TILE)
        assert job.tile_shape == run.tileset.tile_shape
        assert job.image_path == str(out / "images" / f"{job.image_key}.png")
    assert spy.join_specs == [(key, REBUILD_FULL) for key in keys]
    assert len(spy.mask_calls) == 1
    jobs, kwargs = spy.mask_calls[0]
    assert [job.image_key for job in jobs] == keys
    assert kwargs["workers"] == run.mask_workers
    assert callable(kwargs["on_complete"])
    mask_reporters = [item for item in spy.reporters if item[0][0] == pipeline_mod.MASKS_TRACK_ID]
    assert len(mask_reporters) == 1
    args, reporter_kwargs = mask_reporters[0]
    assert args[1] == len(keys)
    assert reporter_kwargs == {"name": pipeline_mod.MASKS_LABEL, "section": _Z}
    assert spy.manifest_keys == list(keys)
    # Each annotation takes its bbox and area from its own member's mask-job stats.
    assert len(spy.annotation_stats) == len(keys)
    by_member = {item["location_id"]: (item["bbox"], int(item["area"])) for item in spy.mask_results}
    assert {(job.image_key, job.location_id) for job in jobs} == {
        (item["image_key"], item["location_id"]) for item in spy.mask_results
    }
    for location_id, bbox, area in spy.annotation_stats:
        assert (bbox, area) == by_member[location_id]


def test_mixed_modes_split_into_stitch_mask_and_manifest_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip, full (missing PNG), full (PNG on disk, not forced), and mask_only plans."""
    out, run, records, keys = _first_export(tmp_path)
    previous = load_section_watermark(out, _Z)
    assert previous is not None
    missing = out / "images" / f"{keys[0]}.png"
    missing.unlink()
    get_product_index(out).forget(IMAGES_FOLDER, missing.name)
    modes = {
        keys[0]: REBUILD_FULL,
        keys[1]: REBUILD_SKIP,
        keys[2]: REBUILD_FULL,
        keys[3]: REBUILD_MASK_ONLY,
    }
    calls = _force_modes(monkeypatch, modes)
    spy = _Spy(monkeypatch)
    export_section_crops(
        output_path=out, z=_Z, records=records, run=run, force=False, previous=previous
    )
    first_stitch = {job.image_key: job for job in spy.stitch_jobs}
    assert list(first_stitch) == [keys[0]]
    job = first_stitch[keys[0]]
    assert (job.crop_x, job.crop_y, job.crop_width, job.crop_height) == (0, 0, _TILE, _TILE)
    assert spy.join_specs == [
        (keys[0], REBUILD_FULL),
        (keys[1], REBUILD_SKIP),
        (keys[2], REBUILD_MASK_ONLY),
        (keys[3], REBUILD_MASK_ONLY),
    ]
    assert len(spy.mask_calls) == 1
    assert [job.image_key for job in spy.mask_calls[0][0]] == [keys[0], keys[2], keys[3]]
    # Skip rows are appended while classifying, rebuilt rows while writing.
    assert spy.manifest_keys == [keys[1], keys[0], keys[2], keys[3]]
    assert [call["current"].key for call in calls] == keys
    params_hash = run.params.hash()
    for call, key in zip(calls, keys):
        assert call["previous"] is not None and call["previous"].key == key
        assert call["image_path"] == out / "images" / f"{key}.png"
        assert call["json_path"] == out / "images" / f"{key}.json"
        assert call["params_hash"] == params_hash
        assert call["previous_params_hash"] == previous.params_hash
        assert call["tileset_mtime"] == run.tileset.mtime
        assert call["previous_tileset_mtime"] == previous.tileset_mtime
        assert call["force"] is False
        assert call["refresh_odata"] is False
    assert (out / "images" / f"{keys[0]}.png").is_file()


def test_force_restitches_full_plan_with_png_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With force, a full plan whose PNG exists still gets a stitch job."""
    out, run, records, keys = _first_export(tmp_path)
    previous = load_section_watermark(out, _Z)
    modes = {key: REBUILD_SKIP for key in keys}
    modes[keys[2]] = REBUILD_FULL
    _force_modes(monkeypatch, modes)
    spy = _Spy(monkeypatch)
    export_section_crops(output_path=out, z=_Z, records=records, run=run, force=True, previous=previous)
    assert [job.image_key for job in spy.stitch_jobs] == [keys[2]]
    assert [key for key, mode in spy.join_specs if mode == REBUILD_FULL] == [keys[2]]
    assert [job.image_key for job in spy.mask_calls[0][0]] == [keys[2]]


def test_all_skip_runs_no_mask_jobs_and_no_reporter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When every plan is skipped, no mask reporter is created and run_mask_jobs is not called."""
    out, run, records, keys = _first_export(tmp_path)
    previous = load_section_watermark(out, _Z)
    _force_modes(monkeypatch, {key: REBUILD_SKIP for key in keys})
    spy = _Spy(monkeypatch)
    export_section_crops(output_path=out, z=_Z, records=records, run=run, force=False, previous=previous)
    assert spy.stitch_jobs == []
    assert spy.mask_calls == []
    assert spy.reporters == []
    assert spy.join_specs == [(key, REBUILD_SKIP) for key in keys]
    assert spy.manifest_keys == list(keys)


def test_mask_paths_are_noted_in_product_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the mask run, each job's mask and RLE path is noted in the product index."""
    noted: list[str] = []
    real_index = pipeline_mod.get_product_index

    def index(output: Path) -> Any:
        products = real_index(output)
        real_note = products.note_path

        def note(path: Any) -> None:
            noted.append(str(path))
            real_note(path)

        monkeypatch.setattr(products, "note_path", note)
        return products

    monkeypatch.setattr(pipeline_mod, "get_product_index", index)
    spy = _Spy(monkeypatch)
    _first_export(tmp_path)
    jobs = spy.mask_calls[0][0]
    for job in jobs:
        assert str(job.mask_path) in noted
        assert str(job.rle_path) in noted
