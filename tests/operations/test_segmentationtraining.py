"""Tests for SegmentationTraining ingest, freshness, crops, and pools."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

import numpy as np
import pytest
from hypothesis import example, given, settings, strategies as st
from PIL import Image

from nornir_imageregistration.computational_lib import ComputationLib
from nornir_shared.reflection import get_module_class

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.segmentationtraining.catalog import (
    ignore_location,
    list_catalog_rows,
    rebuild_catalog,
    upsert_catalog,
)
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    EXPORTER_LEGACY,
    GEOMETRY_LEGACY,
    GEOMETRY_TILED,
    ExportParams,
    SectionWatermark,
    geometry_version_for,
    save_section_watermark,
    section_is_fresh,
)
from nornir_buildmanager.operations.segmentationtraining.geometry import (
    choose_downsample,
    pixel_rings_in_crop,
    place_mask_windows,
    window_tile_rect,
)
from nornir_buildmanager.operations.segmentationtraining.grouping import (
    group_snaps,
    sanitize_volume_token,
)
from nornir_buildmanager.operations.segmentationtraining.ingest import (
    ODATA_FILTER_NODE_MARGIN,
    ODATA_MAX_FILTER_NODES,
    _chunk_z_for_odata_filter,
    _combined_z_filter,
    _odata_z_filter,
    estimate_odata_filter_nodes,
    ingest_to_section_files,
    load_section_records,
)
from nornir_buildmanager.operations.segmentationtraining.masks import encode_coco_rle
from nornir_buildmanager.operations.segmentationtraining.pipeline import (
    ExportSectionCrops,
    IngestGeometries,
    RepairAnnotationOverlays,
    _resolve_tileset,
    _resolve_volume_name,
    export_section_crops,
    force_numpy_computation,
    repair_overlays,
)
from nornir_buildmanager.operations.segmentationtraining.cleanup import CleanupAnnotationCrops
from nornir_buildmanager.operations.segmentationtraining.planning import plan_section_crops
from nornir_buildmanager.operations.segmentationtraining.poolutil import submit_bounded
from nornir_buildmanager.operations.segmentationtraining.records import (
    DEFAULT_ODATA_FILTER,
    LocationRecord,
    LocationType,
)
from nornir_buildmanager.operations.segmentationtraining.sam2.write import (
    _OVERLAY_COLOR_ALPHA,
    _OVERLAY_COLORS,
    hcl_to_rgb,
    max_chroma_at_luma,
    perceptual_luma,
    rgb_to_hcl,
    write_overlay,
)
from nornir_buildmanager.operations.segmentationtraining.stitch import (
    DEFAULT_STAGE_TILES_ROOT,
    StitchJob,
    downsample_stage_dir,
    probe_tile_pixel_size,
    resolve_section_stage_root,
    stitch_from_loader,
    sweep_stitch_jobs,
    tile_filename,
    wait_stage_cleanup,
)

_PIPELINES = (
    Path(__file__).resolve().parents[2]
    / "nornir_buildmanager"
    / "config"
    / "Pipelines.xml"
)


def _box_wkt(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def _record(
    loc_id: int,
    z: int,
    wkt: str,
    *,
    last_modified: str = "2020-01-01T00:00:00+00:00",
    label: str | None = None,
    type_name: str | None = "Cell",
) -> LocationRecord:
    return LocationRecord(
        id=loc_id,
        z=z,
        wkt=wkt,
        parent_id=10,
        off_edge=False,
        last_modified=datetime.fromisoformat(last_modified),
        type_id=1,
        type_name=type_name,
        structure_label=label,
        type_code=LocationType.POLYGON,
    )


def _entity(
    loc_id: int,
    z: int,
    wkt: str,
    *,
    last_modified: str = "2020-01-01T00:00:00Z",
    label: str | None = None,
    type_name: str | None = "Cell",
    off_edge: bool = False,
    type_code: int = LocationType.POLYGON,
) -> dict:
    return {
        "ID": loc_id,
        "Z": z,
        "ParentID": 10,
        "TypeCode": type_code,
        "OffEdge": off_edge,
        "LastModified": last_modified,
        "MosaicShape": {"Geometry": {"WellKnownText": wkt}},
        "Parent": {
            "ID": 10,
            "TypeID": 1,
            "Label": label,
            "Type": {"ID": 1, "Name": type_name, "ParentID": None},
        },
    }


def _getter(entities: list[dict]) -> object:
    calls: list[str] = []

    def getter(url: str) -> dict:
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


def _params(**overrides) -> ExportParams:
    payload = dict(
        pad=0.15,
        downsample=1,
        max_texture=4096,
        min_process_pixels=16,
        include_off_edge=False,
        channel="TEM",
        filter_name="Leveled",
        volume="TestVolume",
    )
    payload.update(overrides)
    return ExportParams(**payload)


def test_pipelines_xml_registers_export_annotation_crops() -> None:
    tree = ElementTree.parse(_PIPELINES)
    pipeline = tree.find(".//Pipeline[@Name='ExportAnnotationCrops']")
    assert pipeline is not None
    functions = [node.get("Function") for node in pipeline.findall(".//PythonCall")]
    assert "segmentationtraining.IngestGeometries" in functions
    assert "segmentationtraining.ExportSectionCrops" in functions
    assert "segmentationtraining.CleanupAnnotationCrops" in functions
    assert "WriteGallery" in functions
    assert pipeline.find(".//Argument[@dest='Cleanup']") is not None
    assert pipeline.find(".//Argument[@dest='RefreshOData']") is not None
    assert pipeline.find(".//Argument[@dest='Exporter']").get("default") == "tiled"
    gallery = [node for node in pipeline.findall(".//PythonCall") if node.get("Function") == "WriteGallery"]
    assert gallery[0].get("Module") == "nornir_buildmanager.operations.segmentationtraining.sam2"
    repair = tree.find(".//Pipeline[@Name='RepairAnnotationOverlays']")
    assert repair is not None
    repair_functions = [node.get("Function") for node in repair.findall(".//PythonCall")]
    assert repair_functions == ["segmentationtraining.RepairAnnotationOverlays"]
    assert repair.find(".//Argument[@dest='ForceOverlays']") is None


def test_pipeline_entry_points_resolve() -> None:
    ingest = get_module_class(
        "nornir_buildmanager.operations",
        "segmentationtraining.IngestGeometries",
    )
    export = get_module_class(
        "nornir_buildmanager.operations",
        "segmentationtraining.ExportSectionCrops",
    )
    repair = get_module_class(
        "nornir_buildmanager.operations",
        "segmentationtraining.RepairAnnotationOverlays",
    )
    cleanup = get_module_class(
        "nornir_buildmanager.operations",
        "segmentationtraining.CleanupAnnotationCrops",
    )
    gallery = get_module_class(
        "nornir_buildmanager.operations.segmentationtraining.sam2",
        "WriteGallery",
    )
    assert ingest is IngestGeometries
    assert export is ExportSectionCrops
    assert repair is RepairAnnotationOverlays
    assert cleanup is CleanupAnnotationCrops
    assert callable(gallery)


def test_force_numpy_computation_restores_prior(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[ComputationLib] = []
    monkeypatch.setattr(
        "nornir_buildmanager.operations.segmentationtraining.pipeline.GetActiveComputationLib",
        lambda: ComputationLib.cupy,
    )
    monkeypatch.setattr(
        "nornir_buildmanager.operations.segmentationtraining.pipeline.SetActiveComputationLib",
        lambda lib: calls.append(lib),
    )
    with force_numpy_computation():
        assert calls == [ComputationLib.numpy]
    assert calls == [ComputationLib.numpy, ComputationLib.cupy]


def test_force_numpy_computation_restores_prior_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[ComputationLib] = []
    monkeypatch.setattr(
        "nornir_buildmanager.operations.segmentationtraining.pipeline.GetActiveComputationLib",
        lambda: ComputationLib.cupy,
    )
    monkeypatch.setattr(
        "nornir_buildmanager.operations.segmentationtraining.pipeline.SetActiveComputationLib",
        lambda lib: calls.append(lib),
    )
    with pytest.raises(RuntimeError, match="boom"):
        with force_numpy_computation():
            raise RuntimeError("boom")
    assert calls == [ComputationLib.numpy, ComputationLib.cupy]


def test_ingest_requires_exactly_one_source(tmp_path: Path) -> None:
    with pytest.raises(NornirUserException):
        ingest_to_section_files(output_path=tmp_path, odata=None, geometries=None)
    with pytest.raises(NornirUserException):
        ingest_to_section_files(
            output_path=tmp_path,
            odata="http://example/odata",
            geometries=tmp_path / "dump.json",
        )


def test_refresh_odata_forces_ingest_pass_b(tmp_path: Path) -> None:
    entity = _entity(9, 4, _box_wkt(0, 0, 4, 4))
    getter = _getter([entity])
    out = tmp_path / "out"
    ingest_to_section_files(
        output_path=out,
        odata="http://example/odata",
        geometries=None,
        http_get=getter,  # type: ignore[arg-type]
    )
    getter2 = _getter([entity])
    IngestGeometries(
        OutputPath=str(out),
        OData="http://example/odata",
        RefreshOData=True,
        http_get=getter2,  # type: ignore[arg-type]
    )
    assert sum("MosaicShape" in unquote(url) for url in getter2.calls) >= 1  # type: ignore[attr-defined]


def test_refresh_odata_reruns_masks_when_last_modified_changed(tmp_path: Path) -> None:
    """With -RefreshOData: same LastModified → skip; bumped LastModified → mask_only."""
    from nornir_buildmanager.operations.segmentationtraining.freshness import (
        image_rebuild_mode,
        load_section_watermark,
    )

    level = tmp_path / "tiles" / "001"
    level.mkdir(parents=True)
    Image.fromarray(np.full((8, 8), 40, dtype=np.uint8), mode="L").save(
        level / tile_filename("", ".png", 0, 0)
    )
    first = _record(8, 2, _box_wkt(1, 1, 4, 4), last_modified="2020-01-01T00:00:00+00:00")
    out = tmp_path / "export"
    params = _params(pad=0.0, max_texture=8)
    base = dict(
        output_path=out,
        z=2,
        tile_x_dim=8,
        tile_y_dim=8,
        available=[1],
        level_dirs={1: str(level)},
        file_prefix="",
        file_postfix=".png",
        params=params,
        tileset_mtime=3.0,
        max_tiles_x=8,
        max_tiles_y=8,
        workers=1,
        mask_workers=1,
        stage_tiles=None,
        overlay=False,
        force=False,
    )
    export_section_crops(**base, records=[first])  # type: ignore[arg-type]
    crop = next((out / "images").glob("*.png"))
    crop_mtime = crop.stat().st_mtime
    previous = load_section_watermark(out, 2)
    previous_mark = previous.images[0]
    image_path = out / "images" / crop.name
    json_path = out / "images" / f"{previous_mark.key}.json"

    # Same LastModified → skip (DB trigger did not fire; geometry unchanged).
    assert (
        image_rebuild_mode(
            previous_mark,
            previous_mark,
            image_path=image_path,
            json_path=json_path,
            params_hash=params.hash(),
            previous_params_hash=params.hash(),
            tileset_mtime=3.0,
            previous_tileset_mtime=3.0,
            force=False,
            refresh_odata=True,
        )
        == "skip"
    )

    # LastModified bumped (DB trigger fired on MosaicShape change) → mask_only.
    updated = _record(8, 2, _box_wkt(1, 1, 5, 5), last_modified="2021-06-01T00:00:00+00:00")
    from nornir_buildmanager.operations.segmentationtraining.pipeline import _image_watermark
    plans, _ = plan_section_crops(
        [updated],
        pad=0.0, downsample=1, available=[1], max_texture=8, min_process_pixels=16,
        tile_x_dim=8, tile_y_dim=8, max_tiles_x=8, max_tiles_y=8, volume="volume",
    )
    updated_mark = _image_watermark(plans[0], {updated.id: updated})
    assert (
        image_rebuild_mode(
            updated_mark,
            previous_mark,
            image_path=image_path,
            json_path=json_path,
            params_hash=params.hash(),
            previous_params_hash=params.hash(),
            tileset_mtime=3.0,
            previous_tileset_mtime=3.0,
            force=False,
            refresh_odata=True,
        )
        == "mask_only"
    )

    # Full export with refresh: unchanged record leaves crop PNG untouched.
    export_section_crops(**base, records=[first], previous=previous, refresh_odata=True)  # type: ignore[arg-type]
    assert crop.stat().st_mtime == crop_mtime


def test_ingest_geometries_jsonl_and_value_array(tmp_path: Path) -> None:
    entity = _entity(5, 3, _box_wkt(0, 0, 10, 10), label="")
    jsonl = tmp_path / "dump.jsonl"
    jsonl.write_text(json.dumps(entity) + "\n", encoding="utf-8")
    ingest_to_section_files(output_path=tmp_path / "out1", odata=None, geometries=jsonl)
    records = load_section_records(tmp_path / "out1", 3)
    assert len(records) == 1
    assert records[0].category_name() == "Cell"
    assert records[0].type_code == LocationType.POLYGON

    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps({"value": [entity]}), encoding="utf-8")
    ingest_to_section_files(output_path=tmp_path / "out2", odata=None, geometries=dump)
    assert load_section_records(tmp_path / "out2", 3)[0].id == 5


def test_ingest_skips_curvepolygon(tmp_path: Path) -> None:
    entity = _entity(1, 1, "CURVEPOLYGON(CIRCULARSTRING(0 0, 1 1, 0 0))")
    path = tmp_path / "dump.jsonl"
    path.write_text(json.dumps(entity) + "\n", encoding="utf-8")
    ingest_to_section_files(output_path=tmp_path / "out", odata=None, geometries=path)
    assert load_section_records(tmp_path / "out", 1) == []


def test_ingest_skips_point_polyline_circle_wkt(tmp_path: Path) -> None:
    rows = [
        _entity(1, 2, "POINT (0 0)"),
        _entity(2, 2, "LINESTRING(0 0, 10 10)"),
        _entity(3, 2, "CIRCLE((5 5), 3)"),
        _entity(4, 2, _box_wkt(0, 0, 4, 4), type_code=LocationType.POINT),
        _entity(5, 2, _box_wkt(0, 0, 4, 4)),
    ]
    path = tmp_path / "dump.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    ingest_to_section_files(output_path=tmp_path / "out", odata=None, geometries=path)
    kept = load_section_records(tmp_path / "out", 2)
    assert [record.id for record in kept] == [5]


def test_mock_odata_two_phase_and_pass_b_skip(tmp_path: Path) -> None:
    entity = _entity(9, 4, _box_wkt(0, 0, 4, 4))
    getter = _getter([entity])
    out = tmp_path / "out"
    ingest_to_section_files(
        output_path=out,
        odata="http://example/odata",
        geometries=None,
        http_get=getter,  # type: ignore[arg-type]
    )
    decoded = [unquote(url) for url in getter.calls]  # type: ignore[attr-defined]
    assert any("MosaicShape" in url for url in decoded)
    assert any("LastModified" in url and "MosaicShape" not in unquote(url) for url in getter.calls)  # type: ignore[attr-defined]
    first_pass_b = sum("MosaicShape" in unquote(url) for url in getter.calls)  # type: ignore[attr-defined]

    getter2 = _getter([entity])
    ingest_to_section_files(
        output_path=out,
        odata="http://example/odata",
        geometries=None,
        http_get=getter2,  # type: ignore[arg-type]
    )
    assert sum("MosaicShape" in unquote(url) for url in getter2.calls) == 0  # type: ignore[attr-defined]
    assert load_section_records(out, 4)[0].id == 9
    assert load_section_records(out, 4)[0].type_code == LocationType.POLYGON
    assert first_pass_b >= 1


def test_ingest_persists_curved_polygon_type_code(tmp_path: Path) -> None:
    entity = _entity(12, 2, _box_wkt(0, 0, 8, 8), type_code=LocationType.CURVEPOLYGON)
    path = tmp_path / "dump.jsonl"
    path.write_text(json.dumps(entity) + "\n", encoding="utf-8")
    ingest_to_section_files(output_path=tmp_path / "out", odata=None, geometries=path)
    records = load_section_records(tmp_path / "out", 2)
    assert records[0].type_code == LocationType.CURVEPOLYGON


def test_odata_refetches_when_cached_type_code_missing(tmp_path: Path) -> None:
    entity = _entity(9, 4, _box_wkt(0, 0, 4, 4), type_code=LocationType.CURVEPOLYGON)
    out = tmp_path / "out"
    stale = LocationRecord(
        id=9,
        z=4,
        wkt=_box_wkt(0, 0, 4, 4),
        parent_id=10,
        off_edge=False,
        last_modified=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
        type_id=1,
        type_name="Cell",
        structure_label=None,
        type_code=None,
    )
    from nornir_buildmanager.operations.segmentationtraining.ingest import write_section_jsonl

    write_section_jsonl(out, [stale], prune_missing=False)
    getter = _getter([entity])
    ingest_to_section_files(
        output_path=out,
        odata="http://example/odata",
        geometries=None,
        http_get=getter,  # type: ignore[arg-type]
    )
    assert sum("MosaicShape" in unquote(url) for url in getter.calls) >= 1  # type: ignore[attr-defined]
    assert load_section_records(out, 4)[0].type_code == LocationType.CURVEPOLYGON


def test_odata_z_filter_collapses_runs_and_keeps_gaps() -> None:
    zs = list(range(1, 11)) + list(range(13, 37)) + [39] + list(range(41, 46))
    assert _odata_z_filter(zs) == (
        "(Z ge 1 and Z le 10) or (Z ge 13 and Z le 36) or Z eq 39 or (Z ge 41 and Z le 45)"
    )
    or_eq = "(" + DEFAULT_ODATA_FILTER + ") and (" + " or ".join(f"Z eq {z}" for z in zs) + ")"
    ranged = _combined_z_filter(DEFAULT_ODATA_FILTER, zs)
    assert estimate_odata_filter_nodes(or_eq) > ODATA_MAX_FILTER_NODES
    assert estimate_odata_filter_nodes(ranged) <= ODATA_MAX_FILTER_NODES - ODATA_FILTER_NODE_MARGIN
    chunks = list(_chunk_z_for_odata_filter(zs, DEFAULT_ODATA_FILTER))
    assert chunks == [zs]


def test_sparse_z_is_split_under_node_limit() -> None:
    zs = list(range(1, 160, 2))
    chunks = list(_chunk_z_for_odata_filter(zs, DEFAULT_ODATA_FILTER))
    budget = ODATA_MAX_FILTER_NODES - ODATA_FILTER_NODE_MARGIN
    assert len(chunks) > 1
    assert sorted(z for chunk in chunks for z in chunk) == zs
    for chunk in chunks:
        assert estimate_odata_filter_nodes(_combined_z_filter(DEFAULT_ODATA_FILTER, chunk)) <= budget


@given(st.lists(st.integers(min_value=1, max_value=400), min_size=1, max_size=120))
@settings(max_examples=40)
def test_chunk_z_covers_values_under_node_budget(zs: list[int]) -> None:
    chunks = list(_chunk_z_for_odata_filter(zs, DEFAULT_ODATA_FILTER))
    budget = ODATA_MAX_FILTER_NODES - ODATA_FILTER_NODE_MARGIN
    assert sorted({z for chunk in chunks for z in chunk}) == sorted(set(zs))
    for chunk in chunks:
        assert estimate_odata_filter_nodes(_combined_z_filter(DEFAULT_ODATA_FILTER, chunk)) <= budget


def test_odata_pass_b_uses_z_ranges(tmp_path: Path) -> None:
    zs = list(range(1, 11)) + list(range(13, 37)) + [39] + list(range(41, 46))
    entities = [_entity(index, z, _box_wkt(0, 0, 2, 2)) for index, z in enumerate(zs, start=1)]
    getter = _getter(entities)
    ingest_to_section_files(
        output_path=tmp_path / "out",
        odata="http://example/odata",
        geometries=None,
        http_get=getter,  # type: ignore[arg-type]
    )
    pass_b = [unquote(url) for url in getter.calls if "MosaicShape" in unquote(url)]  # type: ignore[attr-defined]
    assert len(pass_b) == 1
    assert "Z ge 1 and Z le 10" in pass_b[0]
    assert "Z eq 1 or Z eq 2" not in pass_b[0]


def test_section_freshness_id_set_and_last_modified(tmp_path: Path) -> None:
    records = [_record(1, 2, _box_wkt(0, 0, 2, 2)), _record(2, 2, _box_wkt(4, 4, 6, 6))]
    params = _params()
    watermark = SectionWatermark(
        ids=[1, 2],
        max_last_modified=max(item.last_modified for item in records).isoformat(),
        tileset_mtime=10.0,
        params_hash=params.hash(),
    )
    save_section_watermark(tmp_path, 2, watermark)
    assert section_is_fresh(records, watermark=watermark, params=params, tileset_mtime=10.0, force=False)
    # LastModified bumped → stale (DB trigger fires on MosaicShape change).
    edited = [_record(1, 2, _box_wkt(0, 0, 2, 2), last_modified="2021-01-01T00:00:00+00:00"), records[1]]
    assert not section_is_fresh(edited, watermark=watermark, params=params, tileset_mtime=10.0, force=False)
    deleted = [records[0]]
    assert not section_is_fresh(deleted, watermark=watermark, params=params, tileset_mtime=10.0, force=False)
    assert not section_is_fresh(records, watermark=watermark, params=params, tileset_mtime=10.0, force=True)


def test_choose_downsample_small_compact_and_long() -> None:
    available = [1, 2, 4, 8]
    small = choose_downsample(
        width_mosaic=32,
        height_mosaic=32,
        finest=1,
        available=available,
        max_texture=4096,
        min_process_pixels=16,
    )
    compact = choose_downsample(
        width_mosaic=8000,
        height_mosaic=8000,
        finest=1,
        available=available,
        max_texture=4096,
        min_process_pixels=16,
    )
    long_process = choose_downsample(
        width_mosaic=20000,
        height_mosaic=200,
        finest=1,
        available=available,
        max_texture=4096,
        min_process_pixels=16,
    )
    assert small == 1
    assert compact == 2
    assert long_process == 8


def _plan_kwargs(**overrides: object) -> dict:
    payload: dict = dict(
        pad=0.0,
        downsample=1,
        available=[1, 2, 4, 8],
        max_texture=1024,
        min_process_pixels=16,
        tile_x_dim=1024,
        tile_y_dim=1024,
        max_tiles_x=1,
        max_tiles_y=1,
    )
    payload.update(overrides)
    return payload


def test_long_mask_stays_at_requested_downsample_and_splits() -> None:
    record = _record(3, 1, _box_wkt(0, 0, 1124, 100))
    plans, polygons = plan_section_crops([record], **_plan_kwargs())
    assert len(plans) == 2
    assert {plan.downsample for plan in plans} == {1}
    assert {plan.window_of[3][1] for plan in plans} == {2}
    origins = {(plan.window.origin_x, plan.window.origin_y) for plan in plans}
    assert origins == {(-512, 0), (512, 0)}
    for plan in plans:
        width, height = plan.output_size()
        origin_x, origin_y = plan.mosaic_origin()
        rings = pixel_rings_in_crop(
            polygons[3],
            origin_x=origin_x,
            origin_y=origin_y,
            downsample=1.0,
            width=width,
            height=height,
        )
        xs = [pt[0] for ring in rings[0] for pt in ring]
        ys = [pt[1] for ring in rings[0] for pt in ring]
        assert rings
        assert max(xs) <= width
        assert max(ys) <= height
        assert min(xs) >= 0
        overlap = min(1124, plan.window.origin_x + plan.window.width) - max(0, plan.window.origin_x)
        assert overlap >= 512


def test_contained_mask_uses_nearest_center_once() -> None:
    record = _record(1, 1, _box_wkt(700, 700, 1000, 1000))
    plans, _polygons = plan_section_crops([record], **_plan_kwargs())
    assert len(plans) == 1
    assert plans[0].window_of[1] == (0, 1)
    assert (plans[0].window.origin_x, plans[0].window.origin_y) == (512, 512)
    assert plans[0].image_key.endswith("_D1_X512-1536_Y512-1536")


def test_masks_share_image_only_when_they_choose_the_same_window() -> None:
    near = _record(1, 1, _box_wkt(700, 700, 900, 900))
    also_near = _record(2, 1, _box_wkt(720, 720, 880, 880))
    shared, _polygons = plan_section_crops([near, also_near], **_plan_kwargs())
    assert len(shared) == 1
    assert set(shared[0].location_ids) == {1, 2}

    left = _record(1, 1, _box_wkt(400, 400, 600, 600))
    right = _record(2, 1, _box_wkt(700, 700, 900, 900))
    separate, _polygons = plan_section_crops([left, right], **_plan_kwargs())
    assert len(separate) == 2
    by_id = {plan.location_ids[0]: plan.window.origin_x for plan in separate}
    assert by_id[1] == 0
    assert by_id[2] == 512


def test_legacy_exporter_coarsens_a_long_mask_to_one_crop() -> None:
    record = _record(1, 1, _box_wkt(0, 0, 2500, 100))
    kwargs = _plan_kwargs()
    tiled, _polygons = plan_section_crops([record], **kwargs)
    legacy, _polygons = plan_section_crops([record], exporter=EXPORTER_LEGACY, **kwargs)
    assert len(tiled) > 1
    assert {plan.downsample for plan in tiled} == {1}
    assert len(legacy) == 1
    assert legacy[0].downsample > 1
    assert legacy[0].window_of[1] == (0, 1)


def test_exporter_geometry_versions_hash_apart() -> None:
    tiled = _params()
    legacy = _params(geometry_version=geometry_version_for(EXPORTER_LEGACY))
    assert tiled.geometry_version == GEOMETRY_TILED
    assert legacy.geometry_version == GEOMETRY_LEGACY
    assert tiled.hash() != legacy.hash()


def _min_phase_overlap_width(x0: int, x1: int, crop: int, phase: int) -> int:
    """Smallest positive x-overlap of one non-overlapping phase with [x0, x1]."""
    widths: list[int] = []
    k_min = (x0 - crop - phase) // crop - 1
    k_max = (x1 - phase + crop - 1) // crop + 1
    for k in range(k_min, k_max + 1):
        origin = phase + k * crop
        overlap = min(x1, origin + crop) - max(x0, origin)
        if overlap > 0:
            widths.append(overlap)
    return min(widths) if widths else 0


@given(
    x0=st.integers(min_value=-4000, max_value=4000),
    width=st.integers(min_value=1025, max_value=2048),
    height=st.integers(min_value=1, max_value=512),
)
@example(x0=0, width=1124, height=100)
@settings(max_examples=40, deadline=None)
def test_split_phase_maximizes_minimum_piece_width(x0: int, width: int, height: int) -> None:
    crop = 1024
    x1 = x0 + width
    ring = (
        (float(x0), 0.0),
        (float(x1), 0.0),
        (float(x1), float(height)),
        (float(x0), float(height)),
        (float(x0), 0.0),
    )
    windows = place_mask_windows(
        [(ring,)],
        (float(x0), 0.0, float(x1), float(height)),
        downsample=1,
        crop_width=crop,
        crop_height=crop,
    )
    assert len(windows) >= 2
    chosen = min(
        min(x1, window.origin_x + window.width) - max(x0, window.origin_x)
        for window in windows
    )
    best = max(_min_phase_overlap_width(x0, x1, crop, phase) for phase in (0, crop // 2))
    assert chosen >= best
    assert len({window.origin_x % crop for window in windows}) == 1
    assert len({window.origin_y % crop for window in windows}) == 1


def test_group_identical_snaps() -> None:
    from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect

    snap = TileRect(0, 2, 0, 2)
    groups = group_snaps([(1, snap, snap, 1), (2, snap, snap, 1)], max_tiles_x=8, max_tiles_y=8)
    assert len(groups) == 1
    assert set(groups[0].location_ids) == {1, 2}


def test_submit_bounded_does_not_grow_unbounded_queue() -> None:
    peaks: list[int] = []
    submitted = 0

    def submit(_item: int) -> int:
        nonlocal submitted
        submitted += 1
        return submitted

    list(submit_bounded(submit, range(20), max_in_flight=3, on_in_flight=peaks.append))
    assert max(peaks) <= 3
    assert submitted == 20


def test_encode_coco_rle_fortran_ones() -> None:
    mask = np.ones((2, 2), dtype=bool)
    rle = encode_coco_rle(mask)
    assert rle["size"] == [2, 2]
    assert rle["counts"] == [0, 4]


def test_empty_label_still_exported(tmp_path: Path) -> None:
    level = tmp_path / "tiles" / "001"
    level.mkdir(parents=True)
    tile_path = level / tile_filename("", ".png", 0, 0)
    Image.fromarray(np.full((8, 8), 128, dtype=np.uint8), mode="L").save(tile_path)
    record = _record(11, 7, _box_wkt(1, 1, 6, 6), label=None, type_name=None)
    out = tmp_path / "export"
    params = _params(pad=0.0, max_texture=8)
    keys = export_section_crops(
        output_path=out,
        z=7,
        records=[record],
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
        overlay=True,
        force=False,
    )
    assert keys
    assert keys[0].startswith("TestVolume_7_")
    payload = json.loads((out / "images" / f"{keys[0]}.json").read_text(encoding="utf-8"))
    assert payload["annotations"][0]["category_name"] == "unlabeled"
    assert payload["image"]["downsample"] == 1
    assert payload["image"]["file_name"] == f"{keys[0]}.png"
    assert payload["image"]["volume"] == "TestVolume"
    assert (out / "images" / f"{keys[0]}.png").is_file()
    assert (out / "images" / f"{keys[0]}.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (out / "masks" / f"{keys[0]}_{record.id}.png").is_file()
    overlay = np.asarray(Image.open(out / "overlays" / f"{keys[0]}.png").convert("RGB"))
    crop = np.asarray(Image.open(out / "images" / f"{keys[0]}.png").convert("RGB"))
    assert overlay.shape == crop.shape
    assert np.array_equal(overlay[0, 0], crop[0, 0])
    interior = overlay[3, 3]
    assert interior[0] > interior[1]
    assert interior[0] > interior[2]


def test_write_overlay_red_is_quarter_transparent(tmp_path: Path) -> None:
    tem = tmp_path / "tem.png"
    mask_path = tmp_path / "mask.png"
    out = tmp_path / "overlay.png"
    Image.fromarray(np.full((4, 4, 3), 128, dtype=np.uint8), mode="RGB").save(tem)
    mask = np.zeros((4, 4), dtype=np.uint8)
    mask[1:3, 1:3] = 255
    Image.fromarray(mask, mode="L").save(mask_path)
    write_overlay(out, width=4, height=4, mask_paths=[str(mask_path)], tem_path=tem)
    overlay = np.asarray(Image.open(out).convert("RGB"))
    assert np.array_equal(overlay[0, 0], (128, 128, 128))
    tem_rgb = np.full((4, 4, 3), 128 / 255.0)
    hue, chroma, _color_luma = rgb_to_hcl(_OVERLAY_COLORS[0])
    luma = perceptual_luma(tem_rgb)[1, 1]
    limited = np.minimum(chroma, max_chroma_at_luma(hue, luma))
    full = hcl_to_rgb(hue, limited, luma)
    expected = np.clip(
        np.rint(((1.0 - _OVERLAY_COLOR_ALPHA) * tem_rgb[1, 1] + _OVERLAY_COLOR_ALPHA * full) * 255.0),
        0,
        255,
    ).astype(np.uint8)
    assert _OVERLAY_COLOR_ALPHA == 0.25
    assert np.array_equal(overlay[1, 1], expected)


def _annotation_crop_tree(root: Path, key: str = "RC2_1_D1_X0-0_Y0-0") -> tuple[Path, Path, Path]:
    images = root / "images"
    masks = root / "masks"
    overlays = root / "overlays"
    images.mkdir(parents=True, exist_ok=True)
    masks.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.full((8, 8, 3), 128, dtype=np.uint8), mode="RGB").save(images / f"{key}.png")
    mask = np.zeros((8, 8), dtype=np.uint8)
    mask[2:6, 2:6] = 255
    Image.fromarray(mask, mode="L").save(masks / f"{key}_99.png")
    return images / f"{key}.png", masks / f"{key}_99.png", overlays / f"{key}.png"


def test_repair_overlays_writes_missing(tmp_path: Path) -> None:
    crop, _mask, overlay = _annotation_crop_tree(tmp_path)
    crop_bytes = crop.read_bytes()
    assert not overlay.is_file()
    assert repair_overlays(tmp_path) == 1
    assert overlay.is_file()
    assert crop.read_bytes() == crop_bytes
    image = np.asarray(Image.open(overlay).convert("RGB"))
    assert image[0, 0, 0] == image[0, 0, 1]
    assert image[3, 3, 0] > image[3, 3, 1]


def test_repair_overlays_rewrites_existing(tmp_path: Path) -> None:
    _crop, _mask, overlay = _annotation_crop_tree(tmp_path)
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_bytes(b"stale-overlay")
    assert repair_overlays(tmp_path) == 1
    with Image.open(overlay) as image:
        assert image.size == (8, 8)


def test_repair_annotation_overlays_writes_gallery(tmp_path: Path) -> None:
    _annotation_crop_tree(tmp_path)
    RepairAnnotationOverlays(OutputPath=str(tmp_path))
    assert (tmp_path / "overlays" / "RC2_1_D1_X0-0_Y0-0.png").is_file()
    rows = list_catalog_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["location_id"] == 99
    assert rows[0]["image_relpath"].endswith(".png")


def test_repair_overlays_sections_filter(tmp_path: Path) -> None:
    _annotation_crop_tree(tmp_path, "RC2_1_D1_X0-0_Y0-0")
    _annotation_crop_tree(tmp_path, "RC2_2_D1_X0-0_Y0-0")
    assert repair_overlays(tmp_path, sections=[2]) == 1
    assert not (tmp_path / "overlays" / "RC2_1_D1_X0-0_Y0-0.png").is_file()
    assert (tmp_path / "overlays" / "RC2_2_D1_X0-0_Y0-0.png").is_file()


def test_fresh_section_export_skips_restitch(tmp_path: Path) -> None:
    level = tmp_path / "tiles" / "001"
    level.mkdir(parents=True)
    Image.fromarray(np.full((8, 8), 90, dtype=np.uint8), mode="L").save(
        level / tile_filename("", ".png", 0, 0)
    )
    record = _record(4, 1, _box_wkt(1, 1, 6, 6), label="soma")
    out = tmp_path / "export"
    params = _params(pad=0.0, max_texture=8)
    kwargs = dict(
        output_path=out,
        z=1,
        records=[record],
        tile_x_dim=8,
        tile_y_dim=8,
        available=[1],
        level_dirs={1: str(level)},
        file_prefix="",
        file_postfix=".png",
        params=params,
        tileset_mtime=5.0,
        max_tiles_x=8,
        max_tiles_y=8,
        workers=1,
        mask_workers=1,
        stage_tiles=None,
        overlay=False,
        force=False,
    )
    export_section_crops(**kwargs)  # type: ignore[arg-type]
    crop = next((out / "images").glob("*.png"))
    mtime = crop.stat().st_mtime
    from nornir_buildmanager.operations.segmentationtraining.freshness import load_section_watermark

    previous = load_section_watermark(out, 1)
    export_section_crops(**kwargs, previous=previous)  # type: ignore[arg-type]
    assert crop.stat().st_mtime == mtime


def test_section_rebuild_plan_logs_left_alone_vs_regenerate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    level = tmp_path / "tiles" / "001"
    level.mkdir(parents=True)
    Image.fromarray(np.full((8, 8), 90, dtype=np.uint8), mode="L").save(
        level / tile_filename("", ".png", 0, 0)
    )
    record = _record(4, 1, _box_wkt(1, 1, 6, 6), label="soma")
    out = tmp_path / "export"
    params = _params(pad=0.0, max_texture=8)
    kwargs = dict(
        output_path=out,
        z=1,
        records=[record],
        tile_x_dim=8,
        tile_y_dim=8,
        available=[1],
        level_dirs={1: str(level)},
        file_prefix="",
        file_postfix=".png",
        params=params,
        tileset_mtime=5.0,
        max_tiles_x=8,
        max_tiles_y=8,
        workers=1,
        mask_workers=1,
        stage_tiles=None,
        overlay=False,
        force=False,
    )
    export_section_crops(**kwargs)  # type: ignore[arg-type]
    from nornir_buildmanager.operations.segmentationtraining.freshness import load_section_watermark
    from nornir_buildmanager.operations.segmentationtraining import pipeline as pipeline_mod

    previous = load_section_watermark(out, 1)
    messages: list[str] = []
    monkeypatch.setattr(pipeline_mod.prettyoutput, "Log", messages.append)
    export_section_crops(**kwargs, previous=previous)  # type: ignore[arg-type]
    rebuild = [msg for msg in messages if "left alone" in msg]
    assert len(rebuild) == 1
    assert "1 annotation(s) left alone" in rebuild[0]
    assert "0 need regeneration" in rebuild[0]
    assert "crops skip=1" in rebuild[0]


def test_mask_only_when_geometry_changes_but_tiles_do_not(tmp_path: Path) -> None:
    level = tmp_path / "tiles" / "001"
    level.mkdir(parents=True)
    Image.fromarray(np.full((8, 8), 40, dtype=np.uint8), mode="L").save(
        level / tile_filename("", ".png", 0, 0)
    )
    first = _record(8, 2, _box_wkt(1, 1, 4, 4), last_modified="2020-01-01T00:00:00+00:00")
    out = tmp_path / "export"
    params = _params(pad=0.0, max_texture=8)
    base = dict(
        output_path=out,
        z=2,
        tile_x_dim=8,
        tile_y_dim=8,
        available=[1],
        level_dirs={1: str(level)},
        file_prefix="",
        file_postfix=".png",
        params=params,
        tileset_mtime=3.0,
        max_tiles_x=8,
        max_tiles_y=8,
        workers=1,
        mask_workers=1,
        stage_tiles=None,
        overlay=False,
        force=False,
    )
    export_section_crops(**base, records=[first])  # type: ignore[arg-type]
    crop = next((out / "images").glob("*.png"))
    crop_mtime = crop.stat().st_mtime
    from nornir_buildmanager.operations.segmentationtraining.freshness import load_section_watermark

    previous = load_section_watermark(out, 2)
    edited = _record(8, 2, _box_wkt(1, 1, 5, 5), last_modified="2022-01-01T00:00:00+00:00")
    export_section_crops(**base, records=[edited], previous=previous)  # type: ignore[arg-type]
    assert crop.stat().st_mtime == crop_mtime
    payload = json.loads(next((out / "images").glob("*.json")).read_text(encoding="utf-8"))
    assert payload["annotations"][0]["last_modified"].startswith("2022-01-01")


def test_sanitize_volume_token() -> None:
    assert sanitize_volume_token("RC2") == "RC2"
    assert sanitize_volume_token("RC2 / foo") == "RC2_foo"
    assert sanitize_volume_token("") == "volume"
    assert sanitize_volume_token(None) == "volume"


def test_resolve_volume_name_from_volume_node() -> None:
    class _Volume:
        tag = "Volume"
        Name = "RC2"

    class _Filter:
        tag = "Filter"
        Name = "Leveled"

        def FindParent(self, parent_tag: str) -> _Volume | None:
            return _Volume() if parent_tag == "Volume" else None

    assert _resolve_volume_name(_Volume()) == "RC2"
    assert _resolve_volume_name(None, _Filter()) == "RC2"
    assert _resolve_volume_name(None) == "volume"


def test_volume_prefix_change_removes_old_files(tmp_path: Path) -> None:
    level = tmp_path / "tiles" / "001"
    level.mkdir(parents=True)
    Image.fromarray(np.full((8, 8), 90, dtype=np.uint8), mode="L").save(
        level / tile_filename("", ".png", 0, 0)
    )
    record = _record(4, 1, _box_wkt(1, 1, 6, 6), label="soma")
    out = tmp_path / "export"
    base = dict(
        output_path=out,
        z=1,
        records=[record],
        tile_x_dim=8,
        tile_y_dim=8,
        available=[1],
        level_dirs={1: str(level)},
        file_prefix="",
        file_postfix=".png",
        tileset_mtime=5.0,
        max_tiles_x=8,
        max_tiles_y=8,
        workers=1,
        mask_workers=1,
        stage_tiles=None,
        overlay=False,
        force=False,
    )
    export_section_crops(**base, params=_params(pad=0.0, max_texture=8, volume="RC2"))  # type: ignore[arg-type]
    old = next((out / "images").glob("*.png"))
    assert old.name.startswith("RC2_")
    from nornir_buildmanager.operations.segmentationtraining.freshness import load_section_watermark

    previous = load_section_watermark(out, 1)
    export_section_crops(  # type: ignore[arg-type]
        **base,
        params=_params(pad=0.0, max_texture=8, volume="RPC1"),
        previous=previous,
    )
    assert not old.is_file()
    renamed = next((out / "images").glob("*.png"))
    assert renamed.name.startswith("RPC1_")


@given(
    loc_id=st.integers(min_value=1, max_value=10_000),
    label=st.one_of(st.none(), st.text(min_size=1, max_size=12, alphabet=st.characters(whitelist_categories=("L", "N")))),
)
@settings(max_examples=25, deadline=None)
def test_location_record_json_roundtrip(loc_id: int, label: str | None) -> None:
    record = _record(loc_id, 9, _box_wkt(0, 0, 3, 3), label=label)
    restored = LocationRecord.from_json(record.to_json())
    assert restored.id == record.id
    assert restored.structure_label == record.structure_label
    assert restored.category_name() == record.category_name()
    assert restored.type_code == LocationType.POLYGON


def test_probe_tile_pixel_size_uses_largest_png(tmp_path: Path) -> None:
    Image.fromarray(np.zeros((512, 512), dtype=np.uint8), mode="L").save(tmp_path / "a.png")
    Image.fromarray(np.zeros((1024, 1024), dtype=np.uint8), mode="L").save(tmp_path / "b.png")
    assert probe_tile_pixel_size(str(tmp_path), samples=8) == (1024, 1024)


def test_probe_tile_pixel_size_snaps_edge_511_to_512(tmp_path: Path) -> None:
    Image.fromarray(np.zeros((512, 511), dtype=np.uint8), mode="L").save(tmp_path / "edge.png")
    Image.fromarray(np.zeros((512, 511), dtype=np.uint8), mode="L").save(tmp_path / "edge2.png")
    assert probe_tile_pixel_size(str(tmp_path), samples=8) == (512, 512)


def test_resolve_tileset_corrects_wrong_tile_dims(tmp_path: Path) -> None:
    Image.fromarray(np.zeros((1024, 1024), dtype=np.uint8), mode="L").save(
        tmp_path / "Leveled_X000_Y000.png"
    )

    class _Level:
        Downsample = 1
        FullPath = str(tmp_path)
        ValidationTime = None

    class _Tileset:
        TileXDim = 512
        TileYDim = 512
        FilePrefix = "Leveled_"
        FilePostfix = ".png"
        Levels = [_Level()]

    class _Filter:
        Tileset = _Tileset()
        FullPath = str(tmp_path)

    tileset = _Filter.Tileset
    info = _resolve_tileset(
        _Filter(),
        downsample=1,
        fail_missing=False,
        tile_x_dim=None,
        tile_y_dim=None,
        level_dirs=None,
        file_prefix=None,
        file_postfix=None,
        tileset_mtime=None,
        available=None,
        channel_name="TEM",
        filter_name="Leveled",
    )
    assert info is not None
    assert info["tile_x_dim"] == 1024
    assert info["tile_y_dim"] == 1024
    assert tileset.TileXDim == 1024
    assert tileset.TileYDim == 1024
    assert info["save_node"] is tileset


def test_resolve_section_stage_root_defaults_and_override() -> None:
    assert resolve_section_stage_root(None, volume="RC2", z=12) == os.path.join(
        DEFAULT_STAGE_TILES_ROOT, "RC2", "z12"
    )
    assert resolve_section_stage_root("", volume="RC2 / foo", z=3) == os.path.join(
        DEFAULT_STAGE_TILES_ROOT, "RC2_foo", "z3"
    )
    assert resolve_section_stage_root("/scratch", volume="RPC1", z=9) == os.path.join(
        "/scratch", "RPC1", "z9"
    )
    assert downsample_stage_dir("/scratch/RPC1/z9", 1) == os.path.join("/scratch/RPC1/z9", "D1")


def test_sweep_removes_staged_tiles_when_no_longer_needed(tmp_path: Path) -> None:
    from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect

    source = tmp_path / "source"
    stage = tmp_path / "stage"
    out = tmp_path / "out"
    source.mkdir()
    stage.mkdir()
    out.mkdir()
    for ix in (0, 1, 2):
        Image.fromarray(np.full((8, 8), ix + 1, dtype=np.uint8), mode="L").save(
            source / tile_filename("", ".png", ix, 0)
        )
    jobs = [
        StitchJob(
            image_key=f"c{ix}",
            snap=TileRect(ix, ix + 1, 0, 1),
            downsample=1,
            tile_x_dim=8,
            tile_y_dim=8,
            image_path=str(out / f"c{ix}.png"),
        )
        for ix in (0, 1, 2)
    ]
    sweep_stitch_jobs(
        jobs,
        source_dir=str(source),
        prefix="",
        postfix=".png",
        workers=1,
        stage_dir=str(stage),
        use_shared_memory=False,
        column_band=1,
        tile_x_dim=8,
        tile_y_dim=8,
    )
    wait_stage_cleanup()
    assert not stage.exists() or not any(stage.iterdir())
    assert all((out / f"c{ix}.png").is_file() for ix in (0, 1, 2))


def test_sweep_reports_column_strips(tmp_path: Path) -> None:
    from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect
    from nornir_buildmanager.operations.segmentationtraining.stitch import count_column_bands

    source = tmp_path / "source"
    out = tmp_path / "out"
    source.mkdir()
    out.mkdir()
    for ix in (0, 1, 2, 3):
        Image.fromarray(np.full((8, 8), ix + 1, dtype=np.uint8), mode="L").save(
            source / tile_filename("", ".png", ix, 0)
        )
    jobs = [
        StitchJob(
            image_key=f"c{ix}",
            snap=TileRect(ix, ix + 1, 0, 1),
            downsample=1,
            tile_x_dim=8,
            tile_y_dim=8,
            image_path=str(out / f"c{ix}.png"),
        )
        for ix in (0, 1, 2, 3)
    ]
    assert count_column_bands(jobs, column_band=1) == 4
    bands: list[tuple[int, int, str]] = []
    sweep_stitch_jobs(
        jobs,
        source_dir=str(source),
        prefix="",
        postfix=".png",
        workers=1,
        stage_dir=None,
        use_shared_memory=False,
        column_band=1,
        tile_x_dim=8,
        tile_y_dim=8,
        on_band=lambda done, total, element: bands.append((done, total, element)),
    )
    assert bands[0] == (0, 4, "X0-1")
    assert bands[-1] == (4, 4, "X3-4")
    assert [item[0] for item in bands] == [0, 1, 1, 2, 2, 3, 3, 4]


def test_stitch_crops_half_tile_step() -> None:
    from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect

    job = StitchJob(
        image_key="half",
        snap=TileRect(0, 2, 0, 1),
        downsample=1,
        tile_x_dim=1024,
        tile_y_dim=1024,
        image_path="unused.png",
        crop_x=512,
        crop_y=0,
        crop_width=1024,
        crop_height=1024,
    )

    def loader(ix: int, iy: int) -> np.ndarray:
        del iy
        return np.full((1024, 1024), ix + 1, dtype=np.uint8)

    canvas = stitch_from_loader(job, loader)
    assert canvas.shape == (1024, 1024)
    assert int(canvas[0, 0]) == 1
    assert int(canvas[0, 511]) == 1
    assert int(canvas[0, 512]) == 2
    assert int(canvas[0, 1023]) == 2


def test_catalog_keeps_each_mask_fragment(tmp_path: Path) -> None:
    import sqlite3

    key_a = "Vol_3_D1_X0-1024_Y0-1024"
    key_b = "Vol_3_D1_X512-1536_Y0-1024"
    images = tmp_path / "images"
    masks = tmp_path / "masks"
    images.mkdir()
    masks.mkdir()
    for key in (key_a, key_b):
        payload = {"annotations": [{"id": 5, "area": 40}]}
        (images / f"{key}.json").write_text(json.dumps(payload), encoding="utf-8")
        Image.fromarray(np.zeros((8, 8), dtype=np.uint8), mode="L").save(masks / f"{key}_5.png")
    db_path = tmp_path / "annotation_crops.sqlite"
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        """
        CREATE TABLE locations (
            location_id INTEGER PRIMARY KEY,
            z INTEGER NOT NULL,
            structure_id INTEGER,
            structure_label TEXT,
            type_id INTEGER,
            type_name TEXT,
            radius REAL,
            image_key TEXT NOT NULL,
            image_relpath TEXT,
            mask_relpath TEXT,
            ignored INTEGER NOT NULL DEFAULT 0,
            sam2PredIou REAL,
            sam2ObjectScore REAL,
            sam2Stability REAL,
            sam2GtIou REAL,
            sam2Checkpoint TEXT,
            sam2ScoredAt TEXT
        )
        """
    )
    legacy.execute(
        "INSERT INTO locations (location_id, z, image_key, sam2PredIou) VALUES (?, ?, ?, ?)",
        (5, 3, key_a, 0.5),
    )
    legacy.commit()
    legacy.close()

    assert rebuild_catalog(tmp_path) == 2
    rows = list_catalog_rows(tmp_path)
    assert {(row["location_id"], row["image_key"]) for row in rows} == {(5, key_a), (5, key_b)}
    scores = {row["image_key"]: row["sam2PredIou"] for row in rows}
    assert scores[key_a] == 0.5
    assert scores[key_b] is None
    assert ignore_location(tmp_path, 5)
    assert not list(masks.glob("*_5.png"))
    assert len(list((tmp_path / "ignored").glob("*_5.png"))) == 2


def test_upsert_catalog_scopes_to_section_image_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-section upsert must not open every volume JSON (avoids EMFILE)."""
    from nornir_buildmanager.operations.segmentationtraining import catalog as catalog_mod

    images = tmp_path / "images"
    masks = tmp_path / "masks"
    images.mkdir()
    masks.mkdir()
    key_z1 = "Vol_1_D4_X0-1024_Y0-1024"
    key_z2 = "Vol_2_D4_X0-1024_Y0-1024"
    for key, loc_id in ((key_z1, 10), (key_z2, 20)):
        (images / f"{key}.json").write_text(
            json.dumps({"annotations": [{"id": loc_id, "area": 10}]}),
            encoding="utf-8",
        )
        Image.fromarray(np.zeros((4, 4), dtype=np.uint8), mode="L").save(
            masks / f"{key}_{loc_id}.png"
        )

    opened: list[str] = []
    real_read = catalog_mod._read_json_object

    def tracking_read(path: Path):
        opened.append(path.name)
        return real_read(path)

    monkeypatch.setattr(catalog_mod, "_read_json_object", tracking_read)
    written = upsert_catalog(tmp_path, {10}, z=1, image_keys=[key_z1])
    assert written == 1
    assert opened == [f"{key_z1}.json"]
    rows = list_catalog_rows(tmp_path)
    assert {(row["location_id"], row["image_key"]) for row in rows} == {(10, key_z1)}

    opened.clear()
    written = upsert_catalog(tmp_path, {20}, z=2, image_keys=[key_z2])
    assert written == 1
    assert opened == [f"{key_z2}.json"]
    rows = list_catalog_rows(tmp_path)
    assert {(row["location_id"], row["image_key"]) for row in rows} == {
        (10, key_z1),
        (20, key_z2),
    }


def test_stitch_pastes_native_1024_tiles() -> None:
    from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect

    job = StitchJob(
        image_key="RC2_3_D1_X124-126_Y117-118",
        snap=TileRect(124, 126, 117, 118),
        downsample=1,
        tile_x_dim=1024,
        tile_y_dim=1024,
        image_path="unused.png",
    )

    def loader(ix: int, iy: int) -> np.ndarray:
        return np.full((1024, 1024), ix % 255, dtype=np.uint8)

    canvas = stitch_from_loader(job, loader)
    assert canvas.shape == (1024, 2048)
    assert int(canvas[0, 0]) == 124 % 255
    assert int(canvas[0, 1024]) == 125 % 255


def test_window_tile_rect_2d_grid() -> None:
    from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect

    rect = TileRect(0, 10, 0, 10)
    windows = window_tile_rect(rect, max_tiles_x=4, max_tiles_y=4, overlap_tiles=1)
    assert len(windows) > 1
    assert all(window.n_x <= 4 and window.n_y <= 4 for window in windows)
