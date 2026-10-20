"""rasterize_mask_job writes its PNG and RLE sidecar atomically, with unchanged bytes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from annotation_crops.rle import encode_coco_rle
from nornir_buildmanager.operations.segmentationtraining.atomic_io import atomic_save_png
from nornir_buildmanager.operations.segmentationtraining.masks import MaskJob, rasterize_mask_job

_RING = ((2.0, 2.0), (12.0, 2.0), (12.0, 9.0), (2.0, 9.0))


def _job(tmp_path: Path) -> MaskJob:
    return MaskJob(
        location_id=7,
        image_key="k",
        width=16,
        height=12,
        rings=((_RING,),),
        mask_path=str(tmp_path / "masks" / "k_7.png"),
        rle_path=str(tmp_path / "work" / "rle" / "k_7.json"),
    )


def _fail_replace(src: object, dst: object) -> None:
    raise OSError("simulated crash before the swap")


def test_written_bytes_match_direct_writes(tmp_path: Path) -> None:
    job = _job(tmp_path)
    result = rasterize_mask_job(job)
    foreground = np.asarray(Image.open(job.mask_path)) > 0
    Image.fromarray(foreground).save(tmp_path / "direct.png")
    assert Path(job.mask_path).read_bytes() == (tmp_path / "direct.png").read_bytes()
    assert Path(job.rle_path).read_text(encoding="utf-8") == json.dumps(encode_coco_rle(foreground))
    assert result["area"] == int(foreground.sum())
    assert not list(tmp_path.rglob("*.tmp"))


def test_failed_swap_keeps_old_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job(tmp_path)
    rasterize_mask_job(job)
    old_png = Path(job.mask_path).read_bytes()
    old_rle = Path(job.rle_path).read_text(encoding="utf-8")
    bigger = MaskJob(**{**job.__dict__, "rings": (((0.0, 0.0), (15.0, 0.0), (15.0, 11.0), (0.0, 11.0)),)})
    # Only the PNG swap fails: it comes first, so the RLE must be untouched too.
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        rasterize_mask_job(bigger)
    assert Path(job.mask_path).read_bytes() == old_png
    assert Path(job.rle_path).read_text(encoding="utf-8") == old_rle
    json.loads(Path(job.rle_path).read_text(encoding="utf-8"))


def test_failed_png_swap_leaves_no_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "fresh.png"
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        atomic_save_png(Image.new("1", (4, 4)), target)
    assert not target.exists()
