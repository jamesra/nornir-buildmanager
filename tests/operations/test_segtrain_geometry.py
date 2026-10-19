"""Characterization tests for the pure downsample and lattice helpers in segmentationtraining geometry."""

from __future__ import annotations

from nornir_buildmanager.operations.segmentationtraining.geometry import (
    TileRect,
    _lattice_step,
    _phase_offsets,
    available_downsamples,
    clamp_downsample_to_available,
    next_available_downsample,
    parse_and_snap,
    snap_fits_max_texture,
    snap_to_tiles,
)


def test_available_downsamples_sorts_dedupes_and_truncates() -> None:
    assert available_downsamples([4, 1, 2, 2, 4.9, 1.5]) == [1, 2, 4]


def test_available_downsamples_drops_levels_below_one() -> None:
    assert available_downsamples([0, 0.5, -3, 8]) == [8]


def test_available_downsamples_empty_or_all_invalid_defaults_to_one() -> None:
    assert available_downsamples([]) == [1]
    assert available_downsamples([0, 0.9]) == [1]


def test_clamp_picks_smallest_available_at_or_above_request() -> None:
    assert clamp_downsample_to_available(3, [1, 2, 4, 8], 1) == 4
    assert clamp_downsample_to_available(4, [1, 2, 4, 8], 1) == 4


def test_clamp_respects_finest_floor() -> None:
    assert clamp_downsample_to_available(1, [1, 2, 4, 8], 2) == 2
    assert clamp_downsample_to_available(1, [1, 2, 4, 8], 3) == 4


def test_clamp_request_above_all_returns_coarsest() -> None:
    assert clamp_downsample_to_available(64, [1, 2, 4], 1) == 4


def test_clamp_no_candidate_at_or_above_finest_returns_max_available() -> None:
    assert clamp_downsample_to_available(1, [1, 2], 16) == 2


def test_clamp_empty_available_returns_floor() -> None:
    assert clamp_downsample_to_available(4, [], 3) == 3
    assert clamp_downsample_to_available(4, [], 0) == 1


def test_clamp_unsorted_duplicates() -> None:
    assert clamp_downsample_to_available(3, [8, 2, 4, 4, 1], 1) == 4


def test_next_available_is_strictly_coarser() -> None:
    assert next_available_downsample([1, 2, 4], 1) == 2
    assert next_available_downsample([1, 2, 4], 2) == 4
    assert next_available_downsample([4, 1, 2], 3) == 4


def test_next_available_none_when_no_coarser_level() -> None:
    assert next_available_downsample([1, 2, 4], 4) is None
    assert next_available_downsample([1, 2, 4], 9) is None
    assert next_available_downsample([], 1) is None


def test_snap_fits_max_texture_boundary_inclusive() -> None:
    snap = TileRect(0, 2, 0, 2)
    assert snap_fits_max_texture(snap, 512, 512, 1024)
    assert not snap_fits_max_texture(snap, 512, 512, 1023)


def test_snap_fits_max_texture_checks_each_axis() -> None:
    wide = TileRect(0, 3, 0, 1)
    tall = TileRect(0, 1, 0, 3)
    assert not snap_fits_max_texture(wide, 512, 512, 1024)
    assert not snap_fits_max_texture(tall, 512, 512, 1024)
    assert snap_fits_max_texture(TileRect(0, 2, 0, 1), 512, 256, 1024)
    assert not snap_fits_max_texture(TileRect(0, 1, 0, 5), 512, 256, 1024)


def test_phase_offsets() -> None:
    assert _phase_offsets(1) == [0]
    assert _phase_offsets(0) == [0]
    assert _phase_offsets(2) == [0, 1]
    assert _phase_offsets(3) == [0, 1]
    assert _phase_offsets(512) == [0, 256]


def test_lattice_step() -> None:
    assert _lattice_step(1) == 1
    assert _lattice_step(2) == 1
    assert _lattice_step(3) == 1
    assert _lattice_step(512) == 256
    assert _lattice_step(513) == 256


def test_snap_to_tiles_floor_and_ceil() -> None:
    rect = snap_to_tiles(10.0, 20.0, 1030.0, 700.0, tile_w=512.0, tile_h=512.0)
    assert rect == TileRect(0, 3, 0, 2)


def test_snap_to_tiles_exact_boundaries_and_negative_origin() -> None:
    assert snap_to_tiles(512.0, 512.0, 1024.0, 1024.0, tile_w=512.0, tile_h=512.0) == TileRect(1, 2, 1, 2)
    assert snap_to_tiles(-1.0, -513.0, 1.0, 1.0, tile_w=512.0, tile_h=512.0) == TileRect(-1, 1, -2, 1)


def test_snap_to_tiles_degenerate_extent_grows_to_one_tile() -> None:
    assert snap_to_tiles(512.0, 1024.0, 512.0, 1024.0, tile_w=512.0, tile_h=512.0) == TileRect(1, 2, 2, 3)
    assert snap_to_tiles(5.0, 5.0, 5.0, 5.0, tile_w=512.0, tile_h=256.0) == TileRect(0, 1, 0, 1)


def test_parse_and_snap_pad_and_downsample() -> None:
    wkt = "POLYGON ((600 600, 900 600, 900 900, 600 900, 600 600))"
    polygons, snap, padded = parse_and_snap(wkt, pad=100, downsample=2, tile_x_dim=256, tile_y_dim=256)
    assert len(polygons) == 1
    assert padded == (500.0, 500.0, 1000.0, 1000.0)
    assert snap == TileRect(0, 2, 0, 2)


def test_parse_and_snap_mosaic_bounds_clamp_padding() -> None:
    wkt = "POLYGON ((10 10, 40 10, 40 40, 10 40, 10 10))"
    _polygons, snap, padded = parse_and_snap(
        wkt, pad=100, downsample=1, tile_x_dim=512, tile_y_dim=512, mosaic_bounds=(0.0, 0.0, 200.0, 300.0)
    )
    assert padded == (0.0, 0.0, 140.0, 140.0)
    assert snap == TileRect(0, 1, 0, 1)
