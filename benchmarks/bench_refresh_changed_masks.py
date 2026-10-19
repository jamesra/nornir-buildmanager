"""Micro-benchmark for update._refresh_changed_masks (category 19).

A section re-export where many locations changed: each changed id used to scan every crop
watermark with list membership. Dense case: 3000 crops of 3 members each on a 60x50 grid,
2500 changed ids; rasterizing and disk writes are stubbed so only the lookup is timed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pyperf

from nornir_buildmanager.operations.segmentationtraining import update
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ImageWatermark,
    SectionWatermark,
)
from nornir_buildmanager.operations.segmentationtraining.records import (
    LocationRecord,
    LocationType,
)

OUTPUT = Path("export")


class _Products:
    def note_path(self, path: object) -> None:
        return None


def _setup(crops: int, per_crop: int, changed: int):
    marks = []
    for index in range(crops):
        base = index * per_crop // 2  # neighbouring crops share members
        marks.append(
            ImageWatermark(
                key=f"k{index}", downsample=1, ix0=0, ix1=1, iy0=0, iy1=1,
                member_ids=[base + n for n in range(per_crop)], max_last_modified="",
                origin_x=0, origin_y=0, width=64, height=64,
            )
        )
    previous = SectionWatermark(ids=[], max_last_modified="", tileset_mtime=0.0, params_hash="", images=marks)
    ids = list(range(changed))
    by_id = {
        i: LocationRecord(
            id=i, z=1, wkt="POLYGON((2 2,6 2,6 6,2 6,2 2))", parent_id=None, off_edge=False,
            last_modified=datetime(2020, 1, 1), type_id=1, type_name="Cell", structure_label=None,
            type_code=LocationType.POLYGON,
        )
        for i in ids
    }
    return ids, by_id, previous


def _run(ids, by_id, previous) -> None:
    update._refresh_changed_masks(OUTPUT, ids, by_id, previous, _Products(), mask_workers=1, overlay=False)


def main() -> None:
    update.run_mask_jobs = lambda jobs, workers: None
    update.apply_ignore_moves = lambda output: None
    update._replace_changed_annotations = lambda *a, **k: None
    runner = pyperf.Runner()
    ids, by_id, previous = _setup(3000, 3, 2500)
    runner.bench_func("refresh_changed_masks_c3000_n2500", _run, ids, by_id, previous)


if __name__ == "__main__":
    main()
