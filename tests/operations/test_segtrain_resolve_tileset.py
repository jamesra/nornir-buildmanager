"""Characterization tests for ``pipeline._resolve_tileset``.

The example tests pin each branch (override shortcut, missing filter or tileset,
level scan, mtime fallback, prefix/postfix defaults, PNG size reconciliation).
The Hypothesis test compares against a frozen copy of the pre-refactor body.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from nornir_imageregistration.type_info import Shape

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.segmentationtraining import pipeline
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ResolvedTileset,
)
from nornir_buildmanager.operations.segmentationtraining.pipeline import (
    _resolve_tileset,
)

MISSING_DIR = "Z:/does/not/exist/resolve_tileset"


class _Level:
    """Stand-in for a VolumeData ``Level`` node."""

    Downsample: int
    FullPath: str
    ValidationTime: Any

    def __init__(self, downsample: int, path: str, validation: Any = None) -> None:
        self.Downsample = downsample
        self.FullPath = path
        self.ValidationTime = validation


class _Tileset:
    """Stand-in for a VolumeData ``Tileset`` node."""

    TileXDim: Any
    TileYDim: Any
    FilePrefix: Any
    FilePostfix: Any
    Levels: Any

    def __init__(
        self,
        levels: list[_Level] | None,
        *,
        x: Any = 512,
        y: Any = 512,
        prefix: Any = "Leveled_",
        postfix: Any = ".png",
    ) -> None:
        self.TileXDim = x
        self.TileYDim = y
        self.FilePrefix = prefix
        self.FilePostfix = postfix
        self.Levels = levels


class _Filter:
    """Stand-in for a VolumeData ``Filter`` node."""

    Tileset: Any

    def __init__(self, tileset: Any, full_path: str | None = "TEM/Leveled/path") -> None:
        self.Tileset = tileset
        if full_path is not None:
            self.FullPath = full_path


def _ts(seconds: float) -> datetime:
    """Naive datetime ``seconds`` after 2020-01-01 (local time, like VolumeData)."""
    return datetime(2020, 1, 1) + timedelta(seconds=seconds)


def _kwargs(**overrides: Any) -> dict[str, Any]:
    """Default keyword arguments for ``_resolve_tileset`` with overrides applied."""
    base: dict[str, Any] = {
        "downsample": 1,
        "fail_missing": False,
        "tile_x_dim": None,
        "tile_y_dim": None,
        "level_dirs": None,
        "file_prefix": None,
        "file_postfix": None,
        "tileset_mtime": None,
        "available": None,
        "channel_name": "TEM",
        "filter_name": "Leveled",
    }
    base.update(overrides)
    return base


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture ``prettyoutput.Log`` messages."""
    captured: list[str] = []
    monkeypatch.setattr(pipeline.prettyoutput, "Log", lambda message, *a, **k: captured.append(message))
    return captured


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Replace the PNG probe with one returning ``None``; records (dir, postfix)."""
    calls: list[tuple[str, str]] = []

    def _probe(level_dir: str, *, postfix: str = ".png", samples: int = 32) -> tuple[int, int] | None:
        calls.append((level_dir, postfix))
        return None

    monkeypatch.setattr(pipeline, "probe_tile_pixel_size", _probe)
    return calls


def _set_probe(monkeypatch: pytest.MonkeyPatch, value: tuple[int, int] | None) -> list[tuple[str, str]]:
    """Make the PNG probe return ``value``; returns the recorded calls."""
    calls: list[tuple[str, str]] = []

    def _probe(level_dir: str, *, postfix: str = ".png", samples: int = 32) -> tuple[int, int] | None:
        calls.append((level_dir, postfix))
        return value

    monkeypatch.setattr(pipeline, "probe_tile_pixel_size", _probe)
    return calls


# --- override shortcut -------------------------------------------------------


def test_override_shortcut_ignores_filter_and_defaults(probes: list[tuple[str, str]]) -> None:
    fetch = lambda d, coords: None
    info = _resolve_tileset(
        None,
        fetch_tiles=fetch,
        **_kwargs(tile_x_dim="256", tile_y_dim=128.0, level_dirs={"4": 1, 1: "a", 2: "b"}, available=[1]),
    )
    assert info is not None
    assert info == ResolvedTileset(
        tile_shape=Shape.from_xy(x=256, y=128),
        available=[1],
        level_dirs={4: "1", 1: "a", 2: "b"},
        prefix="",
        postfix=".png",
        mtime=None,
        fetch_tiles=fetch,
    )
    assert info.save_node is None
    assert info.fetch_tiles is fetch
    assert probes == []


def test_override_shortcut_available_sorted_level_dirs_keys(probes: list[tuple[str, str]]) -> None:
    info = _resolve_tileset(None, **_kwargs(tile_x_dim=512, tile_y_dim=512, level_dirs={8: "c", 1: "a", 4: "b"}))
    assert info is not None
    assert info.available == [1, 4, 8]
    assert info.level_dirs == {8: "c", 1: "a", 4: "b"}


def test_override_shortcut_explicit_values_win(probes: list[tuple[str, str]]) -> None:
    info = _resolve_tileset(
        None,
        **_kwargs(
            tile_x_dim=512,
            tile_y_dim=1024,
            level_dirs={1: "a"},
            available=[2, 1],
            file_prefix="P_",
            file_postfix=".tif",
            tileset_mtime=12.5,
        ),
    )
    assert info is not None
    assert info.available == [2, 1]
    assert (info.prefix, info.postfix, info.mtime) == ("P_", ".tif", 12.5)
    assert (info.tile_shape.x, info.tile_shape.y) == (512, 1024)


def test_override_shortcut_empty_prefix_becomes_default(probes: list[tuple[str, str]]) -> None:
    info = _resolve_tileset(
        None, **_kwargs(tile_x_dim=1, tile_y_dim=1, level_dirs={}, file_prefix="", file_postfix="", available=[])
    )
    assert info is not None
    assert (info.prefix, info.postfix, info.available, info.level_dirs) == ("", ".png", [], {})


@pytest.mark.parametrize("dims", [(0, 512), (512, 0), (None, 512)])
def test_override_needs_both_dims_else_uses_filter(
    dims: tuple[Any, Any], probes: list[tuple[str, str]], logs: list[str]
) -> None:
    info = _resolve_tileset(None, **_kwargs(tile_x_dim=dims[0], tile_y_dim=dims[1], level_dirs={1: "a"}))
    assert info is None
    assert logs == ["ExportTrainingData needs a Filter with a Tileset"]


# --- missing filter / tileset ------------------------------------------------


@pytest.mark.parametrize("node", [None, object()])
def test_missing_filter_logs_and_returns_none(node: Any, logs: list[str]) -> None:
    assert _resolve_tileset(node, **_kwargs()) is None
    assert logs == ["ExportTrainingData needs a Filter with a Tileset"]


@pytest.mark.parametrize("node", [None, object()])
def test_missing_filter_raises_when_fail_missing(node: Any, logs: list[str]) -> None:
    with pytest.raises(NornirUserException, match="^ExportTrainingData needs a Filter with a Tileset$"):
        _resolve_tileset(node, **_kwargs(fail_missing=True))
    assert logs == []


def test_tileset_none_message_uses_full_path(logs: list[str]) -> None:
    assert _resolve_tileset(_Filter(None, "X/Y/Z"), **_kwargs()) is None
    assert logs == ["No Tileset on filter X/Y/Z"]


def test_tileset_none_message_falls_back_to_channel_filter(logs: list[str]) -> None:
    with pytest.raises(NornirUserException) as excinfo:
        _resolve_tileset(_Filter(None, None), **_kwargs(fail_missing=True, channel_name="SEM", filter_name="Raw8"))
    assert str(excinfo.value) == "No Tileset on filter SEM/Raw8"
    assert logs == []


# --- level scan and defaults -------------------------------------------------


def test_level_scan_available_dirs_and_finest_probe(probes: list[tuple[str, str]], logs: list[str]) -> None:
    levels = [
        _Level(4, "d4", _ts(30)),
        _Level(1, "d1", _ts(10)),
        _Level(0, "d0"),
        _Level(2, "d2", _ts(20)),
        _Level(4, "d4b"),
    ]
    tileset = _Tileset(levels, x=256, y=128, prefix=None, postfix=None)
    info = _resolve_tileset(_Filter(tileset), fetch_tiles=lambda d, c: None, **_kwargs(downsample=8))
    assert info is not None
    assert info.available == [1, 2, 4]
    assert info.level_dirs == {4: "d4b", 1: "d1", 0: "d0", 2: "d2"}
    assert probes == [("d0", ".png")]
    assert (info.prefix, info.postfix) == ("", ".png")
    assert info.mtime == _ts(30).timestamp()
    assert (info.tile_shape.x, info.tile_shape.y) == (256, 128)
    assert info.save_node is None
    assert info.fetch_tiles is None
    assert logs == []


def test_explicit_args_override_tileset_fields(probes: list[tuple[str, str]]) -> None:
    tileset = _Tileset([_Level(2, "d2", _ts(5))], x=512, y=512)
    info = _resolve_tileset(
        _Filter(tileset),
        **_kwargs(
            tile_x_dim=1024,
            file_prefix="",
            file_postfix=".tif",
            tileset_mtime=0.0,
            available=[9],
            level_dirs={1: "ignored"},
        ),
    )
    assert info is not None
    assert (info.tile_shape.x, info.tile_shape.y) == (1024, 512)
    assert (info.prefix, info.postfix, info.mtime, info.available) == ("", ".tif", 0.0, [9])
    assert info.level_dirs == {2: "d2"}
    assert probes == [("d2", ".tif")]


def test_no_levels_uses_downsample_and_skips_probe(probes: list[tuple[str, str]]) -> None:
    for levels in (None, []):
        tileset = _Tileset(levels, x=None, y=None)
        info = _resolve_tileset(_Filter(tileset), **_kwargs(downsample=4))
        assert info is not None
        assert info.available == [4]
        assert info.level_dirs == {}
        assert info.mtime is None
        assert (info.tile_shape.x, info.tile_shape.y) == (0, 0)
    assert probes == []


def test_mtime_none_when_no_level_has_a_stamp(probes: list[tuple[str, str]]) -> None:
    tileset = _Tileset([_Level(1, MISSING_DIR), _Level(2, MISSING_DIR, datetime.min)])
    info = _resolve_tileset(_Filter(tileset), **_kwargs())
    assert info is not None
    assert info.mtime is None


def test_mtime_from_level_directory(tmp_path: Path, probes: list[tuple[str, str]]) -> None:
    tileset = _Tileset([_Level(1, str(tmp_path))])
    info = _resolve_tileset(_Filter(tileset), **_kwargs())
    assert info is not None
    assert info.mtime == os.path.getmtime(tmp_path)


# --- PNG size reconciliation -------------------------------------------------


def test_probe_matching_declared_size_keeps_node(monkeypatch: pytest.MonkeyPatch, logs: list[str]) -> None:
    _set_probe(monkeypatch, (512, 1024))
    tileset = _Tileset([_Level(1, "d1")], x=512, y=1024)
    info = _resolve_tileset(_Filter(tileset), **_kwargs())
    assert info is not None
    assert info.save_node is None
    assert (tileset.TileXDim, tileset.TileYDim) == (512, 1024)
    assert logs == []


@pytest.mark.parametrize("actual", [(1024, 512), (512, 256), (2048, 2048)])
def test_probe_disagreeing_updates_node(
    actual: tuple[int, int], monkeypatch: pytest.MonkeyPatch, logs: list[str]
) -> None:
    _set_probe(monkeypatch, actual)
    tileset = _Tileset([_Level(1, "d1")], x=512, y=512)
    info = _resolve_tileset(_Filter(tileset), **_kwargs())
    assert info is not None
    assert (info.tile_shape.x, info.tile_shape.y) == actual
    assert (tileset.TileXDim, tileset.TileYDim) == actual
    assert info.save_node is tileset
    assert logs == [
        (
            f"ExportTrainingData: tileset PNG is {actual[0]}x{actual[1]} but "
            "TileXDim/TileYDim is 512x512; updating VolumeData"
        )
    ]


def test_probe_compares_against_explicit_dims(monkeypatch: pytest.MonkeyPatch, logs: list[str]) -> None:
    _set_probe(monkeypatch, (1024, 1024))
    tileset = _Tileset([_Level(1, "d1")], x=512, y=512)
    info = _resolve_tileset(_Filter(tileset), **_kwargs(tile_x_dim=1024, tile_y_dim=1024))
    assert info is not None
    assert info.save_node is None
    assert (tileset.TileXDim, tileset.TileYDim) == (512, 512)


# --- equivalence with the pre-refactor body ----------------------------------


def _old_resolve_tileset(
    filter_node: Any,
    *,
    downsample: int,
    fail_missing: bool,
    tile_x_dim: int | None,
    tile_y_dim: int | None,
    level_dirs: dict[int, str] | None,
    file_prefix: str | None,
    file_postfix: str | None,
    tileset_mtime: float | None,
    available: list[int] | None,
    channel_name: str,
    filter_name: str,
    fetch_tiles: Callable[..., None] | None = None,
) -> ResolvedTileset | None:
    """Frozen copy of ``_resolve_tileset`` before it was split into helpers."""
    if level_dirs is not None and tile_x_dim and tile_y_dim:
        return ResolvedTileset(
            tile_shape=Shape.from_xy(x=int(tile_x_dim), y=int(tile_y_dim)),
            available=available or sorted(level_dirs),
            level_dirs={int(k): str(v) for k, v in level_dirs.items()},
            prefix=file_prefix or "",
            postfix=file_postfix or ".png",
            mtime=tileset_mtime,
            fetch_tiles=fetch_tiles,
        )
    if filter_node is None or not hasattr(filter_node, "Tileset"):
        message = "ExportTrainingData needs a Filter with a Tileset"
        if fail_missing:
            raise NornirUserException(message)
        pipeline.prettyoutput.Log(message)
        return None
    tileset = filter_node.Tileset
    if tileset is None:
        message = f"No Tileset on filter {getattr(filter_node, 'FullPath', channel_name + '/' + filter_name)}"
        if fail_missing:
            raise NornirUserException(message)
        pipeline.prettyoutput.Log(message)
        return None
    levels = list(getattr(tileset, "Levels", []) or [])
    available_levels = sorted({int(level.Downsample) for level in levels if int(level.Downsample) >= 1}) or [downsample]
    dirs: dict[int, str] = {}
    mtimes: list[float] = []
    for level in levels:
        d = int(level.Downsample)
        dirs[d] = level.FullPath
        stamp = pipeline._level_mtime(level)
        if stamp is not None:
            mtimes.append(stamp)
    declared_x = int(tile_x_dim or tileset.TileXDim or 0)
    declared_y = int(tile_y_dim or tileset.TileYDim or 0)
    prefix = file_prefix if file_prefix is not None else (tileset.FilePrefix or "")
    postfix = file_postfix if file_postfix is not None else (tileset.FilePostfix or ".png")
    resolved_mtime = tileset_mtime if tileset_mtime is not None else (max(mtimes) if mtimes else None)
    finest = min(dirs) if dirs else int(downsample)
    sample_dir = dirs.get(finest)
    actual = pipeline.probe_tile_pixel_size(sample_dir, postfix=str(postfix)) if sample_dir else None
    resolved_x, resolved_y = declared_x, declared_y
    save_node = None
    if actual is not None:
        resolved_x, resolved_y = actual
        if resolved_x != declared_x or resolved_y != declared_y:
            pipeline.prettyoutput.Log(
                f"ExportTrainingData: tileset PNG is {resolved_x}x{resolved_y} but "
                f"TileXDim/TileYDim is {declared_x}x{declared_y}; updating VolumeData"
            )
            tileset.TileXDim = resolved_x
            tileset.TileYDim = resolved_y
            save_node = tileset
    return ResolvedTileset(
        tile_shape=Shape.from_xy(x=resolved_x, y=resolved_y),
        available=available or available_levels,
        level_dirs=dirs,
        prefix=prefix,
        postfix=postfix,
        mtime=resolved_mtime,
        save_node=save_node,
    )


_dims = st.sampled_from([None, 0, 256, 512, 1024])
_text = st.sampled_from([None, "", "Leveled_", ".png", ".tif"])
_level = st.builds(
    _Level,
    downsample=st.integers(-1, 16),
    path=st.sampled_from(["", "d_a", "d_b", MISSING_DIR]),
    validation=st.one_of(st.none(), st.just(datetime.min), st.integers(0, 10**8).map(_ts)),
)


@st.composite
def _cases(draw: st.DrawFn) -> dict[str, Any]:
    """Filter node shape, call arguments and probe result for one comparison."""
    node_kind = draw(st.sampled_from(["none", "bare", "no_tileset", "no_tileset_nopath", "tileset", "tileset"]))
    levels = draw(st.one_of(st.none(), st.lists(_level, max_size=5)))
    tileset_args = {
        "x": draw(_dims),
        "y": draw(_dims),
        "prefix": draw(_text),
        "postfix": draw(_text),
    }
    level_dirs = draw(
        st.one_of(st.none(), st.dictionaries(st.integers(1, 16), st.sampled_from(["a", "b"]), max_size=3))
    )
    return {
        "node_kind": node_kind,
        "levels": levels,
        "tileset_args": tileset_args,
        "probe": draw(st.one_of(st.none(), st.tuples(st.sampled_from([256, 512, 1024]), st.sampled_from([256, 512, 1024])))),
        "kwargs": _kwargs(
            downsample=draw(st.integers(1, 8)),
            fail_missing=draw(st.booleans()),
            tile_x_dim=draw(_dims),
            tile_y_dim=draw(_dims),
            level_dirs=level_dirs,
            file_prefix=draw(_text),
            file_postfix=draw(_text),
            tileset_mtime=draw(st.one_of(st.none(), st.floats(0, 1e9))),
            available=draw(st.one_of(st.none(), st.lists(st.integers(1, 16), max_size=3))),
        ),
    }


def _build_node(case: dict[str, Any]) -> tuple[Any, Any]:
    """Fresh filter node (and its tileset) for one call."""
    kind = case["node_kind"]
    if kind == "none":
        return None, None
    if kind == "bare":
        return object(), None
    if kind == "no_tileset":
        return _Filter(None), None
    if kind == "no_tileset_nopath":
        return _Filter(None, None), None
    levels = None if case["levels"] is None else [_Level(lv.Downsample, lv.FullPath, lv.ValidationTime) for lv in case["levels"]]
    tileset = _Tileset(levels, **case["tileset_args"])
    return _Filter(tileset), tileset


def _run(func: Callable[..., Any], case: dict[str, Any], fetch: Any) -> tuple[Any, ...]:
    """Call ``func`` on a fresh node; return result, error, logs, probe calls and node dims."""
    logs: list[str] = []
    probes: list[tuple[str, str]] = []

    def _probe(level_dir: str, *, postfix: str = ".png", samples: int = 32) -> tuple[int, int] | None:
        probes.append((level_dir, postfix))
        return case["probe"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline.prettyoutput, "Log", lambda message, *a, **k: logs.append(message))
        mp.setattr(pipeline, "probe_tile_pixel_size", _probe)
        node, tileset = _build_node(case)
        result: Any = None
        error: str | None = None
        try:
            result = func(node, fetch_tiles=fetch, **case["kwargs"])
        except NornirUserException as exc:
            error = str(exc)
    if result is not None:
        assert result.save_node in (None, tileset)
        result = (result.tile_shape, result.available, result.level_dirs, result.prefix,
                  result.postfix, result.mtime, result.save_node is not None, result.fetch_tiles)
    dims = None if tileset is None else (tileset.TileXDim, tileset.TileYDim)
    return result, error, logs, probes, dims


@settings(max_examples=1500, deadline=None)
@given(case=_cases())
def test_matches_pre_refactor_body(case: dict[str, Any]) -> None:
    fetch = object()
    assert _run(_resolve_tileset, case, fetch) == _run(_old_resolve_tileset, case, fetch)
