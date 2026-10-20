"""Process-pool mask rasterize from integer pixel rings."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image, ImageDraw

from annotation_crops.rle import encode_coco_rle

from nornir_buildmanager.operations.segmentationtraining.atomic_io import atomic_save_png, atomic_write_text
from nornir_buildmanager.operations.segmentationtraining.product_index import MASKS_FOLDER
from nornir_buildmanager.operations.segmentationtraining.geometry import PolygonRings, pixel_rings_in_crop
from nornir_buildmanager.operations.segmentationtraining.poolutil import run_process_jobs
from nornir_buildmanager.operations.segmentationtraining.workdir import rle_dir

if TYPE_CHECKING:
    from nornir_buildmanager.operations.segmentationtraining.planning import PlannedCrop


@dataclass(frozen=True)
class MaskJob:
    """Picklable spec: no WKT, no TEM pixels."""

    location_id: int
    image_key: str
    width: int
    height: int
    rings: tuple[PolygonRings, ...]
    mask_path: str
    rle_path: str


def mask_job_for_crop(
    output: Path,
    location_id: int,
    image_key: str,
    polygons: list[PolygonRings],
    *,
    origin_x: float,
    origin_y: float,
    downsample: float,
    width: int,
    height: int,
) -> MaskJob | None:
    """Mask job for one location in one crop window, or None when no ring lands inside it.

    Owns the on-disk layout: ``masks/<key>_<id>.png`` and ``<work folder>/rle/<key>_<id>.json``.
    """
    rings = pixel_rings_in_crop(
        polygons,
        origin_x=origin_x,
        origin_y=origin_y,
        downsample=downsample,
        width=width,
        height=height,
    )
    if not rings:
        return None
    return MaskJob(
        location_id=location_id,
        image_key=image_key,
        width=width,
        height=height,
        rings=tuple(rings),
        mask_path=str(output / MASKS_FOLDER / f"{image_key}_{location_id}.png"),
        rle_path=str(rle_dir(output) / f"{image_key}_{location_id}.json"),
    )


def mask_jobs_for_plan(
    output: Path, plan: PlannedCrop, polygons_by_id: dict[int, list[PolygonRings]]
) -> list[MaskJob]:
    """Mask jobs for every member of a planned crop, in ``plan.location_ids`` order."""
    width, height = plan.output_size()
    origin_x, origin_y = plan.mosaic_origin()
    jobs: list[MaskJob] = []
    for location_id in plan.location_ids:
        job = mask_job_for_crop(
            output,
            location_id,
            plan.image_key,
            polygons_by_id[location_id],
            origin_x=origin_x,
            origin_y=origin_y,
            downsample=float(plan.downsample),
            width=width,
            height=height,
        )
        if job is not None:
            jobs.append(job)
    return jobs


def rasterize_mask_job(job: MaskJob) -> dict[str, Any]:
    """Scanline-fill one location into a 1-bit PNG and write COCO RLE sidecar.

    Returns paths only (plus small RLE stats) so the parent never receives bitmaps.
    """
    mask = np.zeros((job.height, job.width), dtype=np.uint8)
    image = Image.fromarray(mask, mode="L")
    draw = ImageDraw.Draw(image)
    for polygon in job.rings:
        exterior = list(polygon[0])
        if len(exterior) < 3:
            continue
        draw.polygon(exterior, fill=1)
        for hole in polygon[1:]:
            if len(hole) >= 3:
                draw.polygon(list(hole), fill=0)
    foreground = np.asarray(image) > 0
    Path(job.mask_path).parent.mkdir(parents=True, exist_ok=True)
    Path(job.rle_path).parent.mkdir(parents=True, exist_ok=True)
    # A bool array maps straight to a mode "1" image; same PNG bytes as L*255 -> convert("1").
    # Atomic: a killed worker leaves the old file, never a truncated PNG or RLE that maskrefresh would crash on.
    atomic_save_png(Image.fromarray(foreground), Path(job.mask_path))
    rle = encode_coco_rle(foreground)
    atomic_write_text(Path(job.rle_path), json.dumps(rle))
    area = int(np.count_nonzero(foreground))
    if area == 0:
        bbox = [0, 0, 0, 0]
    else:
        # Occupied columns/rows instead of np.nonzero, which builds two int64 arrays of size area.
        cols = np.flatnonzero(foreground.any(axis=0))
        rows = np.flatnonzero(foreground.any(axis=1))
        x0, x1 = int(cols[0]), int(cols[-1]) + 1
        y0, y1 = int(rows[0]), int(rows[-1]) + 1
        bbox = [x0, y0, x1 - x0, y1 - y0]
    return {
        "location_id": job.location_id,
        "image_key": job.image_key,
        "mask_path": job.mask_path,
        "rle_path": job.rle_path,
        "bbox": bbox,
        "area": area,
    }


def run_mask_jobs(
    jobs: list[MaskJob],
    *,
    workers: int,
    on_complete: Callable[[int], None] | None = None,
) -> list[dict[str, Any]]:
    """Rasterize *jobs* in a process pool, or inline when workers <= 1."""
    return run_process_jobs(
        rasterize_mask_job,
        jobs,
        workers=workers,
        name_prefix="mask",
        on_complete=on_complete,
    )
