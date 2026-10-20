"""missing_covering_tiles: the per-pass existence memo must not change results."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from nornir_imageregistration.type_info import Shape

from nornir_buildmanager.operations.segmentationtraining import pipeline
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ImageWatermark,
    ResolvedTileset,
    SectionCropRun,
    SectionWatermark,
)
from nornir_buildmanager.operations.segmentationtraining.geometry import (
    CropWindow,
    TileRect,
)
from nornir_buildmanager.operations.segmentationtraining.grouping import CropGroup
from nornir_buildmanager.operations.segmentationtraining.planning import PlannedCrop
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    get_product_index,
)
from nornir_buildmanager.operations.segmentationtraining.stitch import (
    missing_covering_tiles,
    tile_filename,
)

_GRID = 5
_TILE_PX = 8
#: 3x3 half-step crop windows of 2x2 tiles over a 4x4 tile grid: 36 visits, 16 distinct tiles.
_HALF_STEP_ORIGINS = [(x * _TILE_PX, y * _TILE_PX) for y in range(3) for x in range(3)]


def _reference(tiles: TileRect, level_dir: str | None, prefix: str, postfix: str) -> list[tuple[int, int]]:
    """Pre-memo implementation: full coordinate list, then stat in row-major order."""
    coords = [
        (ix, iy)
        for iy in range(tiles.iy0, tiles.iy1)
        for ix in range(tiles.ix0, tiles.ix1)
        if ix >= 0 and iy >= 0
    ]
    if not coords:
        return []
    if not level_dir or not os.path.isdir(level_dir):
        return coords[:1]
    for ix, iy in coords:
        if not os.path.isfile(os.path.join(level_dir, tile_filename(prefix, postfix, ix, iy))):
            return [(ix, iy)]
    return []


def _make_level(root: Path, present: set[tuple[int, int]]) -> str:
    level = root / "001"
    level.mkdir(parents=True, exist_ok=True)
    for ix, iy in present:
        (level / tile_filename("", ".png", ix, iy)).write_bytes(b"x")
    return str(level)


@st.composite
def _rects(draw: st.DrawFn) -> TileRect:
    ix0 = draw(st.integers(-2, _GRID))
    iy0 = draw(st.integers(-2, _GRID))
    return TileRect(ix0, ix0 + draw(st.integers(0, 4)), iy0, iy0 + draw(st.integers(0, 4)))


@settings(max_examples=60, deadline=None)
@given(
    st.sets(st.tuples(st.integers(0, _GRID), st.integers(0, _GRID))),
    st.lists(_rects(), min_size=1, max_size=8),
)
@example(set(), [TileRect(-2, 1, -2, 1)])
@example({(0, 0)}, [TileRect(0, 2, 0, 1), TileRect(0, 2, 0, 1)])
def test_memo_matches_reference_over_overlapping_windows(
    tmp_path_factory: pytest.TempPathFactory,
    present: set[tuple[int, int]],
    rects: list[TileRect],
) -> None:
    """One shared memo across many windows gives the same answer as a fresh stat each time."""
    level = _make_level(tmp_path_factory.mktemp("lvl"), present)
    known: dict[tuple[str, int, int], bool] = {}
    for rect in rects:
        expected = _reference(rect, level, "", ".png")
        assert missing_covering_tiles(rect, level, "", ".png") == expected
        assert missing_covering_tiles(rect, level, "", ".png", known) == expected


def test_missing_or_empty_level_dir(tmp_path: Path) -> None:
    """A missing level directory reports the first on-mosaic coordinate; off-mosaic windows report none."""
    known: dict[tuple[str, int, int], bool] = {}
    gone = str(tmp_path / "nope")
    assert missing_covering_tiles(TileRect(-1, 2, -1, 2), gone, "", ".png", known) == [(0, 0)]
    assert missing_covering_tiles(TileRect(-3, 0, 0, 2), gone, "", ".png", known) == []
    assert missing_covering_tiles(TileRect(0, 2, 0, 2), None, "", ".png") == [(0, 0)]
    assert known == {}


def test_overlapping_windows_stat_each_tile_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Half-step windows over a complete grid cost one isfile per tile with a memo, one per visit without."""
    level = _make_level(tmp_path, {(x, y) for x in range(4) for y in range(4)})
    windows = [TileRect(x, x + 2, y, y + 2) for y in range(3) for x in range(3)]
    calls: list[str] = []
    real = os.path.isfile
    monkeypatch.setattr(os.path, "isfile", lambda p: calls.append(p) or real(p))

    known: dict[tuple[str, int, int], bool] = {}
    for rect in windows:
        assert missing_covering_tiles(rect, level, "", ".png", known) == []
    assert len(calls) == 16
    calls.clear()
    for rect in windows:
        assert missing_covering_tiles(rect, level, "", ".png") == []
    assert len(calls) == 36


