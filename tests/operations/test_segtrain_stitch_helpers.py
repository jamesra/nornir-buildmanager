"""Characterization tests for stitch.py helpers that had no direct coverage."""

from __future__ import annotations

import numpy as np
import pytest
from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect
from nornir_buildmanager.operations.segmentationtraining.stitch import (
    StitchJob,
    _as_uint8_gray,
    _crop_canvas,
    _typical_size,
    stitch_from_loader,
)
from nornir_imageregistration.type_info import Shape
from numpy.typing import NDArray

_TILE = 4


def _job(snap: TileRect, **crop: int) -> StitchJob:
    return StitchJob(
        image_key="k",
        snap=snap,
        downsample=1,
        tile_shape=Shape.from_xy(x=_TILE, y=_TILE),
        image_path="unused.png",
        **crop,
    )


# --- _as_uint8_gray -------------------------------------------------------


def test_as_uint8_gray_uint8_passthrough() -> None:
    tile = np.array([[0, 7], [128, 255]], dtype=np.uint8)
    out = _as_uint8_gray(tile)
    assert out.dtype == np.uint8
    assert np.array_equal(out, tile)


def test_as_uint8_gray_float_scaled_and_clipped() -> None:
    tile = np.array([[0.0, 1.0], [0.5, 2.0], [-1.0, 0.2]], dtype=np.float32)
    out = _as_uint8_gray(tile)
    assert out.dtype == np.uint8
    # Truncating cast of clip(x * 255): 0.5 -> 127, 0.2 -> 51; 2.0 saturates, -1.0 floors at 0.
    assert out.tolist() == [[0, 255], [127, 255], [0, 51]]


def test_as_uint8_gray_uint16_scaled_by_dtype_max() -> None:
    tile = np.array([[0, 65535], [32768, 257]], dtype=np.uint16)
    out = _as_uint8_gray(tile)
    assert out.dtype == np.uint8
    assert out.tolist() == [[0, 255], [127, 1]]  # 32768 -> 127.5 truncates


def test_as_uint8_gray_multichannel_takes_first_channel() -> None:
    tile = np.zeros((2, 3, 3), dtype=np.uint8)
    tile[..., 0] = 10
    tile[..., 1] = 99
    tile[..., 2] = 200
    out = _as_uint8_gray(tile)
    assert out.shape == (2, 3)
    assert np.all(out == 10)


# --- _typical_size --------------------------------------------------------


def test_typical_size_is_mode() -> None:
    assert _typical_size([256, 128, 256, 256, 128]) == 256
    assert _typical_size([64, 128, 128]) == 128


def test_typical_size_tie_prefers_larger() -> None:
    assert _typical_size([128, 256]) == 256
    assert _typical_size([256, 128, 256, 128]) == 256


def test_typical_size_single_value() -> None:
    assert _typical_size([512]) == 512


# --- _crop_canvas ---------------------------------------------------------


def _ramp(height: int, width: int) -> NDArray[np.uint8]:
    return (np.arange(height * width, dtype=np.uint8)).reshape(height, width)


def test_crop_canvas_without_crop_returns_same_object() -> None:
    canvas = _ramp(4, 8)
    assert _crop_canvas(_job(TileRect(0, 2, 0, 1)), canvas, 8, 4) is canvas


@pytest.mark.parametrize("crop_w, crop_h", [(0, 2), (2, 0), (0, 0), (-1, 2)])
def test_crop_canvas_nonpositive_size_means_no_crop(crop_w: int, crop_h: int) -> None:
    canvas = _ramp(4, 8)
    job = _job(TileRect(0, 2, 0, 1), crop_x=1, crop_y=1, crop_width=crop_w, crop_height=crop_h)
    assert _crop_canvas(job, canvas, 8, 4) is canvas


def test_crop_canvas_in_bounds_slice_is_contiguous_copy() -> None:
    canvas = _ramp(4, 8)
    job = _job(TileRect(0, 2, 0, 1), crop_x=2, crop_y=1, crop_width=3, crop_height=2)
    out = _crop_canvas(job, canvas, 8, 4)
    assert out.shape == (2, 3)
    assert np.array_equal(out, canvas[1:3, 2:5])
    assert out.flags["C_CONTIGUOUS"]
    assert not np.shares_memory(out, canvas)


def test_crop_canvas_exact_edge_is_allowed() -> None:
    canvas = _ramp(4, 8)
    job = _job(TileRect(0, 2, 0, 1), crop_x=4, crop_y=2, crop_width=4, crop_height=2)
    assert np.array_equal(_crop_canvas(job, canvas, 8, 4), canvas[2:4, 4:8])


