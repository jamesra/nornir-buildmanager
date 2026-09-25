"""Pipeline entry points for ExportAnnotationCrops. PythonCalls return None."""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import nornir_pools
from nornir_imageregistration.computational_lib import (
    ComputationLib,
    GetActiveComputationLib,
    SetActiveComputationLib,
)
from nornir_shared import prettyoutput
from nornir_shared.argparse_helpers import IntegerList
from PIL import Image

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    EXPORTER_LEGACY,
    EXPORTER_TILED,
    ExportParams,
    ImageWatermark,
    ResolvedTileset,
    SectionCropRun,
    SectionWatermark,
    geometry_version_for,
    id_set,
    image_rebuild_mode,
    load_section_watermark,
    max_last_modified,
    save_section_watermark,
    section_is_fresh,
    section_meta_path,
)
from nornir_buildmanager.operations.segmentationtraining.geometry import pixel_rings_in_crop
from nornir_buildmanager.operations.segmentationtraining.grouping import sanitize_volume_token
from nornir_buildmanager.operations.segmentationtraining.ingest import (
    ingest_to_section_files,
    load_cache_meta_sections,
    load_section_records,
)
from nornir_buildmanager.operations.segmentationtraining.masks import MaskJob, run_mask_jobs
from nornir_buildmanager.operations.segmentationtraining.overview import write_split_mask_overviews
from nornir_buildmanager.operations.segmentationtraining.planning import PlannedCrop, plan_section_crops
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    CropProductIndex,
    get_product_index,
)
from nornir_buildmanager.operations.segmentationtraining.progress import (
    MASKS_LABEL,
    MASKS_TRACK_ID,
    SECTIONS_LABEL,
    SECTIONS_TRACK_ID,
    STITCH_LABEL,
    STITCH_TRACK_ID,
    STRIPS_LABEL,
    STRIPS_TRACK_ID,
)
from nornir_buildmanager.progress import report_iterate
from nornir_buildmanager.operations.segmentationtraining.catalog import (
    apply_ignore_moves,
    prune_catalog_section,
    upsert_catalog,
)
from nornir_buildmanager.operations.segmentationtraining.cleanup import (
    remove_image_key_products,
    remove_member_files_not_in,
)
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord
from nornir_buildmanager.operations.segmentationtraining.update import update_section_crops
from nornir_buildmanager.operations.segmentationtraining.sam2 import WriteGallery
from nornir_buildmanager.operations.segmentationtraining.sam2.write import (
    replace_manifest_rows,
    sa1b_annotation,
    write_overlay,
    write_sa1b_json,
)
from nornir_buildmanager.operations.segmentationtraining.stitch import (
    StitchJob,
    count_column_bands,
    crop_image_filename,
    default_column_band,
    downsample_stage_dir,
    finer_dirs_for_downsample,
    probe_tile_pixel_size,
    resolve_crop_image,
    resolve_section_stage_root,
    schedule_remove_stage_tree,
    sweep_stitch_jobs,
)

_logger = logging.getLogger(__name__)


@contextmanager
def force_numpy_computation() -> Iterator[None]:
    """Use NumPy for this pipeline, then restore the process backend."""
    prior = GetActiveComputationLib()
    SetActiveComputationLib(ComputationLib.numpy)
    try:
        yield
    finally:
        if prior is not None:
            SetActiveComputationLib(prior)


def IngestGeometries(
    OutputPath: str | None = None,
    OData: str | None = None,
    Geometries: str | None = None,
    ODataFilter: str | None = None,
    StructureTypeId: list[int] | None = None,
    Sections: list[int] | None = None,
    IncludeOffEdge: bool = False,
    Force: bool = False,
    RefreshOData: bool = False,
    Cleanup: bool = False,
    http_get: Callable[[str], dict[str, Any]] | None = None,
    **kwargs: Any,
) -> None:
    """Volume-level ingest. Writes `{Output}/_work/section_{z}.jsonl` only."""
    del kwargs
    if not OutputPath:
        raise NornirUserException("ExportAnnotationCrops requires -Output")
    if Cleanup:
        return None
    reset_section_visit_order(OutputPath)
    ingest_to_section_files(
        output_path=OutputPath,
        odata=_empty_to_none(OData),
        geometries=_empty_to_none(Geometries),
        odata_filter=_empty_to_none(ODataFilter),
        structure_type_ids=_as_int_list(StructureTypeId),
        sections=_as_int_list(Sections),
        include_off_edge=bool(IncludeOffEdge),
        http_get=http_get,
        force=bool(Force or RefreshOData),
    )
    return None


def RepairAnnotationOverlays(
    OutputPath: str | None = None,
    Sections: list[int] | None = None,
    **kwargs: Any,
) -> None:
    """Rebuild QA overlays from existing TEM crops and masks. Returns None."""
    del kwargs
    if not OutputPath:
        raise NornirUserException("RepairAnnotationOverlays requires -Output")
    apply_ignore_moves(OutputPath)
    written = repair_overlays(OutputPath, sections=_as_int_list(Sections))
    prettyoutput.Log(f"RepairAnnotationOverlays: wrote {written} overlay(s)")
    WriteGallery(OutputPath=OutputPath)
    return None


