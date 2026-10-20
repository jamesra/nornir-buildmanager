"""Micro-benchmark for catalog._collect_location_rows (category 19).

Realistic catalog rebuild: many masks sit in ignored/ and the ignore set is large. The old code
copied the whole ignore set (``ignored_ids | {location_id}``) for every ignored mask.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pyperf

from nornir_buildmanager.operations.segmentationtraining import catalog
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    drop_product_index,
)

_COUNT = 5000


def _build(root: Path) -> Path:
    output = root / "AnnotationCrops"
    (output / "images").mkdir(parents=True)
    ignored = output / "ignored"
    ignored.mkdir()
    for index in range(_COUNT):
        (ignored / f"RC2_17_D1_X0_Y0_{index}.png").write_bytes(b"x")
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


if __name__ == "__main__":
    main()
