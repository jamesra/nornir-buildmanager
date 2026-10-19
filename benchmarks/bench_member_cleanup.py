"""Micro-benchmark for cleanup.remove_member_files_not_in over every image key (category 19).

Export and -Cleanup call it once per image key; it used to scan every indexed name in the
masks, ignored, and RLE folders for each key. Case: 2000 keys x 10 members in each of the
three member folders (60000 indexed names), all ids kept so nothing is unlinked and the
index is unchanged between loops; only the per-key lookup is timed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pyperf

from nornir_buildmanager.operations.segmentationtraining.cleanup import (
    remove_member_files_not_in,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    IGNORED_FOLDER,
    MASKS_FOLDER,
    RLE_FOLDER,
    CropProductIndex,
)


def _setup(keys: int, members: int) -> tuple[Path, CropProductIndex, list[str], set[int]]:
    output = Path(tempfile.mkdtemp()) / "out"
    index = CropProductIndex(output)  # folders absent: scans empty, then filled in memory
    names = [f"RC1_{100 + k // 40}_D1_X{k % 40}-{k % 40 + 1}_Y0-1" for k in range(keys)]
    for key in names:
        for i in range(members):
            location_id = 10000 + i
            index.note(MASKS_FOLDER, f"{key}_{location_id}.png", 0.0)
            index.note(IGNORED_FOLDER, f"{key}_{location_id}.png", 0.0)
            index.note(RLE_FOLDER, f"{key}_{location_id}.json", 0.0)
    return output, index, names, {10000 + i for i in range(members)}


def _run(output: Path, index: CropProductIndex, names: list[str], keep: set[int]) -> None:
    for key in names:
        remove_member_files_not_in(output, key, keep, products=index)


def main() -> None:
    runner = pyperf.Runner()
    output, index, names, keep = _setup(2000, 10)
    runner.bench_func("remove_member_files_not_in_k2000_m10", _run, output, index, names, keep)


if __name__ == "__main__":
    main()
