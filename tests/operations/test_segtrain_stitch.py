"""Tests for tileset stitch loaders, including recursive finer-level recovery."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from PIL import Image

import nornir_pools
from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect
from nornir_buildmanager.operations.segmentationtraining.stitch import (
    StitchJob,
    default_column_band,
    default_io_workers,
    disk_tile_loader,
    finer_dirs_for_downsample,
    load_tiles_threaded,
    sweep_stitch_jobs,
    tile_filename,
    write_png,
)

_TILE = 8
_PREFIX = "t_"
_POSTFIX = ".png"
_STITCH_LOGGER = "nornir_buildmanager.operations.segmentationtraining.stitch"


def _tile_path(level_dir: Path, ix: int, iy: int) -> Path:
    return level_dir / tile_filename(_PREFIX, _POSTFIX, ix, iy)


def _constant_tile(value: int) -> NDArray[np.uint8]:
    return np.full((_TILE, _TILE), value, dtype=np.uint8)


def _write_tile(level_dir: Path, ix: int, iy: int, value: int) -> None:
    level_dir.mkdir(parents=True, exist_ok=True)
    write_png(_constant_tile(value), str(_tile_path(level_dir, ix, iy)))


def _corrupt_tile(level_dir: Path, ix: int, iy: int) -> None:
    level_dir.mkdir(parents=True, exist_ok=True)
    _tile_path(level_dir, ix, iy).write_bytes(b"not a png")


def _downsample_quad(quads: tuple[NDArray[np.uint8], ...]) -> NDArray[np.uint8]:
    """Oracle: stitch four (iy, ix) tiles in row-major order and bilinear-halve."""
    canvas = np.zeros((_TILE * 2, _TILE * 2), dtype=np.uint8)
    for index, tile in enumerate(quads):
        dy, dx = divmod(index, 2)
        py = dy * _TILE
        px = dx * _TILE
        canvas[py:py + _TILE, px:px + _TILE] = tile
    with Image.fromarray(canvas, mode="L") as image:
        result = image.resize((_TILE, _TILE), resample=Image.Resampling.BILINEAR)
    return np.array(result, dtype=np.uint8)


def _loader(level_dir: Path, finer_dirs: list[Path] | None = None):
    return disk_tile_loader(
        str(level_dir),
        _PREFIX,
        _POSTFIX,
        finer_dirs=[str(path) for path in finer_dirs] if finer_dirs else None,
        tile_x_dim=_TILE,
        tile_y_dim=_TILE,
    )


def test_finer_dirs_for_downsample_successive_halves() -> None:
    level_dirs = {1: "/d1", 2: "/d2", 4: "/d4", 8: "/d8", 16: "/d16"}
    assert finer_dirs_for_downsample(8, level_dirs) == ["/d4", "/d2", "/d1"]
    assert finer_dirs_for_downsample(1, level_dirs) == []
    assert finer_dirs_for_downsample(16, {16: "/d16", 8: "/d8", 2: "/d2"}) == ["/d8"]


def test_single_level_recovery_overwrites_corrupt_tile(tmp_path: Path) -> None:
    coarser = tmp_path / "4"
    finer = tmp_path / "2"
    values = (10, 80, 160, 240)
    for index, value in enumerate(values):
        dy, dx = divmod(index, 2)
        _write_tile(finer, dx, dy, value)
    _corrupt_tile(coarser, 0, 0)

    loaded = _loader(coarser, [finer])(0, 0)
    expected = _downsample_quad(tuple(_constant_tile(value) for value in values))
    assert loaded is not None
    np.testing.assert_array_equal(loaded, expected)
    repaired = np.array(Image.open(_tile_path(coarser, 0, 0)), dtype=np.uint8)
    np.testing.assert_array_equal(repaired, expected)


def test_two_level_recursive_recovery(tmp_path: Path) -> None:
    level_4 = tmp_path / "4"
    level_2 = tmp_path / "2"
    level_1 = tmp_path / "1"
    finest = (10, 20, 30, 40)
    for index, value in enumerate(finest):
        dy, dx = divmod(index, 2)
        _write_tile(level_1, dx, dy, value)
    _corrupt_tile(level_2, 0, 0)
    _write_tile(level_2, 1, 0, 50)
    _write_tile(level_2, 0, 1, 60)
    _write_tile(level_2, 1, 1, 70)
    _corrupt_tile(level_4, 0, 0)

    loaded = _loader(level_4, [level_2, level_1])(0, 0)
    recovered_d2 = _downsample_quad(tuple(_constant_tile(value) for value in finest))
    expected = _downsample_quad(
        (recovered_d2, _constant_tile(50), _constant_tile(60), _constant_tile(70))
    )
    assert loaded is not None
    np.testing.assert_array_equal(loaded, expected)
    repaired_d2 = np.array(Image.open(_tile_path(level_2, 0, 0)), dtype=np.uint8)
    np.testing.assert_array_equal(repaired_d2, recovered_d2)
    repaired_d4 = np.array(Image.open(_tile_path(level_4, 0, 0)), dtype=np.uint8)
    np.testing.assert_array_equal(repaired_d4, expected)


def test_full_exhaustion_returns_none_and_leaves_corrupt_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    coarser = tmp_path / "4"
    finer = tmp_path / "2"
    finest = tmp_path / "1"
    _corrupt_tile(coarser, 0, 0)
    _corrupt_tile(finer, 0, 0)
    _write_tile(finer, 1, 0, 50)
    _write_tile(finer, 0, 1, 60)
    _write_tile(finer, 1, 1, 70)
    for dx in range(2):
        for dy in range(2):
            _corrupt_tile(finest, dx, dy)
    corrupt_bytes = _tile_path(coarser, 0, 0).read_bytes()

    with caplog.at_level(logging.WARNING, logger=_STITCH_LOGGER):
        loaded = _loader(coarser, [finer, finest])(0, 0)

    assert loaded is None
    assert _tile_path(coarser, 0, 0).read_bytes() == corrupt_bytes
    assert "Skipping unreadable tile" in caplog.text


def test_missing_subtile_skips_entire_tile(tmp_path: Path) -> None:
    coarser = tmp_path / "4"
    finer = tmp_path / "2"
    _corrupt_tile(coarser, 0, 0)
    _write_tile(finer, 0, 0, 10)
    _write_tile(finer, 0, 1, 30)
    _write_tile(finer, 1, 1, 40)
    corrupt_bytes = _tile_path(coarser, 0, 0).read_bytes()

    loaded = _loader(coarser, [finer])(0, 0)

    assert loaded is None
    assert _tile_path(coarser, 0, 0).read_bytes() == corrupt_bytes


def _job(ix0: int, ix1: int, iy0: int, iy1: int, key: str, image_path: str = "") -> StitchJob:
    return StitchJob(
        image_key=key,
        snap=TileRect(ix0, ix1, iy0, iy1),
        downsample=1,
        tile_x_dim=_TILE,
        tile_y_dim=_TILE,
        image_path=image_path,
    )


def test_default_io_workers_scales_with_cpu_count() -> None:
    assert default_io_workers(cpu_count=1) == 4
    assert default_io_workers(cpu_count=8) == 16
    assert default_io_workers(cpu_count=32) == 32


def test_default_column_band_full_span_when_tiles_fit() -> None:
    jobs = [_job(0, 2, 0, 2, "a"), _job(10, 12, 0, 2, "b")]
    band = default_column_band(
        16, jobs, tile_x_dim=_TILE, tile_y_dim=_TILE, cpu_count=32, available_bytes=1 << 30
    )
    assert band == 12


def test_default_column_band_memory_cap() -> None:
    jobs = [_job(index, index + 1, 0, 20, f"c{index}") for index in range(20)]
    band = default_column_band(
        16, jobs, tile_x_dim=_TILE, tile_y_dim=_TILE, cpu_count=32, available_bytes=256
    )
    assert band == 1


def test_default_column_band_empty_jobs_uses_hardware() -> None:
    assert default_column_band(4, [], cpu_count=8) == 8


def test_load_tiles_threaded_skips_missing_and_loads_present(tmp_path: Path) -> None:
    level = tmp_path / "1"
    _write_tile(level, 0, 0, 11)
    _write_tile(level, 1, 0, 22)
    loader = _loader(level)
    try:
        loaded = load_tiles_threaded([(0, 0), (1, 0), (2, 0)], loader, num_threads=4)
    finally:
        nornir_pools.ClosePools(timeout=10)
    assert set(loaded) == {(0, 0), (1, 0)}
    np.testing.assert_array_equal(loaded[(0, 0)], _constant_tile(11))
    np.testing.assert_array_equal(loaded[(1, 0)], _constant_tile(22))


def test_sweep_stitch_jobs_writes_crops_across_columns(tmp_path: Path) -> None:
    level = tmp_path / "tiles"
    out = tmp_path / "out"
    out.mkdir()
    _write_tile(level, 0, 0, 10)
    _write_tile(level, 4, 0, 40)
    jobs = [
        _job(0, 1, 0, 1, "left", str(out / "left.png")),
        _job(4, 5, 0, 1, "right", str(out / "right.png")),
    ]
    try:
        written = sweep_stitch_jobs(
            jobs,
            source_dir=str(level),
            prefix=_PREFIX,
            postfix=_POSTFIX,
            workers=1,
            column_band=1,
            io_workers=4,
            tile_x_dim=_TILE,
            tile_y_dim=_TILE,
        )
    finally:
        nornir_pools.ClosePools(timeout=10)
    assert set(written) == {jobs[0].image_path, jobs[1].image_path}
    left = np.array(Image.open(jobs[0].image_path), dtype=np.uint8)
    right = np.array(Image.open(jobs[1].image_path), dtype=np.uint8)
    np.testing.assert_array_equal(left, _constant_tile(10))
    np.testing.assert_array_equal(right, _constant_tile(40))


def test_sweep_stitch_jobs_process_pool_shared_tiles(tmp_path: Path) -> None:
    level = tmp_path / "tiles"
    out = tmp_path / "out"
    out.mkdir()
    _write_tile(level, 0, 0, 7)
    _write_tile(level, 2, 0, 9)
    jobs = [
        _job(0, 1, 0, 1, "a", str(out / "a.png")),
        _job(2, 3, 0, 1, "b", str(out / "b.png")),
    ]
    try:
        written = sweep_stitch_jobs(
            jobs,
            source_dir=str(level),
            prefix=_PREFIX,
            postfix=_POSTFIX,
            workers=2,
            column_band=1,
            io_workers=2,
            tile_x_dim=_TILE,
            tile_y_dim=_TILE,
        )
    finally:
        nornir_pools.ClosePools(timeout=10)
    assert set(written) == {jobs[0].image_path, jobs[1].image_path}
    np.testing.assert_array_equal(
        np.array(Image.open(jobs[0].image_path), dtype=np.uint8), _constant_tile(7)
    )
    np.testing.assert_array_equal(
        np.array(Image.open(jobs[1].image_path), dtype=np.uint8), _constant_tile(9)
    )


