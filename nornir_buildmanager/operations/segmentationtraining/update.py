"""ID-set delta for ExportAnnotationCrops -Update.

Removed locations lose mask, RLE, and JSON membership. Added locations are
planned alone and either merged into an existing crop or stitched as a new
tile. Survivors whose LastModified changed are remasked inside the crop they
already occupy. Other survivor masks and TEM crops are left alone.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from nornir_shared import prettyoutput

from nornir_buildmanager.operations.segmentationtraining.catalog import (
    apply_ignore_moves,
    connect,
    ignore_blank_crops,
    prune_catalog_section,
    record_crop_size,
    sqlite_path,
    upsert_catalog,
)
from nornir_buildmanager.operations.segmentationtraining.cleanup import (
    remove_image_key_products,
)
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ImageWatermark,
    SectionCropRun,
    SectionWatermark,
    id_set,
    load_section_watermark,
    max_last_modified,
    save_section_watermark,
)
from nornir_buildmanager.operations.segmentationtraining.curves import hydrate_polygons
from nornir_buildmanager.operations.segmentationtraining.geometry import pixel_rings_in_crop
from nornir_buildmanager.operations.segmentationtraining.ingest import odata_url_for_export
from nornir_buildmanager.operations.segmentationtraining.masks import MaskJob, run_mask_jobs
from nornir_buildmanager.operations.segmentationtraining.overview import write_split_mask_overviews
from nornir_buildmanager.operations.segmentationtraining.planning import PlannedCrop, plan_section_crops
from nornir_buildmanager.operations.segmentationtraining.product_index import CropProductIndex, get_product_index
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord, parse_datetime
from nornir_buildmanager.operations.segmentationtraining.wkt import parse_wkt_polygons
from nornir_buildmanager.operations.segmentationtraining.sam2.write import (
    replace_manifest_rows,
    sa1b_annotation,
    write_overlay,
    write_sa1b_json,
)
from nornir_buildmanager.operations.segmentationtraining.stitch import (
    BlankCrop,
    StitchJob,
    crop_image_filename,
    default_column_band,
    downsample_stage_dir,
    finer_dirs_for_downsample,
    resolve_crop_image,
    sweep_stitch_jobs,
)


def update_section_crops(
    *,
    output_path: str | os.PathLike[str],
    z: int,
    records: list[LocationRecord],
    previous: SectionWatermark | None,
    run: SectionCropRun,
) -> list[str]:
    """Apply an ID-set delta for one section. Returns remaining image keys."""
    params = run.params
    tileset = run.tileset
    exporter = run.exporter
    mask_workers = run.mask_workers
    overlay = run.overlay
    output = Path(output_path)
    products = get_product_index(output)
    if previous is not None:
        from nornir_buildmanager.operations.segmentationtraining.pipeline import (
            drop_recorded_crops_missing_tiles,
        )

        if drop_recorded_crops_missing_tiles(output, z, previous, run.tileset):
            previous = load_section_watermark(output, z)
    current_ids = id_set(records)
    old_ids = set(previous.ids) if previous is not None else set()
    removed = old_ids - current_ids
    added_ids = current_ids - old_ids
    changed = _changed_survivor_ids(output, records, old_ids - removed, previous)
    if not removed and not added_ids and not changed:
        record_crop_size(output, int(params.max_texture))
        prettyoutput.Log(
            f"ExportAnnotationCrops -Update: section {z} membership unchanged, pixels skipped"
        )
        return list(previous.image_keys or []) if previous is not None else []

    by_id = {record.id: record for record in records}
    touched = _prune_removed(output, z, removed, products, previous)
    added = [record for record in records if record.id in added_ids]
    plans: list[PlannedCrop] = []
    polygons_by_id: dict[int, list] = {}
    blank_crops: list[BlankCrop] = []
    if added:
        from nornir_buildmanager.operations.segmentationtraining.pipeline import (
            retain_plans_with_tiles,
        )

        plans, polygons_by_id = plan_section_crops(
            added,
            params=params,
            tileset=tileset,
            exporter=exporter,
        )
        plans = retain_plans_with_tiles(output, z, plans, tileset, products)
        blank_crops = _place_added(
            output,
            z,
            plans,
            polygons_by_id,
            by_id,
            previous,
            products,
            run,
        )
        touched.update(plan.image_key for plan in plans)

    if changed and previous is not None:
        touched.update(
            _refresh_changed_masks(
                output,
                changed,
                by_id,
                previous,
                products,
                mask_workers=mask_workers,
                overlay=overlay,
            )
        )

    image_marks = _rebuild_watermarks(output, previous, by_id, plans, removed)
    image_keys = [mark.key for mark in image_marks]
    _rewrite_manifest(output, z, params.volume, image_marks, removed)
    save_section_watermark(
        output,
        z,
        SectionWatermark(
            ids=sorted(current_ids),
            max_last_modified=max_last_modified(records).isoformat(),
            tileset_mtime=previous.tileset_mtime if previous is not None else run.tileset.mtime,
            params_hash=previous.params_hash if previous is not None else params.hash(),
            downsample=params.downsample,
            image_keys=image_keys,
            images=image_marks,
        ),
    )

    exported_ids = {member for mark in image_marks for member in mark.member_ids}
    remove_only = not added_ids and not changed
    if remove_only:
        # No new masks and no mask redraws: skip the expensive catalog scandir
        # and overview rebuild. Drop removed ids and crops whose tiles were missing.
        prune_catalog_section(output, z, exported_ids)
    else:
        write_split_mask_overviews(
            output, image_marks, exported_ids, max_edge=params.max_texture
        )
        # Pass image_keys to avoid scanning the whole volume. Blank crops are
        # listed here; apply_ignore stays off so this path does not glob every
        # already-ignored id again.
        ignore_blank_crops(
            output,
            z,
            blank_crops,
            {plan.image_key: plan.location_ids for plan in plans},
        )
        upsert_catalog(output, exported_ids, z=z, image_keys=image_keys, apply_ignore=False)
        prune_catalog_section(output, z, exported_ids)

    prettyoutput.Log(
        f"ExportAnnotationCrops -Update: section {z} — "
        f"removed {len(removed)} location(s), added {len(added_ids)}, "
        f"remasked {len(changed)}, crops touched={len(touched)}"
    )
    return image_keys


def _prune_removed(
    output: Path,
    z: int,
    removed: set[int],
    products: CropProductIndex,
    previous: SectionWatermark | None,
) -> set[str]:
    """Delete masks for disqualified ids and drop them from SA-1B JSON.

    Paths come from the section watermark. The catalog is used only when a
    removed id is not listed on any crop.
    """
    if not removed:
        return set()
    keys_by_id = _watermark_keys_for_ids(previous, removed)
    missing = removed - set(keys_by_id)
    for location_id in missing:
        for image_key, _relpath in _catalog_mask_rows(output, z, location_id):
            keys_by_id.setdefault(location_id, set()).add(image_key)
    touched: set[str] = set()
    for location_id, keys in keys_by_id.items():
        for image_key in keys:
            _unlink_computed_member(output, image_key, location_id, products)
            touched.add(image_key)
    _drop_annotations_parallel(output, sorted(touched), removed, products)
    return touched


def _watermark_keys_for_ids(
    previous: SectionWatermark | None,
    location_ids: set[int],
) -> dict[int, set[str]]:
    """Map location ids to the crop keys that listed them on the watermark."""
    found: dict[int, set[str]] = {}
    if previous is None:
        return found
    for mark in previous.images or []:
        for location_id in mark.member_ids:
            if location_id in location_ids:
                found.setdefault(int(location_id), set()).add(mark.key)
    return found


def _catalog_mask_rows(
    output: Path,
    z: int,
    location_id: int,
) -> list[tuple[str, str]]:
    """Return ``(image_key, mask_relpath)`` rows for one location on section *z*."""
    if not sqlite_path(output).is_file():
        return []
    connection = connect(output)
    try:
        rows = connection.execute(
            "SELECT image_key, mask_relpath FROM locations WHERE z = ? AND location_id = ?",
            (int(z), int(location_id)),
        ).fetchall()
    finally:
        connection.close()
    return [(str(row["image_key"]), str(row["mask_relpath"] or "")) for row in rows]


def _unlink_computed_member(
    output: Path,
    image_key: str,
    location_id: int,
    products: CropProductIndex,
) -> None:
    """Delete one location's mask, ignored mask, and RLE, and drop them from the index."""
    for folder, name in (
        ("masks", f"{image_key}_{location_id}.png"),
        ("ignored", f"{image_key}_{location_id}.png"),
        ("_work/rle", f"{image_key}_{location_id}.json"),
    ):
        path = output.joinpath(*folder.split("/")) / name
        if not path.is_file():
            continue
        path.unlink()
        products.forget(folder, name)


