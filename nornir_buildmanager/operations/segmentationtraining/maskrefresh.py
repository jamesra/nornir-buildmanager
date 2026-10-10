"""Redraw one location's masks without the export pipeline.

Importing this module loads geometry, WKT, and the NumPy/Pillow rasterizer.
It does not import stitch, catalog, ingest, or imageregistration. The gallery
loads it through package stubs so ``nornir_buildmanager`` startup does not run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from nornir_buildmanager.operations.segmentationtraining.curves import hydrate_polygons
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ImageWatermark,
    load_section_watermark,
)
from nornir_buildmanager.operations.segmentationtraining.masks import MaskJob, mask_job_for_crop, run_mask_jobs
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord
from nornir_buildmanager.operations.segmentationtraining.sam2.write import sa1b_annotation
from nornir_buildmanager.operations.segmentationtraining.wkt import (
    location_from_odata_entity,
    parse_wkt_polygons,
)

__all__ = ["location_from_odata_entity", "mask_jobs_for_record", "refresh_existing_location_masks"]


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
    jobs = mask_jobs_for_record(output, record, marks)
    if not jobs:
        return []
    results = run_mask_jobs(jobs, workers=1)
    _replace_annotations(output, results, record)
    image_keys = sorted({str(item["image_key"]) for item in results})
    _move_refreshed_masks_if_ignored(output, record.id, image_keys)
    return image_keys


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
    image = _crop_image(output / "images", mark.key)
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


def _crop_image(images_dir: Path, image_key: str) -> Path:
    """PNG crop, or a leftover JPEG when the PNG is not on disk yet."""
    png = images_dir / f"{image_key}.png"
    if png.is_file():
        return png
    jpeg = images_dir / f"{image_key}.jpg"
    if jpeg.is_file():
        return jpeg
    return png


def mask_jobs_for_record(output: Path, record: LocationRecord, marks: list[ImageWatermark]) -> list[MaskJob]:
    """One raster job per existing window that still contains this polygon."""
    polygons = hydrate_polygons(parse_wkt_polygons(record.wkt), record.type_code)
    if not polygons:
        return []
    jobs: list[MaskJob] = []
    for mark in marks:
        if record.id not in mark.member_ids:
            continue
        scale = float(mark.downsample)
        job = mask_job_for_crop(
            output,
            record.id,
            mark.key,
            polygons,
            origin_x=mark.origin_x * scale,
            origin_y=mark.origin_y * scale,
            downsample=scale,
            width=mark.width,
            height=mark.height,
        )
        if job is not None:
            jobs.append(job)
    return jobs


def _replace_annotations(output: Path, results: list[dict[str, Any]], record: LocationRecord) -> None:
    """Replace SA-1B rows for the windows that were just rasterized."""
    by_key: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_key.setdefault(str(item["image_key"]), []).append(item)
    for image_key, group in by_key.items():
        json_path = output / "images" / f"{image_key}.json"
        if not json_path.is_file():
            continue
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        annotations = list(payload.get("annotations") or [])
        replacements: dict[int, dict[str, Any]] = {}
        for item in group:
            rle_path = Path(str(item["rle_path"]))
            if not rle_path.is_file():
                continue
            rle = json.loads(rle_path.read_text(encoding="utf-8"))
            prior = next(
                (row for row in annotations if int(row.get("id", -1)) == record.id),
                {},
            )
            replacements[record.id] = sa1b_annotation(
                record,
                rle=rle,
                bbox=list(item["bbox"]),
                area=int(item["area"]),
                window_index=prior.get("windowIndex"),
                window_count=prior.get("windowCount"),
            )
        payload["annotations"] = [
            replacements.get(int(row.get("id", -1)), row) for row in annotations
        ]
        json_path.write_text(json.dumps(payload), encoding="utf-8")


def _move_refreshed_masks_if_ignored(output: Path, location_id: int, image_keys: list[str]) -> None:
    """Move this location's new masks into ``ignored/`` when it is already rejected.

    Other rejected locations are left alone. Scanning the whole mask directory
    for every ignore-list id holds the refresh request open until the proxy
    returns a gateway timeout.
    """
    path = output / "ignore.json"
    if not path.is_file() or not image_keys:
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, list):
        return
    listed = False
    for item in payload:
        try:
            listed = int(item) == location_id
        except (TypeError, ValueError):
            continue
        if listed:
            break
    if not listed:
        return
    ignored = output / "ignored"
    for image_key in image_keys:
        source = output / "masks" / f"{image_key}_{location_id}.png"
        if not source.is_file():
            continue
        ignored.mkdir(parents=True, exist_ok=True)
        destination = ignored / source.name
        if destination.is_file():
            destination.unlink()
        source.replace(destination)
