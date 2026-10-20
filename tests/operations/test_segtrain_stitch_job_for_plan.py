"""Stitch jobs built from planned crops: full rebuilds in the pipeline and new crops on update."""

from __future__ import annotations

from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    REBUILD_FULL,
    REBUILD_MASK_ONLY,
)
from nornir_buildmanager.operations.segmentationtraining.geometry import (
    CropWindow,
    TileRect,
)
from nornir_buildmanager.operations.segmentationtraining.grouping import CropGroup
from nornir_buildmanager.operations.segmentationtraining.pipeline import (
    _queue_stitch_job,
)
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


class _Products:
    """Product index stub: only ``exists`` is consulted by ``_queue_stitch_job``."""

    _names: set[str]

    def __init__(self, names: set[str] | None = None) -> None:
        self._names = names or set()

    def exists(self, folder: str, name: str) -> bool:
        """True when *name* is recorded under *folder*."""
        return folder == IMAGES_FOLDER and name in self._names


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
def test_queue_stitch_job_full_rebuild_builds_job_from_plan(
    ox: int, oy: int, w: int, h: int, tx: int, ty: int, downsample: int, snap: TileRect
) -> None:
    """A full rebuild without an indexed PNG queues the plan's window, snap and size."""
    tile_shape = Shape.from_xy(x=tx, y=ty)
    plan = _plan(ox, oy, w, h, snap, downsample)
    jobs: list[StitchJob] = []
    mode = _queue_stitch_job(OUTPUT, plan, REBUILD_FULL, tile_shape, False, _Products(), jobs)  # type: ignore[arg-type]
    assert mode == REBUILD_FULL
    image_path = OUTPUT / IMAGES_FOLDER / f"{plan.image_key}.png"
    assert jobs == [_expected(plan, tile_shape, image_path)]


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


def test_queue_stitch_job_existing_png_becomes_mask_only() -> None:
    """An indexed crop PNG skips the restitch unless forced."""
    tile_shape = Shape.from_xy(x=1024, y=1024)
    plan = _plan(512, 0, 1024, 1024, TileRect(0, 2, 0, 1), 1)
    products = _Products({f"{plan.image_key}.png"})
    jobs: list[StitchJob] = []
    assert _queue_stitch_job(OUTPUT, plan, REBUILD_FULL, tile_shape, False, products, jobs) == REBUILD_MASK_ONLY  # type: ignore[arg-type]
    assert jobs == []
    assert _queue_stitch_job(OUTPUT, plan, REBUILD_FULL, tile_shape, True, products, jobs) == REBUILD_FULL  # type: ignore[arg-type]
    assert len(jobs) == 1
