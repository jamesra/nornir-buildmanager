"""Stitch jobs built from planned crops: full rebuilds in the pipeline and new crops on update.

The pipeline's full-rebuild versus mask-only choice is pinned through
``export_section_crops`` in ``test_segtrain_export_classify.py``.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st
from nornir_buildmanager.operations.segmentationtraining.geometry import (
    CropWindow,
    TileRect,
)
from nornir_buildmanager.operations.segmentationtraining.grouping import CropGroup
from nornir_buildmanager.operations.segmentationtraining.planning import PlannedCrop
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    IMAGES_FOLDER,
)
from nornir_buildmanager.operations.segmentationtraining.stitch import (
    StitchJob,
    stitch_job_for_plan,
)
from nornir_imageregistration.type_info import Shape

OUTPUT = Path("export")

# Mosaic pixel coordinates reach the hundreds of thousands at D1.
_coord = st.integers(min_value=-(2**20), max_value=2**20)
_extent = st.integers(min_value=1, max_value=8192)
_tile = st.integers(min_value=1, max_value=4096)


def _plan(ox: int, oy: int, w: int, h: int, snap: TileRect, downsample: int) -> PlannedCrop:
    """Planned crop whose group snap is independent of its pixel window."""
    return PlannedCrop(
        group=CropGroup(snap=snap, location_ids=[1], downsample=downsample, window=CropWindow(ox, oy, w, h)),
        z=3,
        image_key=f"Vol_3_D{downsample}_{ox}_{oy}",
    )


def _expected(plan: PlannedCrop, tile_shape: Shape, image_path: Path) -> StitchJob:
    """Reference job: crop offset is the window origin modulo the tile size."""
    window = plan.window
    return StitchJob(
        image_key=plan.image_key,
        snap=plan.snap,
        downsample=plan.downsample,
        tile_shape=tile_shape,
        image_path=str(image_path),
        crop_x=window.origin_x % tile_shape.x,
        crop_y=window.origin_y % tile_shape.y,
        crop_width=window.width,
        crop_height=window.height,
    )


@settings(max_examples=200, deadline=None)
@given(
    ox=_coord,
    oy=_coord,
    w=_extent,
    h=_extent,
    tx=_tile,
    ty=_tile,
    downsample=st.sampled_from([1, 2, 4, 8, 16]),
    snap=st.builds(TileRect, st.integers(-50, 50), st.integers(51, 100), st.integers(-50, 50), st.integers(51, 100)),
)
@example(ox=1536, oy=512, w=1024, h=2048, tx=1024, ty=1024, downsample=1, snap=TileRect(1, 3, 0, 3))
@example(ox=-300, oy=700, w=10, h=20, tx=512, ty=256, downsample=2, snap=TileRect(-1, 0, 2, 4))
def test_stitch_job_for_plan_keeps_group_snap(
    ox: int, oy: int, w: int, h: int, tx: int, ty: int, downsample: int, snap: TileRect
) -> None:
    """The job takes the plan's own snap, window size and tile-local offset."""
    tile_shape = Shape.from_xy(x=tx, y=ty)
    plan = _plan(ox, oy, w, h, snap, downsample)
    image_path = OUTPUT / IMAGES_FOLDER / f"{plan.image_key}.png"
    assert stitch_job_for_plan(plan, tile_shape, image_path) == _expected(plan, tile_shape, image_path)


@settings(max_examples=200, deadline=None)
@given(
    ox=_coord,
    oy=_coord,
    w=_extent,
    h=_extent,
    tx=_tile,
    ty=_tile,
    downsample=st.sampled_from([1, 2, 4, 8, 16]),
)
@example(ox=1536, oy=512, w=1024, h=2048, tx=1024, ty=512, downsample=4)
def test_stitch_job_for_plan_matches_reference(
    ox: int, oy: int, w: int, h: int, tx: int, ty: int, downsample: int
) -> None:
    """The shared builder (update's new-crop path) keeps window size and tile-local offset."""
    tile_shape = Shape.from_xy(x=tx, y=ty)
    snap = CropWindow(ox, oy, w, h).covering_tiles(tx, ty)
    plan = _plan(ox, oy, w, h, snap, downsample)
    image_path = OUTPUT / IMAGES_FOLDER / "crop.png"
    assert stitch_job_for_plan(plan, tile_shape, image_path) == _expected(plan, tile_shape, image_path)
