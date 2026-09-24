"""QA overlays tint TEM with Viking HCL hue while keeping TEM luma."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from nornir_buildmanager.operations.segmentationtraining.sam2.write import (
    hcl_to_rgb,
    max_chroma_at_luma,
    perceptual_luma,
    rgb_to_hcl,
    write_overlay,
)


def test_rgb_to_hcl_red_matches_viking() -> None:
    hue, chroma, luma = rgb_to_hcl((255, 0, 0))
    assert abs(hue - 0.0) < 1e-9
    assert abs(chroma - 1.0) < 1e-9
    assert abs(luma - 0.3) < 1e-9


def test_hcl_keeps_tem_luma_and_applies_red_hue(tmp_path: Path) -> None:
    tem = np.zeros((8, 8), dtype=np.uint8)
    tem[:, :4] = 40
    tem[:, 4:] = 200
    Image.fromarray(tem, mode="L").save(tmp_path / "tem.png")
    mask = np.zeros((8, 8), dtype=np.uint8)
    mask[2:6, 2:6] = 255
    Image.fromarray(mask, mode="L").save(tmp_path / "mask.png")
    write_overlay(
        tmp_path / "overlay.png",
        width=8,
        height=8,
        mask_paths=[str(tmp_path / "mask.png")],
        tem_path=tmp_path / "tem.png",
    )
    overlay = np.asarray(Image.open(tmp_path / "overlay.png").convert("RGB"))
    tem_rgb = np.asarray(Image.open(tmp_path / "tem.png").convert("RGB"))
    inside = overlay[2:6, 2:6]
    outside = overlay[0, 0]
    assert np.array_equal(outside, tem_rgb[0, 0])
    dark = inside[:, 0]
    bright = inside[:, -1]
    assert perceptual_luma(dark.astype(np.float64) / 255.0).mean() < perceptual_luma(
        bright.astype(np.float64) / 255.0
    ).mean()
    assert inside[..., 0].mean() > inside[..., 1].mean()
    assert inside[..., 0].mean() > inside[..., 2].mean()


def test_hcl_roundtrip_at_own_luma() -> None:
    hue, chroma, luma = rgb_to_hcl((255, 0, 0))
    limited = min(chroma, float(max_chroma_at_luma(hue, np.array(luma))))
    rgb = hcl_to_rgb(hue, np.array(limited), np.array(luma))
    assert abs(float(rgb[0]) - 1.0) < 1e-6
    assert abs(float(rgb[1])) < 1e-6
    assert abs(float(rgb[2])) < 1e-6