def _drop_annotations_parallel(
    output: Path,
    image_keys: list[str],
    removed: set[int],
    products: CropProductIndex,
) -> None:
    """Rewrite SA-1B JSON for *image_keys*, removing *removed* ids.

    Rewrites are dispatched in parallel threads because each JSON file is
    independent and NAS latency dominates over CPU time.
    """
    if not image_keys:
        return
    worker_count = min(len(image_keys), 16)
    if worker_count == 1:
        _drop_annotations(output, image_keys[0], removed, products)
        return
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {pool.submit(_drop_annotations, output, key, removed, products): key for key in image_keys}
        for future in as_completed(futures):
            future.result()  # re-raise any exception from the worker


def _drop_annotations(
    output: Path,
    image_key: str,
    removed: set[int],
    products: CropProductIndex,
) -> None:
    """Remove location ids from one crop JSON, or delete the crop when none remain."""
    json_path = output / "images" / f"{image_key}.json"
    if not json_path.is_file():
        return
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    annotations = payload.get("annotations") or []
    kept = [item for item in annotations if int(item.get("id", -1)) not in removed]
    if len(kept) == len(annotations):
        return
    if not kept:
        remove_image_key_products(output, image_key, products=products)
        return
    payload["annotations"] = kept
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    products.note_path(json_path)