def repair_overlays(
    output_path: str | os.PathLike[str],
    *,
    sections: list[int] | None = None,
) -> int:
    """Rewrite overlays from existing TEM crops and 1-bit masks. Returns the number written."""
    with force_numpy_computation():
        output = Path(output_path)
        written = 0
        for key, member_ids in _overlay_repair_keys(output, sections):
            if _repair_one_overlay(output, key, member_ids):
                written += 1
        return written


def _overlay_repair_keys(
    output: Path,
    sections: list[int] | None,
) -> list[tuple[str, list[int] | None]]:
    """Image keys and optional mask member order from watermarks, else crop stems."""
    wanted = set(sections) if sections is not None else None
    jobs: list[tuple[str, list[int] | None]] = []
    seen: set[str] = set()
    for z in _section_meta_numbers(output):
        if wanted is not None and z not in wanted:
            continue
        watermark = load_section_watermark(output, z)
        if watermark is None:
            continue
        members_by_key = {item.key: item.member_ids for item in (watermark.images or [])}
        for key in watermark.image_keys or members_by_key:
            if key in seen:
                continue
            seen.add(key)
            jobs.append((key, members_by_key.get(key)))
    if jobs:
        return jobs
    for image in _iter_crop_images(output):
        key = image.stem
        if wanted is not None and not _image_key_matches_sections(key, wanted):
            continue
        jobs.append((key, None))
    return jobs


def _iter_crop_images(output: Path) -> Iterator[Path]:
    """PNG crops first; leftover JPEGs only when PNG is missing for that stem."""
    products = get_product_index(output)
    images = output / "images"
    chosen: dict[str, str] = {}
    for name in products.iter_names("images"):
        if name.endswith(".png"):
            chosen[Path(name).stem] = name
    for name in products.iter_names("images"):
        if name.endswith(".jpg"):
            chosen.setdefault(Path(name).stem, name)
    for name in chosen.values():
        yield images / name


def _section_meta_numbers(output: Path) -> list[int]:
    """Section numbers that already have a watermark under ``_work``."""
    work = output / "_work"
    if not work.is_dir():
        return []
    numbers: list[int] = []
    for path in work.glob("section_*.meta.json"):
        token = path.name[len("section_") : -len(".meta.json")]
        if token.isdigit():
            numbers.append(int(token))
    return sorted(numbers)


def _image_key_matches_sections(key: str, sections: set[int]) -> bool:
    """True when *key* looks like ``{volume}_{z}_D...`` for a requested Z."""
    for z in sections:
        if f"_{z}_D" in key:
            return True
    return False


def _repair_one_overlay(
    output: Path,
    key: str,
    member_ids: list[int] | None,
) -> bool:
    """Rewrite one QA overlay from the crop image and its masks."""
    tem = resolve_crop_image(output / "images", key)
    overlay = output / "overlays" / f"{key}.png"
    if not tem.is_file():
        return False
    mask_paths = _mask_paths_for_key(output, key, member_ids)
    with Image.open(tem) as image:
        width, height = image.size
    write_overlay(
        overlay,
        width=width,
        height=height,
        mask_paths=mask_paths,
        tem_path=tem,
    )
    return True


def _mask_paths_for_key(
    output: Path,
    key: str,
    member_ids: list[int] | None,
) -> list[str]:
    """Mask paths for one crop. Known member ids avoid scanning the folder map."""
    products = get_product_index(output)
    masks = output / "masks"
    if member_ids:
        return [
            str(masks / f"{key}_{location_id}.png")
            for location_id in member_ids
            if products.exists("masks", f"{key}_{location_id}.png")
        ]
    found = [
        (location_id, name)
        for location_id, name in products.members("masks", key)
        if name.endswith(".png")
    ]
    found.sort(key=lambda item: item[0])
    return [str(masks / name) for _, name in found]


