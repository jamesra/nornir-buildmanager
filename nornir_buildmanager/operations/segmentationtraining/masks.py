"""Process-pool mask rasterize from integer pixel rings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from nornir_buildmanager.operations.segmentationtraining.geometry import PolygonRings
from nornir_buildmanager.operations.segmentationtraining.poolutil import run_process_jobs


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


def rasterize_mask_job(job: MaskJob) -> dict[str, Any]:
    """Scanline-fill one location into a 1-bit PNG and write COCO RLE sidecar.

    Returns paths only (plus small RLE stats) so the parent never receives bitmaps.
    """
    # #region agent log
    try:
        import json as _json
        import time as _time
        with open("/workspace/.cursor/debug-11e2ac.log", "a", encoding="utf-8") as _dbg:
            _dbg.write(_json.dumps({
                "sessionId": "11e2ac",
                "runId": "post-fix",
                "hypothesisId": "B,C,E",
                "location": "masks.py:rasterize_mask_job",
                "message": "rasterize start",
                "data": {
                    "location_id": job.location_id,
                    "image_key": job.image_key,
                    "width": job.width,
                    "height": job.height,
                    "n_pixels": job.width * job.height,
                    "alloc_mb": round(job.width * job.height / (1024 * 1024), 1),
                },
                "timestamp": int(_time.time() * 1000),
            }) + "\n")
    except Exception:
        pass
    # #endregion
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
    array = np.asarray(image)
    binary = Image.fromarray((array > 0).astype(np.uint8) * 255, mode="L").convert("1")
    Path(job.mask_path).parent.mkdir(parents=True, exist_ok=True)
    Path(job.rle_path).parent.mkdir(parents=True, exist_ok=True)
    binary.save(job.mask_path)
    rle = encode_coco_rle(array > 0)
    Path(job.rle_path).write_text(json.dumps(rle), encoding="utf-8")
    ys, xs = np.nonzero(array > 0)
    if len(xs) == 0:
        bbox = [0, 0, 0, 0]
        area = 0
    else:
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        bbox = [x0, y0, x1 - x0, y1 - y0]
        area = int((array > 0).sum())
    return {
        "location_id": job.location_id,
        "image_key": job.image_key,
        "mask_path": job.mask_path,
        "rle_path": job.rle_path,
        "bbox": bbox,
        "area": area,
    }


def encode_coco_rle(mask: np.ndarray) -> dict[str, Any]:
    """Uncompressed COCO RLE (Fortran order) for SA-1B JSON."""
    fortran = np.asfortranarray(mask.astype(np.uint8))
    pixels = fortran.ravel(order="F")
    height, width = mask.shape
    counts: list[int] = []
    if pixels.size == 0:
        return {"counts": counts, "size": [height, width]}
    current = int(pixels[0])
    if current == 1:
        counts.append(0)
    run = 1
    for value in pixels[1:]:
        if int(value) == current:
            run += 1
        else:
            counts.append(run)
            current = int(value)
            run = 1
    counts.append(run)
    return {"counts": counts, "size": [int(height), int(width)]}


def run_mask_jobs(jobs: list[MaskJob], *, workers: int) -> list[dict[str, Any]]:
    """Rasterize *jobs* in a process pool, or inline when workers <= 1."""
    return run_process_jobs(
        rasterize_mask_job,
        jobs,
        workers=workers,
        name_prefix="mask",
    )