def _changed_survivor_ids(
    output: Path,
    records: list[LocationRecord],
    survivor_ids: set[int],
    previous: SectionWatermark | None,
) -> list[int]:
    """Survivors whose SA-1B last_modified differs from the ingested record."""
    if not survivor_ids or previous is None or not (previous.images or previous.image_keys):
        return []
    stored = _stored_last_modified(output, _section_image_keys(previous))
    changed: list[int] = []
    for record in records:
        if record.id not in survivor_ids:
            continue
        prior = stored.get(record.id)
        if prior is None or prior != record.last_modified:
            changed.append(record.id)
    return changed


def _section_image_keys(previous: SectionWatermark | None) -> list[str]:
    """Crop keys recorded on a section watermark, including per-image marks."""
    if previous is None:
        return []
    keys = list(previous.image_keys or [])
    for mark in previous.images or []:
        if mark.key not in keys:
            keys.append(mark.key)
    return keys


def _stored_last_modified(output: Path, image_keys: list[str]) -> dict[int, Any]:
    """Read annotation timestamps from the known crop JSON files for one section."""
    found: dict[int, Any] = {}
    for image_key in image_keys:
        path = output / "images" / f"{image_key}.json"
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload.get("annotations") or []:
            if "id" not in item:
                continue
            found[int(item["id"])] = parse_datetime(item.get("last_modified"))
    return found