def ExportSectionCrops(
    OutputPath: str | None = None,
    FilterNode: Any = None,
    section_node: Any = None,
    VolumeElement: Any = None,
    VolumeNode: Any = None,
    Pad: float = 1.0,
    Downsample: int = 1,
    Exporter: str = EXPORTER_TILED,
    MaxTexture: int | None = None,
    MaxTiles: int | None = None,
    MinProcessPixels: int = 16,
    IncludeOffEdge: bool = False,
    Channels: str = "TEM",
    Filters: str = "Leveled",
    Workers: int | None = None,
    MaskWorkers: int | None = None,
    StageTiles: str | None = None,
    Force: bool = False,
    FailMissingTileset: bool = False,
    NoOverlay: bool = False,
    Overlay: bool = True,
    TileXDim: int | None = None,
    TileYDim: int | None = None,
    LevelDirs: dict[int, str] | None = None,
    FilePrefix: str | None = None,
    FilePostfix: str | None = None,
    TilesetMtime: float | None = None,
    AvailableDownsamples: list[int] | None = None,
    Cleanup: bool = False,
    RefreshOData: bool = False,
    Update: bool = False,
    **kwargs: Any,
) -> Any:
    """Per-section crop/mask/SA-1B write. Returns a Tileset node only if TileXDim/TileYDim were corrected."""
    if not OutputPath:
        raise NornirUserException("ExportAnnotationCrops requires -Output")
    if Cleanup:
        return None
    if bool(Update) and bool(Force):
        raise NornirUserException("ExportAnnotationCrops -Update cannot be combined with -Force")
    filter_node = FilterNode if FilterNode is not None else VolumeElement
    section = section_node
    if section is None and filter_node is not None and hasattr(filter_node, "FindParent"):
        section = filter_node.FindParent("Section")
    z = int(section.Number) if section is not None else int(kwargs.get("Z", 0))
    sections = load_cache_meta_sections(OutputPath)
    if z in sections:
        report_iterate(
            SECTIONS_TRACK_ID,
            section_visit_index(OutputPath, z),
            len(sections),
            SECTIONS_LABEL,
            depth=0,
            section=z,
        )
    try:
        return _export_section_crops_entry(
            OutputPath=OutputPath,
            VolumeNode=VolumeNode,
            Pad=Pad,
            Downsample=Downsample,
            Exporter=Exporter,
            MaxTexture=MaxTexture,
            MaxTiles=MaxTiles,
            MinProcessPixels=MinProcessPixels,
            IncludeOffEdge=IncludeOffEdge,
            Channels=Channels,
            Filters=Filters,
            Workers=Workers,
            MaskWorkers=MaskWorkers,
            StageTiles=StageTiles,
            Force=Force,
            RefreshOData=RefreshOData,
            Update=Update,
            FailMissingTileset=FailMissingTileset,
            NoOverlay=NoOverlay,
            Overlay=Overlay,
            TileXDim=TileXDim,
            TileYDim=TileYDim,
            LevelDirs=LevelDirs,
            FilePrefix=FilePrefix,
            FilePostfix=FilePostfix,
            TilesetMtime=TilesetMtime,
            AvailableDownsamples=AvailableDownsamples,
            filter_node=filter_node,
            section=section,
            z=z,
        )
    finally:
        if z in sections:
            report_iterate(
                SECTIONS_TRACK_ID,
                section_visit_index(OutputPath, z) + 1,
                len(sections),
                SECTIONS_LABEL,
                depth=0,
                section=z,
            )


_section_visit_order: dict[str, list[int]] = {}


def reset_section_visit_order(output_path: str) -> None:
    """Clear the section progress cursor for one output tree."""
    _section_visit_order.pop(str(output_path), None)


def section_visit_index(output_path: str, z: int) -> int:
    """Zero-based index that increases in the order sections are entered.

    Volume XML may visit high Z first (RPC2) or low Z first (RPC1). The bar
    follows that visit order instead of the ascending ingest list.
    """
    order = _section_visit_order.setdefault(str(output_path), [])
    try:
        return order.index(z)
    except ValueError:
        order.append(z)
        return len(order) - 1


def _export_section_crops_entry(
    *,
    OutputPath: str,
    VolumeNode: Any,
    Pad: float,
    Downsample: int,
    Exporter: str,
    MaxTexture: int | None,
    MaxTiles: int | None,
    MinProcessPixels: int,
    IncludeOffEdge: bool,
    Channels: str,
    Filters: str,
    Workers: int | None,
    MaskWorkers: int | None,
    StageTiles: str | None,
    Force: bool,
    RefreshOData: bool,
    Update: bool,
    FailMissingTileset: bool,
    NoOverlay: bool,
    Overlay: bool,
    TileXDim: int | None,
    TileYDim: int | None,
    LevelDirs: dict[int, str] | None,
    FilePrefix: str | None,
    FilePostfix: str | None,
    TilesetMtime: float | None,
    AvailableDownsamples: list[int] | None,
    filter_node: Any,
    section: Any,
    z: int,
) -> Any:
    """Body of ExportSectionCrops after the sections progress track is opened."""
    records = load_section_records(OutputPath, z)
    if not IncludeOffEdge:
        records = [record for record in records if not record.off_edge]
    if not records:
        prettyoutput.Log(f"ExportAnnotationCrops: no geometries for section {z}")
        _purge_empty_section(Path(OutputPath), z)
        return None

    tileset = _resolve_tileset(
        filter_node,
        downsample=int(Downsample),
        fail_missing=bool(FailMissingTileset),
        tile_x_dim=TileXDim,
        tile_y_dim=TileYDim,
        level_dirs=LevelDirs,
        file_prefix=FilePrefix,
        file_postfix=FilePostfix,
        tileset_mtime=TilesetMtime,
        available=AvailableDownsamples,
        channel_name=str(Channels),
        filter_name=str(Filters),
    )
    if tileset is None:
        return None
    save_node = tileset.save_node

    max_texture = resolve_max_texture(MaxTexture)
    max_tiles_x, max_tiles_y = _max_tiles(
        MaxTiles, max_texture, tileset.tile_x_dim, tileset.tile_y_dim
    )
    volume = _resolve_volume_name(VolumeNode, filter_node, section)
    exporter = (Exporter or EXPORTER_TILED).strip().lower()
    try:
        geometry_version = geometry_version_for(exporter)
    except ValueError as exc:
        raise NornirUserException(str(exc)) from exc
    params = ExportParams(
        pad=float(Pad),
        downsample=int(Downsample),
        max_texture=max_texture,
        min_process_pixels=int(MinProcessPixels),
        include_off_edge=bool(IncludeOffEdge),
        channel=str(Channels),
        filter_name=str(Filters),
        volume=volume,
        geometry_version=geometry_version,
        tile_x_dim=int(tileset.tile_x_dim),
        tile_y_dim=int(tileset.tile_y_dim),
    )
    run = SectionCropRun(
        params=params,
        tileset=tileset,
        exporter=exporter,
        max_tiles_x=max_tiles_x,
        max_tiles_y=max_tiles_y,
        workers=int(Workers) if Workers else (os.cpu_count() or 1),
        mask_workers=int(MaskWorkers) if MaskWorkers else (os.cpu_count() or 1),
        stage_tiles=resolve_section_stage_root(
            _empty_to_none(StageTiles),
            volume=volume,
            z=z,
        ),
        overlay=bool(Overlay) and not bool(NoOverlay),
    )
    watermark = load_section_watermark(OutputPath, z)
    if Update:
        try:
            with force_numpy_computation():
                update_section_crops(
                    output_path=OutputPath,
                    z=z,
                    records=records,
                    previous=watermark,
                    run=run,
                )
                nornir_pools.ReleaseStagePools()
        finally:
            schedule_remove_stage_tree(run.stage_tiles)
        return save_node
    if section_is_fresh(
        records,
        watermark=watermark,
        params=params,
        tileset_mtime=tileset.mtime,
        force=bool(Force),
        refresh_odata=bool(RefreshOData),
    ):
        prettyoutput.Log(
            f"ExportAnnotationCrops: section {z} is fresh, skipping "
            f"({len(records)} annotation(s))"
        )
        return save_node

    try:
        with force_numpy_computation():
            export_section_crops(
                output_path=OutputPath,
                z=z,
                records=records,
                run=run,
                force=bool(Force),
                refresh_odata=bool(RefreshOData),
                previous=watermark,
            )
            nornir_pools.ReleaseStagePools()
    finally:
        # Different sections use distinct z{n} roots; do not block the next section.
        schedule_remove_stage_tree(run.stage_tiles)
    return save_node


