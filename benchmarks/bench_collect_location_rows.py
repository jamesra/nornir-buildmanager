"""Micro-benchmark for catalog._collect_location_rows (category 19).

Realistic catalog rebuild: many masks sit in ignored/ and the ignore set is large. The old code
copied the whole ignore set (``ignored_ids | {location_id}``) for every ignored mask.

Second case: crop JSON lists annotations whose ``{image_key}_{id}.png`` mask is absent (not yet
rasterized, or renamed), so each row falls back to a fragment-name lookup in a full ``masks/``.
The old code rescanned every cached mask name for each such row.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pyperf

from nornir_buildmanager.operations.segmentationtraining import catalog
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    drop_product_index,
)

_COUNT = 5000
_MISSING_ROWS = 3000
_ROWS_PER_CROP = 10


def _build(root: Path) -> Path:
    output = root / "AnnotationCrops"
    (output / "images").mkdir(parents=True)
    ignored = output / "ignored"
    ignored.mkdir()
    for index in range(_COUNT):
        (ignored / f"RC2_17_D1_X0_Y0_{index}.png").write_bytes(b"x")
    drop_product_index(output)
    return output


def _build_missing(root: Path) -> Path:
    """Crops whose annotations have no exact mask, beside ``_COUNT`` unrelated masks."""
    output = root / "MissingMasks"
    images = output / "images"
    images.mkdir(parents=True)
    masks = output / "masks"
    masks.mkdir()
    for index in range(_COUNT):
        (masks / f"RC2_17_D1_X1_Y1_{index}.png").write_bytes(b"x")
    next_id = 100_000
    for crop in range(_MISSING_ROWS // _ROWS_PER_CROP):
        annotations = []
        for _ in range(_ROWS_PER_CROP):
            annotations.append({"id": next_id, "area": 12.0})
            next_id += 1
        payload = {"annotations": annotations}
        (images / f"RC2_17_D1_X0_Y{crop}.json").write_text(json.dumps(payload), encoding="utf-8")
    drop_product_index(output)
    return output


def main() -> None:
    runner = pyperf.Runner()
    with tempfile.TemporaryDirectory() as tmp:
        output = _build(Path(tmp))
        ignored_ids = set(range(_COUNT))
        runner.bench_func(
            f"collect_location_rows_{_COUNT}_ignored",
            lambda: len(catalog._collect_location_rows(output, {}, ignored_ids)),
        )
        missing = _build_missing(Path(tmp))
        runner.bench_func(
            f"collect_location_rows_{_MISSING_ROWS}_rows_without_exact_mask_{_COUNT}_masks",
            lambda: len(catalog._collect_location_rows(missing, {}, set())),
        )


if __name__ == "__main__":
    main()