def _refresh_changed_masks(
    output: Path,
    changed: list[int],
    by_id: dict[int, LocationRecord],
    previous: SectionWatermark,
    products: CropProductIndex,
    *,
    mask_workers: int,
    overlay: bool,
) -> set[str]:
    """Redraw changed locations inside the crop window they already occupy."""
    jobs: list[MaskJob] = []
    touched: set[str] = set()
    marks = [mark for mark in (previous.images or []) if mark.width > 0 and mark.height > 0]
    for location_id in changed:
        record = by_id.get(location_id)
        if record is None:
            continue
        polygons = hydrate_polygons(parse_wkt_polygons(record.wkt), record.type_code)
        if not polygons:
            continue
        for mark in marks:
            if location_id not in mark.member_ids:
                continue
            scale = float(mark.downsample)
            rings = pixel_rings_in_crop(
                polygons,
                origin_x=mark.origin_x * scale,
                origin_y=mark.origin_y * scale,
                downsample=scale,
                width=mark.width,
                height=mark.height,
            )
            if not rings:
                continue
            jobs.append(
                MaskJob(
                    location_id=location_id,
                    image_key=mark.key,
                    width=mark.width,
                    height=mark.height,
                    rings=tuple(rings),
                    mask_path=str(output / "masks" / f"{mark.key}_{location_id}.png"),
                    rle_path=str(output / "_work" / "rle" / f"{mark.key}_{location_id}.json"),
                )
            )
            touched.add(mark.key)
    if not jobs:
        return touched
    run_mask_jobs(jobs, workers=mask_workers)
    for job in jobs:
        products.note_path(job.mask_path)
        products.note_path(job.rle_path)
    apply_ignore_moves(output)
    _replace_changed_annotations(output, jobs, by_id, products, overlay)
    return touched


def refresh_existing_location_masks(
    output_path: str | os.PathLike[str],
    record: LocationRecord,
    *,
    exporter: str = "tiled",
) -> list[str]:
    """Redraw one location on crops that already contain it.

    ``tiled`` and ``legacy`` both keep those crop windows. Neither mode
    downloads a TEM image or writes a new crop file. Returns the image keys
    whose masks were rewritten.
    """
    mode = (exporter or "tiled").strip().lower()
    if mode not in {"tiled", "legacy"}:
        raise ValueError("exporter must be tiled or legacy")
    output = Path(output_path)
    marks = _existing_marks_for_location(output, record)
    if not marks:
        return []
    previous = SectionWatermark(
        ids=[record.id],
        max_last_modified=record.last_modified.isoformat(),
        tileset_mtime=None,
        params_hash="",
        downsample=marks[0].downsample,
        image_keys=[mark.key for mark in marks],
        images=marks,
    )
    products = get_product_index(output)
    touched = _refresh_changed_masks(
        output,
        [record.id],
        {record.id: record},
        previous,
        products,
        mask_workers=1,
        overlay=False,
    )
    return sorted(touched)


def _existing_marks_for_location(output: Path, record: LocationRecord) -> list[ImageWatermark]:
    """Crop windows this location already occupies. Missing images are skipped."""
    previous = load_section_watermark(output, record.z)
    raw: list[ImageWatermark] = []
    if previous is not None:
        raw.extend(mark for mark in (previous.images or []) if record.id in mark.member_ids)
    if not raw:
        raw.extend(_marks_from_crop_json(output, record.id))
    ready: list[ImageWatermark] = []
    for mark in raw:
        filled = _mark_with_extent(output, mark)
        if filled is not None:
            ready.append(filled)
    return ready


def _marks_from_crop_json(output: Path, location_id: int) -> list[ImageWatermark]:
    """Build windows from crop JSON when the section watermark has no membership."""
    images = output / "images"
    if not images.is_dir():
        return []
    marks: list[ImageWatermark] = []
    for path in images.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        annotations = payload.get("annotations") or []
        if not any(int(item.get("id", -1)) == location_id for item in annotations if isinstance(item, dict)):
            continue
        image = payload.get("image") if isinstance(payload.get("image"), dict) else {}
        prior = next(
            (item for item in annotations if isinstance(item, dict) and int(item.get("id", -1)) == location_id),
            {},
        )
        marks.append(
            ImageWatermark(
                key=path.stem,
                downsample=int(image.get("downsample") or 1),
                ix0=0,
                ix1=0,
                iy0=0,
                iy1=0,
                member_ids=[location_id],
                max_last_modified=str(prior.get("last_modified") or ""),
                origin_x=int(prior.get("originX") or 0),
                origin_y=int(prior.get("originY") or 0),
                width=int(image.get("width") or 0),
                height=int(image.get("height") or 0),
            )
        )
    return marks