def export_section_crops(
    *,
    output_path: str | os.PathLike[str],
    z: int,
    records: list[LocationRecord],
    run: SectionCropRun,
    force: bool,
    refresh_odata: bool = False,
    previous: SectionWatermark | None = None,
) -> list[str]:
    """Plan, stitch, rasterize, and write SA-1B products for one section."""
    params = run.params
    tileset = run.tileset
    tile_x_dim = tileset.tile_x_dim
    tile_y_dim = tileset.tile_y_dim
    level_dirs = tileset.level_dirs
    file_prefix = tileset.prefix
    file_postfix = tileset.postfix
    tileset_mtime = tileset.mtime
    exporter = run.exporter
    workers = run.workers
    mask_workers = run.mask_workers
    stage_tiles = run.stage_tiles
    overlay = run.overlay
    with force_numpy_computation():
        output = Path(output_path)
        products = get_product_index(output)
        plans, polygons_by_id = plan_section_crops(
            records,
            params=params,
            tileset=tileset,
            exporter=exporter,
        )
        by_id = {record.id: record for record in records}
        previous_images = {item.key: item for item in (previous.images or [])} if previous else {}
        previous_hash = previous.params_hash if previous else None
        previous_mtime = previous.tileset_mtime if previous else None
        current_keys = {plan.image_key for plan in plans}
        previous_keys = set(previous.image_keys or []) if previous else set()
        _remove_stale_image_products(output, previous_keys - current_keys, products=products)
        for plan in plans:
            remove_member_files_not_in(
                output, plan.image_key, set(plan.location_ids), products=products
            )

        mask_jobs: list[MaskJob] = []
        stitch_jobs: list[StitchJob] = []
        join_specs: list[tuple[PlannedCrop, str]] = []
        manifest_rows: list[dict[str, Any]] = []

        for plan in plans:
            image_path = output / "images" / crop_image_filename(plan.image_key)
            json_path = output / "images" / f"{plan.image_key}.json"
            current_mark = _image_watermark(plan, by_id)
            mode = image_rebuild_mode(
                current_mark,
                previous_images.get(plan.image_key),
                image_path=image_path,
                json_path=json_path,
                params_hash=params.hash(),
                previous_params_hash=previous_hash,
                tileset_mtime=tileset_mtime,
                previous_tileset_mtime=previous_mtime,
                force=force,
                refresh_odata=refresh_odata,
                products=products,
            )
            if mode == "skip":
                join_specs.append((plan, mode))
                manifest_rows.append(_manifest_row(plan, z, params.volume))
                continue
            width, height = plan.output_size()
            origin_x, origin_y = plan.mosaic_origin()
            crop_x, crop_y = plan.window.crop_offset(tile_x_dim, tile_y_dim)
            for location_id in plan.location_ids:
                rings = pixel_rings_in_crop(
                    polygons_by_id[location_id],
                    origin_x=origin_x,
                    origin_y=origin_y,
                    downsample=float(plan.downsample),
                    width=width,
                    height=height,
                )
                if not rings:
                    continue
                mask_jobs.append(
                    MaskJob(
                        location_id=location_id,
                        image_key=plan.image_key,
                        width=width,
                        height=height,
                        rings=tuple(rings),
                        mask_path=str(output / "masks" / f"{plan.image_key}_{location_id}.png"),
                        rle_path=str(output / "_work" / "rle" / f"{plan.image_key}_{location_id}.json"),
                    )
                )
            if mode == "full":
                if not force and products.exists("images", image_path.name):
                    # PNG already on disk; skip restitch unless -Force was passed.
                    # Masks still run below since we may have no prior watermark.
                    mode = "mask_only"
                else:
                    stitch_jobs.append(
                        StitchJob(
                            image_key=plan.image_key,
                            snap=plan.snap,
                            downsample=plan.downsample,
                            tile_x_dim=tile_x_dim,
                            tile_y_dim=tile_y_dim,
                            image_path=str(image_path),
                            crop_x=crop_x,
                            crop_y=crop_y,
                            crop_width=width,
                            crop_height=height,
                        )
                    )
            join_specs.append((plan, mode))

        _log_section_rebuild_plan(z, join_specs)

        mask_results: list[dict[str, Any]] = []
        if mask_jobs:
            mask_reporter = prettyoutput.TaskProgressReporter(
                MASKS_TRACK_ID,
                len(mask_jobs),
                name=MASKS_LABEL,
                section=z,
            )
            mask_reporter.start()
            try:
                mask_results = run_mask_jobs(
                    mask_jobs,
                    workers=mask_workers,
                    on_complete=mask_reporter.update,
                )
            finally:
                mask_reporter.complete()
            for job in mask_jobs:
                products.note_path(job.mask_path)
                products.note_path(job.rle_path)
        apply_ignore_moves(output)

        by_d: dict[int, list[StitchJob]] = {}
        for job in stitch_jobs:
            by_d.setdefault(job.downsample, []).append(job)
        stitch_reporter: prettyoutput.TaskProgressReporter | None = None
        stitch_offset = 0
        if stitch_jobs:
            stitch_reporter = prettyoutput.TaskProgressReporter(
                STITCH_TRACK_ID,
                len(stitch_jobs),
                name=STITCH_LABEL,
                section=z,
            )
            stitch_reporter.start()
        try:
            for downsample in sorted(by_d):
                level_dir = level_dirs.get(downsample)
                jobs = by_d[downsample]
                if not level_dir:
                    _logger.warning("No tileset level for downsample %s, skipping stitch", downsample)
                    stitch_offset += len(jobs)
                    if stitch_reporter is not None:
                        stitch_reporter.update(stitch_offset)
                    continue
                stage_dir = None
                if stage_tiles:
                    stage_dir = downsample_stage_dir(stage_tiles, downsample)
                    os.makedirs(stage_dir, exist_ok=True)
                offset = stitch_offset
                band_width = default_column_band(
                    workers,
                    jobs,
                    tile_x_dim=tile_x_dim,
                    tile_y_dim=tile_y_dim,
                )
                strip_total = count_column_bands(jobs, band_width)
                strip_reporter: prettyoutput.TaskProgressReporter | None = None
                if strip_total > 0:
                    strip_reporter = prettyoutput.TaskProgressReporter(
                        STRIPS_TRACK_ID,
                        strip_total,
                        name=STRIPS_LABEL,
                        section=z,
                    )
                    strip_reporter.start()

                def on_stitch_progress(done: int, _total: int, _offset: int = offset) -> None:
                    """Advance the section stitch bar. The offset is bound here so the next downsample does not move it."""
                    if stitch_reporter is not None:
                        stitch_reporter.update(_offset + done)

                def on_band(
                    done: int,
                    _total: int,
                    element: str,
                    _reporter: prettyoutput.TaskProgressReporter | None = strip_reporter,
                ) -> None:
                    """Advance the column-strip bar. The reporter is bound here for this downsample only."""
                    if _reporter is not None:
                        _reporter.update(done, element=element)

                try:
                    sweep_stitch_jobs(
                        jobs,
                        source_dir=level_dir,
                        prefix=file_prefix,
                        postfix=file_postfix,
                        workers=workers,
                        stage_dir=stage_dir,
                        use_shared_memory=workers > 1,
                        finer_dirs=finer_dirs_for_downsample(downsample, level_dirs) or None,
                        tile_x_dim=tile_x_dim,
                        tile_y_dim=tile_y_dim,
                        column_band=band_width,
                        on_progress=on_stitch_progress,
                        on_band=on_band if strip_reporter is not None else None,
                    )
                finally:
                    if strip_reporter is not None:
                        strip_reporter.complete()
                for job in jobs:
                    products.note_path(job.image_path)
                    leftover = Path(job.image_path).with_suffix(".jpg")
                    if leftover.name != Path(job.image_path).name:
                        products.forget("images", leftover.name)
                stitch_offset += len(jobs)
        finally:
            if stitch_reporter is not None:
                stitch_reporter.complete()

        mask_by_member = {(item["image_key"], item["location_id"]): item for item in mask_results}
        image_marks: list[ImageWatermark] = []
        for plan, mode in join_specs:
            image_marks.append(_image_watermark(plan, by_id))
            if mode == "skip":
                # Crop/mask membership and OData LastModified already matched. Only
                # ``stat`` overlay vs on-disk TEM/masks — never load pixels or restitch.
                if overlay and not _overlay_is_fresh(output, plan, products):
                    _write_plan_overlay(output, plan, tile_x_dim, tile_y_dim, products)
                continue
            width, height = plan.output_size()
            annotations: list[dict[str, Any]] = []
            mask_paths: list[str] = []
            for location_id in plan.location_ids:
                rle_name = f"{plan.image_key}_{location_id}.json"
                mask_name = f"{plan.image_key}_{location_id}.png"
                rle_path = output / "_work" / "rle" / rle_name
                mask_path = output / "masks" / mask_name
                if not products.exists("_work/rle", rle_name):
                    continue
                try:
                    rle = json.loads(rle_path.read_text(encoding="utf-8"))
                except OSError:
                    products.refresh_one("_work/rle", rle_name)
                    continue
                record = by_id[location_id]
                window_index, window_count = plan.window_of.get(location_id, (0, 1))
                stats = mask_by_member.get((plan.image_key, location_id), {})
                bbox = stats.get("bbox") or _bbox_area_from_rle(rle)[0]
                area = stats.get("area")
                if area is None:
                    _, area = _bbox_area_from_rle(rle)
                annotations.append(
                    sa1b_annotation(
                        record,
                        rle=rle,
                        bbox=bbox,
                        area=int(area),
                        window_index=window_index,
                        window_count=window_count,
                        origin_x=plan.window.origin_x,
                        origin_y=plan.window.origin_y,
                    )
                )
                if products.exists("masks", mask_name):
                    mask_paths.append(str(mask_path))
            image_name = crop_image_filename(plan.image_key)
            json_path = output / "images" / f"{plan.image_key}.json"
            write_sa1b_json(
                json_path,
                file_name=image_name,
                width=width,
                height=height,
                annotations=annotations,
                downsample=plan.downsample,
                volume=params.volume,
            )
            products.note_path(json_path)
            if overlay:
                overlay_path = output / "overlays" / f"{plan.image_key}.png"
                write_overlay(
                    overlay_path,
                    width=width,
                    height=height,
                    mask_paths=mask_paths,
                    tem_path=output / "images" / image_name,
                )
                products.note_path(overlay_path)
            manifest_rows.append(_manifest_row(plan, z, params.volume))

        replace_manifest_rows(output, z, manifest_rows)
        save_section_watermark(
            output,
            z,
            SectionWatermark(
                ids=sorted(id_set(records)),
                max_last_modified=max_last_modified(records).isoformat(),
                tileset_mtime=tileset_mtime,
                params_hash=params.hash(),
                downsample=params.downsample,
                image_keys=[plan.image_key for plan in plans],
                images=image_marks,
            ),
        )
        keep_ids = id_set(records)
        image_keys = [plan.image_key for plan in plans]
        write_split_mask_overviews(output, image_marks, keep_ids, max_edge=params.max_texture)
        upsert_catalog(output, keep_ids, z=z, image_keys=image_keys)
        prune_catalog_section(output, z, keep_ids)
        return image_keys