@pytest.mark.parametrize(
    "crop_x, crop_y, crop_w, crop_h",
    [(5, 0, 4, 2), (0, 3, 2, 2), (-1, 0, 2, 2), (0, -1, 2, 2)],
)
def test_crop_canvas_outside_canvas_raises(crop_x: int, crop_y: int, crop_w: int, crop_h: int) -> None:
    canvas = _ramp(4, 8)
    job = _job(TileRect(0, 2, 0, 1), crop_x=crop_x, crop_y=crop_y, crop_width=crop_w, crop_height=crop_h)
    with pytest.raises(ValueError, match="outside stitched canvas 8x4"):
        _crop_canvas(job, canvas, 8, 4)


# --- stitch_from_loader ---------------------------------------------------


def test_stitch_from_loader_places_tiles_by_grid_offset() -> None:
    job = _job(TileRect(2, 4, 5, 7))

    def loader(ix: int, iy: int) -> NDArray[np.uint8]:
        return np.full((_TILE, _TILE), 10 * (ix - 2) + (iy - 5) + 1, dtype=np.uint8)

    canvas = stitch_from_loader(job, loader)
    assert canvas.shape == (2 * _TILE, 2 * _TILE)
    assert canvas.dtype == np.uint8
    assert canvas[0, 0] == 1  # (ix0, iy0)
    assert canvas[_TILE, 0] == 2  # next row, iy+1
    assert canvas[0, _TILE] == 11  # next column, ix+1
    assert canvas[_TILE, _TILE] == 12


def test_stitch_from_loader_edge_tile_smaller_than_tile_shape_is_origin_aligned() -> None:
    job = _job(TileRect(0, 1, 0, 1))
    canvas = stitch_from_loader(job, lambda ix, iy: np.full((2, 3), 9, dtype=np.uint8))
    assert canvas.shape == (_TILE, _TILE)
    assert np.all(canvas[:2, :3] == 9)
    assert canvas.sum() == 9 * 2 * 3


def test_stitch_from_loader_oversize_tile_raises() -> None:
    job = _job(TileRect(0, 1, 0, 1))
    with pytest.raises(ValueError, match=r"Tile \(0,0\) is 5x4 but TileXDim/TileYDim is 4x4"):
        stitch_from_loader(job, lambda ix, iy: np.zeros((4, 5), dtype=np.uint8))


def test_stitch_from_loader_missing_and_unreadable_tiles_stay_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = _job(TileRect(0, 3, 0, 1))

    def loader(ix: int, iy: int) -> NDArray[np.uint8] | None:
        if ix == 0:
            return None
        if ix == 1:
            raise OSError("disk gone")
        return np.full((_TILE, _TILE), 200, dtype=np.uint8)

    with caplog.at_level("WARNING"):
        canvas = stitch_from_loader(job, loader)
    assert np.all(canvas[:, : 2 * _TILE] == 0)
    assert np.all(canvas[:, 2 * _TILE :] == 200)
    assert "Skipping unreadable tile (1,0)" in caplog.text
    assert "(0,0)" not in caplog.text  # None is silent


def test_stitch_from_loader_non_io_error_propagates() -> None:
    job = _job(TileRect(0, 1, 0, 1))

    def loader(ix: int, iy: int) -> NDArray[np.uint8]:
        raise KeyError("bug")

    with pytest.raises(KeyError):
        stitch_from_loader(job, loader)


def test_stitch_from_loader_applies_crop() -> None:
    job = _job(TileRect(0, 2, 0, 1), crop_x=2, crop_y=0, crop_width=4, crop_height=4)
    canvas = stitch_from_loader(job, lambda ix, iy: np.full((_TILE, _TILE), ix + 1, dtype=np.uint8))
    assert canvas.shape == (4, 4)
    assert canvas[0, 1] == 1 and canvas[0, 2] == 2


def test_crop_canvas_zero_origin_crop_is_allowed() -> None:
    canvas = _ramp(4, 8)
    job = _job(TileRect(0, 2, 0, 1), crop_x=0, crop_y=0, crop_width=3, crop_height=2)
    assert np.array_equal(_crop_canvas(job, canvas, 8, 4), canvas[:2, :3])


def test_stitch_from_loader_non_square_tiles_use_x_for_columns_and_y_for_rows() -> None:
    job = StitchJob(
        image_key="k",
        snap=TileRect(0, 2, 0, 2),
        downsample=1,
        tile_shape=Shape.from_xy(x=6, y=2),
        image_path="unused.png",
    )

    def loader(ix: int, iy: int) -> NDArray[np.uint8]:
        return np.full((2, 6), 1 + ix + 2 * iy, dtype=np.uint8)

    canvas = stitch_from_loader(job, loader)
    assert canvas.shape == (4, 12)
    expected = np.block([[np.full((2, 6), 1), np.full((2, 6), 2)], [np.full((2, 6), 3), np.full((2, 6), 4)]])
    assert np.array_equal(canvas, expected.astype(np.uint8))