def _mark_with_extent(output: Path, mark: ImageWatermark) -> ImageWatermark | None:
    """Keep a mark only when its crop image exists, filling a missing pixel size."""
    image = resolve_crop_image(output / "images", mark.key)
    if not image.is_file():
        return None
    if mark.width > 0 and mark.height > 0:
        return mark
    from PIL import Image

    with Image.open(image) as picture:
        width, height = picture.size
    if width <= 0 or height <= 0:
        return None
    return ImageWatermark(
        key=mark.key,
        downsample=mark.downsample,
        ix0=mark.ix0,
        ix1=mark.ix1,
        iy0=mark.iy0,
        iy1=mark.iy1,
        member_ids=list(mark.member_ids),
        max_last_modified=mark.max_last_modified,
        origin_x=mark.origin_x,
        origin_y=mark.origin_y,
        width=width,
        height=height,
    )


def _replace_changed_annotations(
    output: Path,
    jobs: list[MaskJob],
    by_id: dict[int, LocationRecord],
    products: CropProductIndex,
    overlay: bool,
) -> None:
    """Replace SA-1B annotations for remasked locations and refresh overlays."""
    by_key: dict[str, list[MaskJob]] = {}
    for job in jobs:
        by_key.setdefault(job.image_key, []).append(job)
    for image_key, group in by_key.items():
        json_path = output / "images" / f"{image_key}.json"
        if not json_path.is_file():
            continue
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        annotations = list(payload.get("annotations") or [])
        replacements: dict[int, dict[str, Any]] = {}
        for job in group:
            rle_path = Path(job.rle_path)
            if not rle_path.is_file():
                continue
            rle = json.loads(rle_path.read_text(encoding="utf-8"))
            bbox, area = _bbox_area_from_rle(rle)
            prior = next((item for item in annotations if int(item.get("id", -1)) == job.location_id), {})
            replacements[job.location_id] = sa1b_annotation(
                by_id[job.location_id],
                rle=rle,
                bbox=bbox,
                area=area,
                window_index=prior.get("windowIndex"),
                window_count=prior.get("windowCount"),
            )
        payload["annotations"] = [
            replacements.get(int(item.get("id", -1)), item) for item in annotations
        ]
        json_path.write_text(json.dumps(payload), encoding="utf-8")
        products.note_path(json_path)
        if overlay:
            image_meta = payload.get("image") or {}
            mask_paths = [
                str(output / "masks" / f"{image_key}_{int(item['id'])}.png")
                for item in payload["annotations"]
                if "id" in item
            ]
            tem = resolve_crop_image(output / "images", image_key)
            overlay_path = output / "overlays" / f"{image_key}.png"
            write_overlay(
                overlay_path,
                width=int(image_meta.get("width") or group[0].width),
                height=int(image_meta.get("height") or group[0].height),
                mask_paths=mask_paths,
                tem_path=tem,
            )
            products.note_path(overlay_path)


def _place_added(
    output: Path,
    z: int,
    plans: list[PlannedCrop],
    polygons_by_id: dict[int, list],
    by_id: dict[int, LocationRecord],
    previous: SectionWatermark | None,
    products: CropProductIndex,
    run: SectionCropRun,
) -> list[BlankCrop]:
    """Stitch crops that are new and attach added locations onto crops that already exist."""
    tile_shape = run.tileset.tile_shape
    mask_workers = run.mask_workers
    overlay = run.overlay
    known = {item.key for item in (previous.images or [])} if previous else set()
    if previous and previous.image_keys:
        known.update(previous.image_keys)
    mask_jobs: list[MaskJob] = []
    stitch_jobs: list[StitchJob] = []
    for plan in plans:
        image_name = crop_image_filename(plan.image_key)
        exists = plan.image_key in known or products.exists("images", image_name)
        if not exists:
            exists = products.exists("images", f"{plan.image_key}.jpg")
        width, height = plan.output_size()
        origin_x, origin_y = plan.mosaic_origin()
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
        if not exists:
            crop_x, crop_y = plan.window.crop_offset(tile_shape.x, tile_shape.y)
            stitch_jobs.append(
                StitchJob(
                    image_key=plan.image_key,
                    snap=plan.snap,
                    downsample=plan.downsample,
                    tile_shape=tile_shape,
                    image_path=str(output / "images" / image_name),
                    crop_x=crop_x,
                    crop_y=crop_y,
                    crop_width=width,
                    crop_height=height,
                )
            )
    if mask_jobs:
        run_mask_jobs(mask_jobs, workers=mask_workers)
        for job in mask_jobs:
            products.note_path(job.mask_path)
            products.note_path(job.rle_path)
    apply_ignore_moves(output)
    blank_crops: list[BlankCrop] = []
    if stitch_jobs:
        blank_crops = _stitch(stitch_jobs, run, products)
    _merge_added_json(output, plans, by_id, products, overlay)
    return blank_crops


