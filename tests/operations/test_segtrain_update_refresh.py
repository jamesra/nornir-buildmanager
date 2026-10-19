"""Equivalence of the member-id index used by update._refresh_changed_masks."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nornir_buildmanager.operations.segmentationtraining import update
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ImageWatermark,
    SectionWatermark,
)
from nornir_buildmanager.operations.segmentationtraining.maskrefresh import (
    mask_jobs_for_record,
)
from nornir_buildmanager.operations.segmentationtraining.records import (
    LocationRecord,
    LocationType,
)

OUTPUT = Path("export")


def _record(loc_id: int) -> LocationRecord:
    """Small polygon location on section 1."""
    x = float(loc_id)
    wkt = f"POLYGON(({x} 0,{x + 4} 0,{x + 4} 4,{x} 4,{x} 0))"
    return LocationRecord(
        id=loc_id, z=1, wkt=wkt, parent_id=None, off_edge=False, last_modified=datetime(2020, 1, 1),
        type_id=1, type_name="Cell", structure_label=None, type_code=LocationType.POLYGON,
    )


def _mark(key: str, members: list[int], width: int = 64, height: int = 64) -> ImageWatermark:
    """Crop watermark at the origin."""
    return ImageWatermark(
        key=key, downsample=1, ix0=0, ix1=1, iy0=0, iy1=1, member_ids=members,
        max_last_modified="", origin_x=0, origin_y=0, width=width, height=height,
    )


def _reference(output: Path, record: LocationRecord, marks: list[ImageWatermark]) -> list[tuple[str, int]]:
    """Old behavior: hand every valid mark to mask_jobs_for_record."""
    valid = [m for m in marks if m.width > 0 and m.height > 0]
    return [(j.image_key, j.location_id) for j in mask_jobs_for_record(output, record, valid)]


@st.composite
def _marks(draw: st.DrawFn) -> list[ImageWatermark]:
    count = draw(st.integers(0, 8))
    marks = []
    for index in range(count):
        members = draw(st.lists(st.integers(1, 6), max_size=6))  # duplicates allowed
        size = draw(st.sampled_from([0, 64]))
        marks.append(_mark(f"k{index}", members, width=size, height=size))
    return marks


@given(_marks(), st.lists(st.integers(1, 6), unique=True, max_size=6))
@settings(max_examples=80, deadline=None)
def test_refresh_changed_masks_jobs_match_full_scan(marks: list[ImageWatermark], changed: list[int]) -> None:
    captured: list[Any] = []
    previous = SectionWatermark(
        ids=[], max_last_modified="", tileset_mtime=0.0, params_hash="", downsample=1, image_keys=[], images=marks
    )
    by_id = {i: _record(i) for i in changed}
    expected: list[tuple[str, int]] = []
    for location_id in changed:
        expected.extend(_reference(OUTPUT, by_id[location_id], marks))

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(update, "run_mask_jobs", lambda jobs, workers: captured.extend(jobs))
        mp.setattr(update, "apply_ignore_moves", lambda output: None)
        mp.setattr(update, "_replace_changed_annotations", lambda *a, **k: None)

        class _Products:
            def note_path(self, path: Any) -> None:
                return None

        update._refresh_changed_masks(
            OUTPUT, changed, by_id, previous, _Products(), mask_workers=1, overlay=False  # type: ignore[arg-type]
        )
    finally:
        mp.undo()
    assert [(j.image_key, j.location_id) for j in captured] == expected


def test_marks_by_member_id_keeps_order_and_dedupes() -> None:
    a, b = _mark("a", [1, 1, 2]), _mark("b", [2, 1])
    index = update._marks_by_member_id([a, b])
    assert index[1] == [a, b]
    assert index[2] == [a, b]
