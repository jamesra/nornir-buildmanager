"""missing_covering_tiles: the per-pass existence memo must not change results."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect
from nornir_buildmanager.operations.segmentationtraining.stitch import (
    missing_covering_tiles,
    tile_filename,
)

_GRID = 5


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