def _stitch(
    jobs: list[StitchJob],
    run: SectionCropRun,
    products: CropProductIndex,
) -> list[BlankCrop]:
    """Stitch jobs grouped by downsample and record the written images.

    Returns crops whose stitched pixels are more than half saturated.
    """
    level_dirs = run.tileset.level_dirs
    file_prefix = run.tileset.prefix
    file_postfix = run.tileset.postfix
    workers = run.workers
    stage_tiles = run.stage_tiles
    blank_crops: list[BlankCrop] = []
    by_d: dict[int, list[StitchJob]] = {}
    for job in jobs:
        by_d.setdefault(job.downsample, []).append(job)
    for downsample in sorted(by_d):
        level_dir = level_dirs.get(downsample)
        group = by_d[downsample]
        if not level_dir:
            continue
        stage_dir = None
        if stage_tiles:
            stage_dir = downsample_stage_dir(stage_tiles, downsample)
            os.makedirs(stage_dir, exist_ok=True)
        tile_x = group[0].tile_shape.x
        tile_y = group[0].tile_shape.y
        band = default_column_band(workers, group, tile_x_dim=tile_x, tile_y_dim=tile_y)
        sweep_stitch_jobs(
            group,
            source_dir=level_dir,
            prefix=file_prefix,
            postfix=file_postfix,
            workers=workers,
            stage_dir=stage_dir,
            use_shared_memory=workers > 1,
            finer_dirs=finer_dirs_for_downsample(downsample, level_dirs) or None,
            tile_x_dim=tile_x,
            tile_y_dim=tile_y,
            column_band=band,
            blank_crops=blank_crops,
        )
        for job in group:
            products.note_path(job.image_path)
    return blank_crops


def _merge_added_json(
    output: Path,
    plans: list[PlannedCrop],
    by_id: dict[int, LocationRecord],
    products: CropProductIndex,
    overlay: bool,
) -> None:
    """Append new annotations onto existing crop JSON and refresh overlays."""
    for plan in plans:
        json_path = output / "images" / f"{plan.image_key}.json"
        existing: list[dict[str, Any]] = []
        image_meta: dict[str, Any] = {}
        if json_path.is_file():
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            existing = list(payload.get("annotations") or [])
            image_meta = dict(payload.get("image") or {})
        present = {int(item["id"]) for item in existing if "id" in item}
        width, height = plan.output_size()
        new_items: list[dict[str, Any]] = []
        for location_id in plan.location_ids:
            if location_id in present:
                continue
            rle_path = output / "_work" / "rle" / f"{plan.image_key}_{location_id}.json"
            if not rle_path.is_file():
                continue
            rle = json.loads(rle_path.read_text(encoding="utf-8"))
            bbox, area = _bbox_area_from_rle(rle)
            window_index, window_count = plan.window_of.get(location_id, (0, 1))
            new_items.append(
                sa1b_annotation(
                    by_id[location_id],
                    rle=rle,
                    bbox=bbox,
                    area=area,
                    window_index=window_index,
                    window_count=window_count,
                )
            )
        if not existing and not new_items:
            continue
        image_name = crop_image_filename(plan.image_key)
        write_sa1b_json(
            json_path,
            file_name=str(image_meta.get("file_name") or image_name),
            width=int(image_meta.get("width") or width),
            height=int(image_meta.get("height") or height),
            annotations=existing + new_items,
            downsample=plan.downsample,
            volume=image_meta.get("volume"),
            odata=odata_url_for_export(output, image_meta),
        )
        products.note_path(json_path)
        if overlay:
            mask_paths = [
                str(output / "masks" / f"{plan.image_key}_{int(item['id'])}.png")
                for item in existing + new_items
            ]
            tem = resolve_crop_image(output / "images", plan.image_key)
            overlay_path = output / "overlays" / f"{plan.image_key}.png"
            write_overlay(
                overlay_path,
                width=int(image_meta.get("width") or width),
                height=int(image_meta.get("height") or height),
                mask_paths=mask_paths,
                tem_path=tem,
            )
            products.note_path(overlay_path)


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
        if value == 1 and height:
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