def resolve_max_texture(flag: int | None) -> int:
    """Return -MaxTexture if set, otherwise 1024 pixels per crop axis."""
    if flag:
        return int(flag)
    return 1024


def _max_tiles(
    override: int | None,
    max_texture: int,
    tile_x_dim: int,
    tile_y_dim: int,
) -> tuple[int, int]:
    """Tiles per crop axis from ``-MaxTiles``, else ``MaxTexture`` divided by tile size."""
    if override:
        value = max(1, int(override))
        return value, value
    if tile_x_dim <= 0 or tile_y_dim <= 0:
        raise NornirUserException("Tileset TileXDim/TileYDim must be positive")
    return max(1, math.floor(max_texture / tile_x_dim)), max(1, math.floor(max_texture / tile_y_dim))


def _resolve_tileset(
    filter_node: Any,
    *,
    downsample: int,
    fail_missing: bool,
    tile_x_dim: int | None,
    tile_y_dim: int | None,
    level_dirs: dict[int, str] | None,
    file_prefix: str | None,
    file_postfix: str | None,
    tileset_mtime: float | None,
    available: list[int] | None,
    channel_name: str,
    filter_name: str,
) -> ResolvedTileset | None:
    """Tile size and level directories from the filter, or from explicit overrides.

    A sampled PNG wins over ``TileXDim`` / ``TileYDim`` when they disagree, and
    the tileset node is updated so later stages see the real size.
    """
    if level_dirs is not None and tile_x_dim and tile_y_dim:
        return ResolvedTileset(
            tile_x_dim=int(tile_x_dim),
            tile_y_dim=int(tile_y_dim),
            available=available or sorted(level_dirs),
            level_dirs={int(k): str(v) for k, v in level_dirs.items()},
            prefix=file_prefix or "",
            postfix=file_postfix or ".png",
            mtime=tileset_mtime,
        )
    if filter_node is None or not hasattr(filter_node, "Tileset"):
        message = "ExportAnnotationCrops needs a Filter with a Tileset"
        if fail_missing:
            raise NornirUserException(message)
        prettyoutput.Log(message)
        return None
    tileset = filter_node.Tileset
    if tileset is None:
        message = f"No Tileset on filter {getattr(filter_node, 'FullPath', channel_name + '/' + filter_name)}"
        if fail_missing:
            raise NornirUserException(message)
        prettyoutput.Log(message)
        return None
    levels = list(getattr(tileset, "Levels", []) or [])
    available_levels = sorted({int(level.Downsample) for level in levels if int(level.Downsample) >= 1}) or [downsample]
    dirs: dict[int, str] = {}
    mtimes: list[float] = []
    for level in levels:
        d = int(level.Downsample)
        dirs[d] = level.FullPath
        stamp = _level_mtime(level)
        if stamp is not None:
            mtimes.append(stamp)
    declared_x = int(tile_x_dim or tileset.TileXDim or 0)
    declared_y = int(tile_y_dim or tileset.TileYDim or 0)
    prefix = file_prefix if file_prefix is not None else (tileset.FilePrefix or "")
    postfix = file_postfix if file_postfix is not None else (tileset.FilePostfix or ".png")
    resolved_mtime = tileset_mtime if tileset_mtime is not None else (max(mtimes) if mtimes else None)
    finest = min(dirs) if dirs else int(downsample)
    sample_dir = dirs.get(finest)
    actual = probe_tile_pixel_size(sample_dir, postfix=str(postfix)) if sample_dir else None
    resolved_x, resolved_y = declared_x, declared_y
    save_node = None
    if actual is not None:
        resolved_x, resolved_y = actual
        if resolved_x != declared_x or resolved_y != declared_y:
            prettyoutput.Log(
                f"ExportAnnotationCrops: tileset PNG is {resolved_x}x{resolved_y} but "
                f"TileXDim/TileYDim is {declared_x}x{declared_y}; updating VolumeData"
            )
            tileset.TileXDim = resolved_x
            tileset.TileYDim = resolved_y
            save_node = tileset
    return ResolvedTileset(
        tile_x_dim=resolved_x,
        tile_y_dim=resolved_y,
        available=available or available_levels,
        level_dirs=dirs,
        prefix=prefix,
        postfix=postfix,
        mtime=resolved_mtime,
        save_node=save_node,
    )


