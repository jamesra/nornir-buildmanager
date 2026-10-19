"""Equivalence tests for masks.rasterize_mask_job against the pre-optimization body."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from PIL import Image, ImageDraw

from annotation_crops.rle import encode_coco_rle
from nornir_buildmanager.operations.segmentationtraining.masks import (
    MaskJob,
    rasterize_mask_job,
)


def _rasterize_reference(job: MaskJob) -> dict[str, Any]:
    """Original rasterize_mask_job body (foreground recomputed per use, bbox via np.nonzero)."""
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


def _job(root: Path, name: str, width: int, height: int, rings: tuple) -> MaskJob:
    """Mask job writing under *root*/<name>/."""
    return MaskJob(
        location_id=9,
        image_key="k",
        width=width,
        height=height,
        rings=rings,
        mask_path=str(root / name / "masks" / "k_9.png"),
        rle_path=str(root / name / "rle" / "k_9.json"),
    )


def _assert_same(root: Path, width: int, height: int, rings: tuple) -> dict[str, Any]:
    """Run old and new on the same job; returned dicts and written bytes must match."""
    old_job = _job(root, "old", width, height, rings)
    new_job = _job(root, "new", width, height, rings)
    old = _rasterize_reference(old_job)
    new = rasterize_mask_job(new_job)
    for key in ("bbox", "area", "location_id", "image_key"):
        assert new[key] == old[key], key
        assert type(new[key]) is type(old[key]), key
    assert all(type(v) is int for v in new["bbox"])
    assert Path(new_job.mask_path).read_bytes() == Path(old_job.mask_path).read_bytes()
    assert Path(new_job.rle_path).read_bytes() == Path(old_job.rle_path).read_bytes()
    with Image.open(new_job.mask_path) as saved:
        assert saved.mode == "1"
    return new


SQUARE = (((2, 2), (6, 2), (6, 6), (2, 6)),)
RING_WITH_HOLE = (((1, 1), (14, 1), (14, 14), (1, 14)), ((5, 5), (10, 5), (10, 10), (5, 10)))
EDGE_TOUCHING = (((0, 0), (15, 0), (15, 11), (0, 11)),)
SINGLE_PIXEL = (((3, 4), (3, 4), (3, 4)),)
TWO_VERTEX = (((1, 1), (8, 8)),)


@pytest.mark.parametrize(
    "rings",
    [
        (),
        (TWO_VERTEX,),
        (SINGLE_PIXEL,),
        (SQUARE,),
        (RING_WITH_HOLE,),
        (EDGE_TOUCHING,),
        (SQUARE, RING_WITH_HOLE),
        ((((2, 2), (6, 2), (6, 6), (2, 6)), ((1, 1), (2, 2))),),
    ],
    ids=["empty", "two-vertex", "single-pixel", "square", "hole", "edges", "two-polygons", "short-hole"],
)
def test_rasterize_mask_job_matches_reference_examples(tmp_path: Path, rings: tuple) -> None:
    _assert_same(tmp_path, 16, 12, rings)


def test_rasterize_mask_job_known_values(tmp_path: Path) -> None:
    result = _assert_same(tmp_path, 16, 12, (SQUARE,))
    assert result["bbox"] == [2, 2, 5, 5]
    assert result["area"] == 25
    empty = _assert_same(tmp_path / "e", 16, 12, ())
    assert empty["bbox"] == [0, 0, 0, 0]
    assert empty["area"] == 0


@st.composite
def _rings(draw: st.DrawFn) -> tuple[int, int, tuple]:
    """Random integer polygons (with optional holes) inside or touching a small crop."""
    width = draw(st.integers(1, 40))
    height = draw(st.integers(1, 40))
    point = st.tuples(st.integers(-2, width + 1), st.integers(-2, height + 1))
    ring = st.lists(point, min_size=1, max_size=7).map(tuple)
    polygon = st.lists(ring, min_size=1, max_size=3).map(tuple)
    polygons = draw(st.lists(polygon, min_size=0, max_size=3).map(tuple))
    return width, height, polygons


@given(_rings())
@example((1, 1, ((((0, 0), (0, 0), (0, 0)),),)))
@example((40, 1, ((((0, 0), (39, 0), (39, 0)),),)))
@settings(max_examples=150, deadline=None)
def test_rasterize_mask_job_matches_reference_random(
    tmp_path_factory: pytest.TempPathFactory, case: tuple[int, int, tuple]
) -> None:
    width, height, rings = case
    _assert_same(tmp_path_factory.mktemp("r"), width, height, rings)