def _rebuild_watermarks(
    output: Path,
    previous: SectionWatermark | None,
    by_id: dict[int, LocationRecord],
    plans: list[PlannedCrop],
    removed: set[int],
) -> list[ImageWatermark]:
    """Rebuild per-crop watermarks after removals and additions."""
    marks: dict[str, ImageWatermark] = {}
    if previous is not None:
        for item in previous.images or []:
            kept = [member for member in item.member_ids if member not in removed]
            if not kept:
                continue
            if not resolve_crop_image(output / "images", item.key).is_file():
                continue
            members = [by_id[i] for i in kept if i in by_id]
            marks[item.key] = ImageWatermark(
                key=item.key,
                downsample=item.downsample,
                ix0=item.ix0,
                ix1=item.ix1,
                iy0=item.iy0,
                iy1=item.iy1,
                member_ids=kept,
                max_last_modified=max_last_modified(members).isoformat() if members else item.max_last_modified,
                origin_x=item.origin_x,
                origin_y=item.origin_y,
                width=item.width,
                height=item.height,
            )
    for plan in plans:
        if not resolve_crop_image(output / "images", plan.image_key).is_file():
            json_path = output / "images" / f"{plan.image_key}.json"
            if not json_path.is_file():
                continue
        prior = marks.get(plan.image_key)
        extra = [i for i in plan.location_ids if prior is None or i not in prior.member_ids]
        if prior is None:
            window = plan.window
            members = [by_id[i] for i in plan.location_ids if i in by_id]
            marks[plan.image_key] = ImageWatermark(
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
            continue
        member_ids = list(prior.member_ids) + extra
        members = [by_id[i] for i in member_ids if i in by_id]
        marks[plan.image_key] = ImageWatermark(
            key=prior.key,
            downsample=prior.downsample,
            ix0=prior.ix0,
            ix1=prior.ix1,
            iy0=prior.iy0,
            iy1=prior.iy1,
            member_ids=member_ids,
            max_last_modified=max_last_modified(members).isoformat() if members else prior.max_last_modified,
            origin_x=prior.origin_x,
            origin_y=prior.origin_y,
            width=prior.width,
            height=prior.height,
        )
    return list(marks.values())


def _rewrite_manifest(
    output: Path,
    z: int,
    volume: str,
    marks: list[ImageWatermark],
    removed: set[int],
) -> None:
    """Rewrite ``manifest.jsonl`` rows for one section from the updated watermarks."""
    path = output / "manifest.jsonl"
    rows: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if int(item.get("z", -1)) != int(z):
                continue
            ids = [int(i) for i in item.get("locationIds") or [] if int(i) not in removed]
            if not ids:
                continue
            item["locationIds"] = ids
            by_key[str(item.get("imageKey"))] = item
    for mark in marks:
        row = by_key.get(mark.key)
        if row is None:
            row = {
                "z": z,
                "volume": volume,
                "imageKey": mark.key,
                "downsample": mark.downsample,
                "image": f"images/{crop_image_filename(mark.key)}",
                "json": f"images/{mark.key}.json",
                "locationIds": list(mark.member_ids),
            }
        else:
            row["locationIds"] = list(mark.member_ids)
        rows.append(row)
    replace_manifest_rows(output, z, rows)