def _level_mtime(level: Any) -> float | None:
    """Level timestamp from ``ValidationTime``, else the level directory mtime."""
    validation = getattr(level, "ValidationTime", None)
    if isinstance(validation, datetime) and validation != datetime.min:
        try:
            return validation.timestamp()
        except Exception:
            pass
    path = getattr(level, "FullPath", None)
    if path and os.path.isdir(path):
        return os.path.getmtime(path)
    return None


def _overlay_is_fresh(output: Path, plan: PlannedCrop, products: CropProductIndex) -> bool:
    """True when the QA overlay exists and is at least as new as TEM crop and masks.

    Uses indexed mtimes only. OData LastModified is already handled by
    ``image_rebuild_mode`` before skip.
    """
    del output
    overlay_mtime = products.mtime("overlays", f"{plan.image_key}.png")
    if overlay_mtime is None:
        return False
    tem_name = f"{plan.image_key}.png"
    if not products.exists("images", tem_name):
        tem_name = f"{plan.image_key}.jpg"
    tem_mtime = products.mtime("images", tem_name)
    if tem_mtime is not None and tem_mtime > overlay_mtime + 1e-6:
        return False
    for location_id in plan.location_ids:
        mask_mtime = products.mtime("masks", f"{plan.image_key}_{location_id}.png")
        if mask_mtime is not None and mask_mtime > overlay_mtime + 1e-6:
            return False
    return True