def test_memo_remembers_absence_only_for_its_own_pass(tmp_path: Path) -> None:
    """A tile that appears later is seen by a new pass, not hidden by a stale memo."""
    level = _make_level(tmp_path, {(0, 0)})
    rect = TileRect(0, 2, 0, 1)
    first: dict[tuple[str, int, int], bool] = {}
    assert missing_covering_tiles(rect, level, "", ".png", first) == [(1, 0)]
    (Path(level) / tile_filename("", ".png", 1, 0)).write_bytes(b"x")
    assert missing_covering_tiles(rect, level, "", ".png", {}) == []


def _complete_tileset(tmp_path: Path) -> ResolvedTileset:
    """Local 4x4 tileset with every tile present, so no pass drops or deletes anything."""
    level = _make_level(tmp_path / "tiles", {(x, y) for x in range(4) for y in range(4)})
    return ResolvedTileset(
        tile_shape=Shape.from_xy(x=_TILE_PX, y=_TILE_PX),
        available=[1],
        level_dirs={1: level},
        prefix="",
        postfix=".png",
        mtime=1.0,
    )


def _count_level_stats(monkeypatch: pytest.MonkeyPatch, level: str) -> list[str]:
    """Record every os.path.isfile call that lands in *level*; other stats are not counted."""
    calls: list[str] = []
    real: Callable[[str], bool] = os.path.isfile
    root = os.path.normcase(os.path.abspath(level))

    def counting(path: str) -> bool:
        if os.path.normcase(os.path.abspath(os.fspath(path))).startswith(root):
            calls.append(os.fspath(path))
        return real(path)

    monkeypatch.setattr(os.path, "isfile", counting)
    return calls


def _half_step_marks() -> list[ImageWatermark]:
    """Recorded crops for the half-step windows, each with its pixel extent."""
    return [
        ImageWatermark(
            key=f"Vol_2_D1_{ox}_{oy}",
            downsample=1,
            ix0=ox // _TILE_PX,
            ix1=ox // _TILE_PX + 2,
            iy0=oy // _TILE_PX,
            iy1=oy // _TILE_PX + 2,
            member_ids=[index],
            max_last_modified="",
            origin_x=ox,
            origin_y=oy,
            width=2 * _TILE_PX,
            height=2 * _TILE_PX,
        )
        for index, (ox, oy) in enumerate(_HALF_STEP_ORIGINS)
    ]


def test_retain_plans_with_tiles_stats_each_tile_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """retain_plans_with_tiles shares one memo across its plans: 16 stats for 36 tile visits."""
    tileset = _complete_tileset(tmp_path)
    output = tmp_path / "export"
    plans = [
        PlannedCrop(
            group=CropGroup(
                snap=TileRect(ox // _TILE_PX, ox // _TILE_PX + 2, oy // _TILE_PX, oy // _TILE_PX + 2),
                location_ids=[index],
                downsample=1,
                window=CropWindow(ox, oy, 2 * _TILE_PX, 2 * _TILE_PX),
            ),
            z=2,
            image_key=f"Vol_2_D1_{ox}_{oy}",
        )
        for index, (ox, oy) in enumerate(_HALF_STEP_ORIGINS)
    ]
    products = get_product_index(output)
    calls = _count_level_stats(monkeypatch, tileset.level_dirs[1])
    kept = pipeline.retain_plans_with_tiles(output, 2, plans, tileset, products)
    assert kept == plans
    assert len(calls) == 16


def test_drop_recorded_crops_missing_tiles_stats_each_tile_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """drop_recorded_crops_missing_tiles shares one memo across its marks: 16 stats for 36 visits."""
    tileset = _complete_tileset(tmp_path)
    output = tmp_path / "export"
    marks = _half_step_marks()
    watermark = SectionWatermark(
        ids=[mark.member_ids[0] for mark in marks],
        max_last_modified="",
        tileset_mtime=1.0,
        params_hash="abc",
        downsample=1,
        image_keys=[mark.key for mark in marks],
        images=marks,
    )
    calls = _count_level_stats(monkeypatch, tileset.level_dirs[1])
    assert not pipeline.drop_recorded_crops_missing_tiles(output, 2, watermark, tileset)
    assert len(calls) == 16


def test_stitch_missing_crop_marks_stats_each_tile_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The repair loop shares one memo across its marks: 16 stats for 36 visits, every crop queued."""
    tileset = _complete_tileset(tmp_path)
    queued: list[int] = []

    def fake_run(jobs: list[object], **_kwargs: object) -> list[tuple[str, float]]:
        queued.append(len(jobs))
        return []

    monkeypatch.setattr(pipeline, "_run_stitch_jobs", fake_run)
    monkeypatch.setattr(pipeline, "ignore_blank_crops", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "mark_catalog_dirty", lambda: None)
    run = cast(SectionCropRun, SimpleNamespace(tileset=tileset, workers=1, stage_tiles=None))
    calls = _count_level_stats(monkeypatch, tileset.level_dirs[1])
    written = pipeline.stitch_missing_crop_marks(
        output_path=tmp_path / "export", z=2, marks=_half_step_marks(), run=run
    )
    assert written == len(_HALF_STEP_ORIGINS)
    assert queued == [len(_HALF_STEP_ORIGINS)]
    assert len(calls) == 16
