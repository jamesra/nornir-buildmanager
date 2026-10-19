"""Micro-benchmark for sam2.write.write_overlay (category 18).

One 1024x1024 crop: textured 8-bit TEM and four overlapping circular masks (radii 150-300 px,
about 680k masked pixels in total), end to end: TEM load, HCL tint per mask, PNG save.
Inputs and output go under TESTOUTPUTPATH (or the temp folder); the overlay is overwritten each call.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pyperf
from PIL import Image

from nornir_buildmanager.operations.segmentationtraining.sam2.write import write_overlay

SIZE = 1024
CIRCLES = ((300, 300, 250), (700, 300, 200), (500, 700, 300), (800, 800, 150))


def _inputs() -> tuple[Path, Path, list[str]]:
    """Write the TEM and mask PNGs once; return output path, TEM path, and mask paths."""
    root = Path(os.environ.get("TESTOUTPUTPATH") or tempfile.gettempdir()) / "bench_write_overlay"
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    tem = np.clip(rng.normal(128, 40, (SIZE, SIZE)), 0, 255).astype(np.uint8)
    Image.fromarray(tem, mode="L").save(root / "tem.png")
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    mask_paths = []
    for index, (cx, cy, radius) in enumerate(CIRCLES):
        mask = (((xx - cx) ** 2 + (yy - cy) ** 2) <= radius**2).astype(np.uint8) * 255
        Image.fromarray(mask, mode="L").save(root / f"mask_{index}.png")
        mask_paths.append(str(root / f"mask_{index}.png"))
    return root / "overlay.png", root / "tem.png", mask_paths


def _run(out_path: Path, tem_path: Path, mask_paths: list[str]) -> None:
    """One overlay write."""
    write_overlay(out_path, width=SIZE, height=SIZE, mask_paths=mask_paths, tem_path=tem_path)


def main() -> None:
    """Run the pyperf benchmark."""
    runner = pyperf.Runner()
    runner.bench_func("write_overlay_1024_4masks", _run, *_inputs())


if __name__ == "__main__":
    main()