def _write_plan_overlay(
    output: Path,
    plan: PlannedCrop,
    tile_x_dim: int,
    tile_y_dim: int,
    products: CropProductIndex,
) -> None:
    """Rebuild a QA overlay from the on-disk TEM crop and 1-bit masks.

    Does not restitch mosaic tiles. Callers should use :func:`_overlay_is_fresh`
    first when only timestamps may have changed.
    """
    del tile_x_dim, tile_y_dim
    width, height = plan.output_size()
    tem_name = f"{plan.image_key}.png"
    if not products.exists("images", tem_name) and products.exists("images", f"{plan.image_key}.jpg"):
        tem_name = f"{plan.image_key}.jpg"
    tem = output / "images" / tem_name
    mask_paths = [
        str(output / "masks" / f"{plan.image_key}_{location_id}.png")
        for location_id in plan.location_ids
        if products.exists("masks", f"{plan.image_key}_{location_id}.png")
    ]
    overlay_path = output / "overlays" / f"{plan.image_key}.png"
    write_overlay(
        overlay_path,
        width=width,
        height=height,
        mask_paths=mask_paths,
        tem_path=tem,
    )
    products.note_path(overlay_path)


def _manifest_row(plan: PlannedCrop, z: int, volume: str) -> dict[str, Any]:
    """One ``manifest.jsonl`` row for a planned crop."""
    return {
        "z": z,
        "volume": volume,
        "imageKey": plan.image_key,
        "downsample": plan.downsample,
        "image": f"images/{crop_image_filename(plan.image_key)}",
        "json": f"images/{plan.image_key}.json",
        "locationIds": plan.location_ids,
    }


