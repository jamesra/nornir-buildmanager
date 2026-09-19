"""Score AnnotationCrops masks into sqlite SAM2 columns (build machine only)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nornir_shared import prettyoutput
from PIL import Image

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.segmentationtraining.catalog import (
    SAM2_COLUMNS,
    list_catalog_rows,
    sqlite_path,
    upsert_sam2_scores,
)
from nornir_buildmanager.operations.segmentationtraining.sam2.predictor import (
    Predictor,
    load_predictor,
)

_BBox = list[int]


def mask_bbox(mask_path: str | Path) -> _BBox | None:
    """Axis-aligned bbox of nonzero mask pixels as ``[x, y, w, h]``."""
    with Image.open(mask_path) as image:
        extrema = image.convert("L").getbbox()
    if extrema is None:
        return None
    left, top, right, bottom = extrema
    return [left, top, right - left, bottom - top]


def box_iou(first: _BBox, second: _BBox) -> float:
    """Intersection-over-union of two ``[x, y, w, h]`` boxes."""
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0
    ix0 = max(ax, bx)
    iy0 = max(ay, by)
    ix1 = min(ax + aw, bx + bw)
    iy1 = min(ay + ah, by + bh)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


def ScoreAnnotationCrops(
    OutputPath: str | None = None,
    Checkpoint: str | None = None,
    Predictor: Predictor | Callable[..., dict[str, Any]] | None = None,
    **kwargs: Any,
) -> None:
    """Write SAM2 score columns for each catalog row. Returns None.

    Uses an injected *Predictor* in tests. On the build machine, omit it to
    load the optional SAM2 stack; if that stack is missing, log and return
    without failing the pipeline.
    """
    del kwargs
    if not OutputPath:
        raise NornirUserException("ScoreAnnotationCrops requires -Output")
    if not sqlite_path(OutputPath).is_file():
        prettyoutput.Log("ScoreAnnotationCrops: no annotation_crops.sqlite; run Export or WriteGallery first")
        return None
    predictor = Predictor
    if predictor is None:
        try:
            predictor = load_predictor(Checkpoint)
        except RuntimeError as exc:
            prettyoutput.Log(f"ScoreAnnotationCrops: {exc}")
            return None
    scored_at = datetime.now(timezone.utc).isoformat()
    scored = 0
    for row in list_catalog_rows(OutputPath):
        image = Path(OutputPath) / str(row.get("image_relpath") or row.get("jpeg_relpath") or "")
        mask = Path(OutputPath) / str(row.get("mask_relpath") or "")
        if not image.is_file() or not mask.is_file():
            continue
        bbox = mask_bbox(mask)
        payload = predictor(
            image_path=image,
            mask_path=mask,
            bbox=bbox,
            checkpoint=Checkpoint,
            location_id=int(row["location_id"]),
        )
        scores = {name: payload.get(name) for name in SAM2_COLUMNS}
        if scores.get("sam2Checkpoint") is None:
            scores["sam2Checkpoint"] = Checkpoint
        if scores.get("sam2ScoredAt") is None:
            scores["sam2ScoredAt"] = scored_at
        pred_box = payload.get("pred_bbox")
        if scores.get("sam2GtIou") is None and pred_box is not None and bbox is not None:
            scores["sam2GtIou"] = box_iou(bbox, list(pred_box))
        upsert_sam2_scores(OutputPath, int(row["location_id"]), scores)
        scored += 1
    prettyoutput.Log(f"ScoreAnnotationCrops: scored {scored} location(s)")
    return None
