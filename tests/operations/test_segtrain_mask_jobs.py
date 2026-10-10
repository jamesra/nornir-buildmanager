"""Tests for building mask raster jobs from planned crops and existing crop windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from nornir_imageregistration.type_info import Shape

from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ExportParams,
    ImageWatermark,
    ResolvedTileset,
)
from nornir_buildmanager.operations.segmentationtraining.maskrefresh import (
    mask_jobs_for_record,
)
from nornir_buildmanager.operations.segmentationtraining.masks import (
    mask_job_for_crop,
    mask_jobs_for_plan,
)
from nornir_buildmanager.operations.segmentationtraining.planning import (
    PlannedCrop,
    plan_section_crops,
)
from nornir_buildmanager.operations.segmentationtraining.records import (
    LocationRecord,
    LocationType,
)

OUTPUT = Path("export")
SQUARE = [(((2.0, 2.0), (6.0, 2.0), (6.0, 6.0), (2.0, 6.0), (2.0, 2.0)),)]
SQUARE_FAR = [(((1e6, 1e6), (1e6 + 4, 1e6), (1e6 + 4, 1e6 + 4), (1e6, 1e6 + 4), (1e6, 1e6)),)]


def _box_wkt(x0: float, y0: float, x1: float, y1: float) -> str:
    """Closed axis-aligned WKT polygon."""
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def _record(loc_id: int, wkt: str) -> LocationRecord:
    """Polygon location on section 1."""
    return LocationRecord(
        id=loc_id,
        z=1,
        wkt=wkt,
        parent_id=10,
        off_edge=False,
        last_modified=datetime(2020, 1, 1),
        type_id=1,
        type_name="Cell",
        structure_label=None,
        type_code=LocationType.POLYGON,
    )


def _plan(records: list[LocationRecord], *, tile: int = 1024, downsample: int = 1):
    """Plan section crops with a fixed tileset; returns (plans, polygons_by_id)."""
    shape = Shape.from_xy(x=tile, y=tile)
    params = ExportParams(
        pad=0.0,
        downsample=downsample,
        max_texture=1024,
        min_process_pixels=16,
        include_off_edge=False,
        channel="TEM",
        filter_name="Leveled",
        volume="Vol",
        tile_shape=shape,
    )
    tileset = ResolvedTileset(
        tile_shape=shape, available=[1, 2, 4, 8], level_dirs={}, prefix="", postfix=".png", mtime=None
    )
    return plan_section_crops(records, params=params, tileset=tileset)


def test_mask_job_for_crop_owns_mask_and_rle_layout() -> None:
    job = mask_job_for_crop(
        OUTPUT, 7, "Vol_1_D1_X0-1_Y0-1", SQUARE, origin_x=0.0, origin_y=0.0, downsample=1.0, width=8, height=8
    )
    assert job is not None
    assert job.location_id == 7
    assert job.image_key == "Vol_1_D1_X0-1_Y0-1"
    assert (job.width, job.height) == (8, 8)
    assert job.mask_path == str(OUTPUT / "masks" / "Vol_1_D1_X0-1_Y0-1_7.png")
    assert job.rle_path == str(OUTPUT / "_work" / "rle" / "Vol_1_D1_X0-1_Y0-1_7.json")
    assert len(job.rings) == 1
    assert isinstance(job.rings, tuple)


def test_mask_job_for_crop_honours_origin_and_downsample() -> None:
    near = mask_job_for_crop(
        OUTPUT, 1, "k", SQUARE, origin_x=0.0, origin_y=0.0, downsample=1.0, width=8, height=8
    )
    # Shift only x so a swapped origin_x/origin_y shows up in either axis.
    shifted = mask_job_for_crop(
        OUTPUT, 1, "k", SQUARE, origin_x=2.0, origin_y=0.0, downsample=2.0, width=8, height=8
    )
    unshifted = mask_job_for_crop(
        OUTPUT, 1, "k", SQUARE, origin_x=0.0, origin_y=0.0, downsample=2.0, width=8, height=8
    )
    assert near is not None and shifted is not None and unshifted is not None
    assert sorted({x for x, _ in near.rings[0][0]}) == [2, 6]
    assert sorted({x for x, _ in shifted.rings[0][0]}) == [0, 2]
    assert sorted({x for x, _ in unshifted.rings[0][0]}) == [1, 3]
    assert {y for _, y in shifted.rings[0][0]} == {y for _, y in unshifted.rings[0][0]}


def test_mask_job_for_crop_returns_none_outside_window() -> None:
    assert (
        mask_job_for_crop(OUTPUT, 1, "k", SQUARE, origin_x=500.0, origin_y=500.0, downsample=1.0, width=8, height=8)
        is None
    )


def test_mask_jobs_for_plan_keeps_member_order_and_skips_misses() -> None:
    records = [_record(5, _box_wkt(10, 10, 60, 60)), _record(3, _box_wkt(20, 20, 50, 50))]
    plans, polygons = _plan(records)
    assert len(plans) == 1
    plan = plans[0]
    assert sorted(plan.location_ids) == [3, 5]
    jobs = mask_jobs_for_plan(OUTPUT, plan, polygons)
    assert [job.location_id for job in jobs] == plan.location_ids
    width, height = plan.output_size()
    assert all((job.width, job.height) == (width, height) for job in jobs)

    far = dict(polygons)
    far[plan.location_ids[0]] = SQUARE_FAR
    jobs = mask_jobs_for_plan(OUTPUT, plan, far)
    assert [job.location_id for job in jobs] == plan.location_ids[1:]


@dataclass
class _WidePlan:
    """Stand-in for a non-square planned crop, so a width/height mix-up is visible."""

    image_key: str = "wide"
    location_ids: tuple[int, ...] = (4,)
    downsample: int = 1

    def output_size(self) -> tuple[int, int]:
        return 16, 8

    def mosaic_origin(self) -> tuple[float, float]:
        return 0.0, 0.0


def test_mask_jobs_for_plan_keeps_width_and_height_apart() -> None:
    wide_box = [(((10.0, 2.0), (14.0, 2.0), (14.0, 6.0), (10.0, 6.0), (10.0, 2.0)),)]
    (job,) = mask_jobs_for_plan(OUTPUT, cast(PlannedCrop, _WidePlan()), {4: wide_box})
    assert (job.width, job.height) == (16, 8)
    assert sorted({x for x, _ in job.rings[0][0]}) == [10, 14]


def _mark(key: str, *, downsample: int, origin: tuple[int, int], members: list[int]) -> ImageWatermark:
    """8x8 crop watermark at *origin* (crop pixels at *downsample*)."""
    return ImageWatermark(
        key=key, downsample=downsample, ix0=0, ix1=1, iy0=0, iy1=1, member_ids=members,
        max_last_modified="", origin_x=origin[0], origin_y=origin[1], width=8, height=8,
    )


def test_mask_jobs_for_record_redraws_only_member_windows_it_reaches() -> None:
    record = _record(3, _box_wkt(4, 4, 12, 12))
    marks = [
        _mark("A", downsample=2, origin=(1, 0), members=[3]),
        _mark("far", downsample=1, origin=(500, 500), members=[3]),
        _mark("other", downsample=1, origin=(0, 0), members=[7]),
    ]
    jobs = mask_jobs_for_record(OUTPUT, record, marks)
    assert [(job.image_key, job.location_id) for job in jobs] == [("A", 3)]
    (job,) = jobs
    # Watermark origins are crop pixels; mosaic origin is origin * downsample = 2.
    assert sorted({x for x, _ in job.rings[0][0]}) == [1, 5]
    assert job.mask_path == str(OUTPUT / "masks" / "A_3.png")


@pytest.mark.parametrize("downsample", [1, 2, 4])
def test_mask_jobs_for_plan_maps_mosaic_x_through_plan_window(downsample: int) -> None:
    plans, polygons = _plan([_record(5, _box_wkt(100, 100, 300, 300))], downsample=downsample)
    plan = plans[0]
    origin_x, _ = plan.mosaic_origin()
    (job,) = mask_jobs_for_plan(OUTPUT, plan, polygons)
    xs = [x for x, _ in job.rings[0][0]]
    assert min(xs) == round((100 - origin_x) / downsample)
    assert max(xs) == round((300 - origin_x) / downsample)


@st.composite
def _sections(draw: st.DrawFn) -> tuple[list[PlannedCrop], dict, int]:
    """Boxes at real mosaic magnitudes, planned into crops."""
    base = draw(st.sampled_from([0.0, 1500.0, 150000.0]))
    count = draw(st.integers(1, 6))
    records = []
    for loc in range(count):
        x0 = base + draw(st.floats(0, 5000))
        y0 = base + draw(st.floats(0, 5000))
        w = draw(st.floats(5, 2500))
        h = draw(st.floats(5, 2500))
        records.append(_record(loc + 1, _box_wkt(x0, y0, x0 + w, y0 + h)))
    tile = draw(st.sampled_from([256, 512, 1024]))
    downsample = draw(st.sampled_from([1, 2, 4]))
    plans, polygons = _plan(records, tile=tile, downsample=downsample)
    return plans, polygons, count


@given(_sections())
@settings(max_examples=60, deadline=None)
def test_mask_jobs_for_plan_one_unique_in_bounds_job_per_member(section) -> None:
    plans, polygons, _ = section
    seen: set[str] = set()
    for plan in plans:
        width, height = plan.output_size()
        jobs = mask_jobs_for_plan(OUTPUT, plan, polygons)
        ids = [job.location_id for job in jobs]
        assert ids == [i for i in plan.location_ids if i in ids]
        for job in jobs:
            assert job.image_key == plan.image_key
            assert (job.width, job.height) == (width, height)
            assert job.mask_path not in seen
            seen.add(job.mask_path)
            for polygon in job.rings:
                for ring in polygon:
                    assert all(0 <= x <= width and 0 <= y <= height for x, y in ring)
        # Planning only places a location in crops it overlaps, so every member draws something.
        assert len(jobs) == len(plan.location_ids)