def _purge_empty_section(output: Path, z: int) -> None:
    """Remove crop products and catalog rows for a Z that no longer has locations."""
    watermark = load_section_watermark(output, z)
    keys: set[str] = set()
    if watermark is not None:
        keys.update(watermark.image_keys or [])
        keys.update(item.key for item in (watermark.images or []))
    # One folder scan for the whole tree. Per-key globs re-list masks/ on CIFS.
    products = get_product_index(output)
    _remove_stale_image_products(output, keys, products=products)
    replace_manifest_rows(output, z, [])
    section_meta_path(output, z).unlink(missing_ok=True)
    prune_catalog_section(output, z, set())


def _remove_stale_image_products(
    output: Path,
    stale_keys: set[str],
    *,
    products: CropProductIndex | None = None,
) -> None:
    """Delete prior crop products whose keys are no longer emitted for this Z."""
    for key in stale_keys:
        remove_image_key_products(output, key, products=products)


def _resolve_volume_name(*nodes: Any) -> str:
    """Volume XML Name, sanitized for filenames."""
    for node in nodes:
        if node is None:
            continue
        tag = getattr(node, "tag", None)
        name = getattr(node, "Name", None)
        if tag == "Volume" and name:
            return sanitize_volume_token(str(name))
        finder = getattr(node, "FindParent", None)
        if not callable(finder):
            continue
        parent = finder("Volume")
        parent_name = getattr(parent, "Name", None) if parent is not None else None
        if parent_name:
            return sanitize_volume_token(str(parent_name))
    return "volume"


def _log_section_rebuild_plan(z: int, join_specs: list[tuple[PlannedCrop, str]]) -> None:
    """Log how many annotations and crops are skipped vs regenerated for one Z."""
    left_alone: set[int] = set()
    regenerate: set[int] = set()
    crops_skip = 0
    crops_mask_only = 0
    crops_full = 0
    for plan, mode in join_specs:
        members = set(plan.location_ids)
        if mode == "skip":
            crops_skip += 1
            left_alone |= members
        elif mode == "mask_only":
            crops_mask_only += 1
            regenerate |= members
        else:
            crops_full += 1
            regenerate |= members
    # A location on both a skipped crop and a regen crop still needs work.
    left_alone -= regenerate
    prettyoutput.Log(
        f"ExportAnnotationCrops: section {z} — "
        f"{len(left_alone)} annotation(s) left alone, "
        f"{len(regenerate)} need regeneration "
        f"({crops_mask_only} crop(s) mask-only, {crops_full} crop(s) full stitch); "
        f"crops skip={crops_skip} mask_only={crops_mask_only} full={crops_full}"
    )


def _image_watermark(plan: PlannedCrop, by_id: dict[int, LocationRecord]) -> ImageWatermark:
    """Watermark for one planned crop from its member location records."""
    members = [by_id[i] for i in plan.location_ids if i in by_id]
    window = plan.window
    return ImageWatermark(
        key=plan.image_key,
        downsample=plan.downsample,
        ix0=plan.snap.ix0,
        ix1=plan.snap.ix1,
        iy0=plan.snap.iy0,
        iy1=plan.snap.iy1,
        member_ids=list(plan.location_ids),
        max_last_modified=max_last_modified(members).isoformat() if members else "",
        origin_x=window.origin_x,
        origin_y=window.origin_y,
        width=window.width,
        height=window.height,
    )


def _bbox_area_from_rle(rle: dict[str, Any]) -> tuple[list[int], int]:
    """Foreground bounding box and area from a column-major COCO RLE."""
    counts = [int(c) for c in rle.get("counts") or []]
    size = rle.get("size") or [0, 0]
    height, width = int(size[0]), int(size[1])
    area = 0
    value = 0
    offset = 0
    xs: list[int] = []
    ys: list[int] = []
    for run in counts:
        if value == 1:
            area += run
            for index in range(offset, offset + run):
                ys.append(index % height)
                xs.append(index // height)
        offset += run
        value = 1 - value
    if not xs:
        return [0, 0, 0, 0], 0
    x0, x1 = min(xs), max(xs) + 1
    y0, y1 = min(ys), max(ys) + 1
    return [x0, y0, x1 - x0, y1 - y0], area


def _empty_to_none(value: Any) -> Any:
    """Treat ``None`` and blank strings as missing."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _as_int_list(value: Any) -> list[int] | None:
    """Normalize a scalar, list, or ``-Sections``-style ``1,3,5-7`` string."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        parsed = IntegerList(value)
        return parsed or None
    if isinstance(value, list):
        flattened: list[int] = []
        for item in value:
            if isinstance(item, str) and ("," in item or "-" in item.strip()):
                flattened.extend(IntegerList(item))
            else:
                flattened.append(int(item))
        return flattened or None
    return [int(value)]
