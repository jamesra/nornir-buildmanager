"""Micro-benchmark for catalog.apply_ignore_moves in the steady state (category 19).

rebuild_catalog, upsert_catalog, ignore_locations, and the export/update/sam2 steps call it;
it used to scan every indexed name in masks/ once per ignored id. Case: 500 ignored ids and
20000 mask names (40 per image key), every ignored mask already moved, so nothing is
relocated and the index is unchanged between loops; only the lookup (and the ignore.json
read) is timed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pyperf

from nornir_buildmanager.operations.segmentationtraining.catalog import (
    apply_ignore_moves,
    save_ignore_ids,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    MASKS_FOLDER,
    get_product_index,
)


def _setup(ignored: int, masks: int) -> Path:
    output = Path(tempfile.mkdtemp()) / "out"
    output.mkdir(parents=True)
    index = get_product_index(output)  # folders absent: scans empty, then filled in memory
    for i in range(masks):
        key = f"RC1_{100 + i // 4000}_D1_X{(i // 40) % 100}-{(i // 40) % 100 + 1}_Y0-1"
        index.note(MASKS_FOLDER, f"{key}_{10000 + i}.png", 0.0)
    save_ignore_ids(output, range(1_000_000, 1_000_000 + ignored))
    return output


def main() -> None:
    runner = pyperf.Runner()
    output = _setup(500, 20000)
    runner.bench_func("apply_ignore_moves_i500_m20000_steady", apply_ignore_moves, output)


if __name__ == "__main__":
    main()
