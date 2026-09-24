"""Planning: 1px pad, 2x2 from min tile for 512-px tiles, exact-key share."""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st

from nornir_buildmanager.operations.segmentationtraining.geometry import (
    SnapPlan,
    TileRect,
    choose_downsample,
    downsample_and_align_snap,
    expand_snap_from_min_tile,
    max_texture_tile_span,
    mosaic_bbox,
    next_power_of_two,
    pad_bbox,
)
from nornir_buildmanager.operations.segmentationtraining.planning import plan_section_crops
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord, LocationType
from nornir_buildmanager.operations.segmentationtraining.wkt import parse_wkt_polygons

_PYRAMID = [1, 2, 4, 8, 16, 32]
_TILE = 512
_MAX = 1024
_PAD = 1.0


def _box_wkt(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def _record(location_id: int, wkt: str, z: int = 37) -> LocationRecord:
    return LocationRecord(
        id=location_id,
        z=z,
        wkt=wkt,
        parent_id=None,
        off_edge=False,
        last_modified=datetime(2020, 1, 1, tzinfo=timezone.utc),
        type_id=1,
        type_name="Cell",
        structure_label=None,
    )


def _plan(records: list[LocationRecord]):
    return plan_section_crops(
        records,
        pad=_PAD,
        finest=1,
        available=_PYRAMID,
        max_texture=_MAX,
        min_process_pixels=16,
        tile_x_dim=_TILE,
        tile_y_dim=_TILE,
        max_tiles_x=2,
        max_tiles_y=2,
    )


def test_pad_bbox_is_one_pixel_each_direction() -> None:
    assert pad_bbox(10.0, 20.0, 30.0, 40.0, 1.0) == (9.0, 19.0, 31.0, 41.0)


def test_next_power_of_two() -> None:
    assert next_power_of_two(0) == 1
    assert next_power_of_two(1) == 1
    assert next_power_of_two(2) == 2
    assert next_power_of_two(3) == 4


def test_choose_downsample_closed_form() -> None:
    kwargs = {"finest": 1, "available": _PYRAMID, "max_texture": _MAX}
    assert choose_downsample(width_mosaic=600, height_mosaic=400, **kwargs) == 1
    assert choose_downsample(width_mosaic=2000, height_mosaic=400, **kwargs) == 2
    assert choose_downsample(width_mosaic=3000, height_mosaic=3000, **kwargs) == 4


def test_cell_inside_one_512_tile_grows_to_2x2_from_min_tile() -> None:
    records = [_record(1, _box_wkt(5200, 5200, 5300, 5300))]
    plans, _ = _plan(records)
    assert len(plans) == 1
    assert plans[0].downsample == 1
    assert plans[0].snap == TileRect(10, 12, 10, 12)
    width, height = plans[0].snap.pixel_size(_TILE, _TILE)
    assert width == _MAX and height == _MAX
    assert plans[0].window_of[1] == (0, 1)


def test_odd_min_tile_2x2_starts_at_that_tile() -> None:
    records = [_record(1, _box_wkt(2100, 3600, 2200, 3700))]
    plans, _ = _plan(records)
    assert len(plans) == 1
    assert plans[0].downsample == 1
    assert plans[0].snap == TileRect(4, 6, 7, 9)


def test_expand_snap_from_min_tile() -> None:
    assert max_texture_tile_span(512, 1024) == 2
    assert max_texture_tile_span(1024, 1024) == 1
    assert max_texture_tile_span(768, 1024) is None
    assert expand_snap_from_min_tile(TileRect(4, 5, 7, 8), 2, 2) == TileRect(4, 6, 7, 9)
    assert expand_snap_from_min_tile(TileRect(11, 12, 10, 11), 2, 2) == TileRect(11, 13, 10, 12)
    assert expand_snap_from_min_tile(TileRect(9, 12, 10, 11), 2, 2) is None


def test_plan_skips_point_wkt_without_failing() -> None:
    records = [
        _record(1, _box_wkt(5200, 5200, 5300, 5300)),
        _record(2, "POINT (0 0)"),
    ]
    plans, hosted = _plan(records)
    assert len(plans) == 1
    assert 1 in hosted
    assert 2 not in hosted


def test_parse_wkt_ignores_point_polyline_circle() -> None:
    assert parse_wkt_polygons("POINT (0 0)") == []
    assert parse_wkt_polygons("LINESTRING(0 0, 10 0)") == []
    assert parse_wkt_polygons("CIRCLE((5 5), 3)") == []


def test_plan_skips_circle_type_code_even_with_polygon_wkt() -> None:
    record = _record(3, _box_wkt(5200, 5200, 5300, 5300))
    record = LocationRecord(
        id=record.id,
        z=record.z,
        wkt=record.wkt,
        parent_id=record.parent_id,
        off_edge=record.off_edge,
        last_modified=record.last_modified,
        type_id=record.type_id,
        type_name=record.type_name,
        structure_label=record.structure_label,
        type_code=LocationType.CIRCLE,
    )
    plans, hosted = _plan([record])
    assert plans == []
    assert 3 not in hosted


def test_image_key_is_prefixed_with_volume() -> None:
    records = [_record(1, _box_wkt(5200, 5200, 5300, 5300))]
    plans, _ = plan_section_crops(
        records,
        pad=_PAD,
        finest=1,
        available=_PYRAMID,
        max_texture=_MAX,
        min_process_pixels=16,
        tile_x_dim=_TILE,
        tile_y_dim=_TILE,
        max_tiles_x=2,
        max_tiles_y=2,
        volume="RC2",
    )
    assert len(plans) == 1
    assert plans[0].image_key.startswith("RC2_37_D")


def test_three_tile_span_is_one_full_mask_crop() -> None:
    records = [_record(1, _box_wkt(5520, 5200, 6244, 5300))]
    plans, _ = _plan(records)
    assert len(plans) == 1
    width, height = plans[0].snap.pixel_size(_TILE, _TILE)
    assert width <= _MAX and height <= _MAX
    assert plans[0].location_ids == [1]
    assert plans[0].window_of[1] == (0, 1)


def test_identical_key_cells_share_one_image() -> None:
    records = [
        _record(1, _box_wkt(5200, 5200, 5250, 5250)),
        _record(2, _box_wkt(5400, 5400, 5500, 5500)),
    ]
    plans, _ = _plan(records)
    assert len(plans) == 1
    assert sorted(plans[0].location_ids) == [1, 2]


def test_neighbor_tile_reuses_min_tile_2x2() -> None:
    records = [
        _record(1, _box_wkt(2100, 3600, 2200, 3700)),
        _record(2, _box_wkt(2600, 3600, 2700, 3700)),
    ]
    plans, _ = _plan(records)
    assert len(plans) == 1
    assert plans[0].downsample == 1
    assert plans[0].snap == TileRect(4, 6, 7, 9)
    assert sorted(plans[0].location_ids) == [1, 2]


def test_cells_with_same_min_tile_share() -> None:
    records = [
        _record(1, _box_wkt(5200, 5200, 5300, 5300)),
        _record(2, _box_wkt(5400, 5200, 5500, 5300)),
    ]
    plans, _ = _plan(records)
    assert len(plans) == 1
    assert sorted(plans[0].location_ids) == [1, 2]
    assert plans[0].snap == TileRect(10, 12, 10, 12)


def test_cells_two_tiles_apart_do_not_share() -> None:
    records = [
        _record(1, _box_wkt(5200, 5200, 5300, 5300)),
        _record(2, _box_wkt(6300, 5200, 6400, 5300)),
    ]
    plans, _ = _plan(records)
    assert len(plans) == 2
    assert {plan.downsample for plan in plans} == {1}
    keys = sorted(plan.image_key for plan in plans)
    assert keys[0] != keys[1]


def test_contained_neighbor_reuses_without_coarsening() -> None:
    records = [
        _record(1, _box_wkt(5200, 5200, 5300, 5300)),
        _record(2, _box_wkt(5700, 5200, 5800, 5300)),
    ]
    plans, _ = _plan(records)
    assert len(plans) == 1
    assert plans[0].downsample == 1
    assert plans[0].snap == TileRect(10, 12, 10, 12)
    assert sorted(plans[0].location_ids) == [1, 2]


def test_two_tile_mask_grows_from_min_tile_at_d1() -> None:
    records = [_record(1, _box_wkt(5000, 5200, 5300, 5300))]
    plans, _ = _plan(records)
    assert len(plans) == 1
    assert plans[0].downsample == 1
    assert plans[0].snap == TileRect(9, 11, 10, 12)
    width, height = plans[0].snap.pixel_size(_TILE, _TILE)
    assert width == _MAX and height == _MAX
    assert plans[0].window_of[1] == (0, 1)


def test_256_px_tiles_do_not_coarsen_past_bbox_fit() -> None:
    """RC1-style 256px tiles: a ~5600px mask fits D=8 as a 4×4 from its min tile."""
    records = [_record(1, _box_wkt(32017.0, 48141.0, 37621.0, 53403.0))]
    plans, _ = plan_section_crops(
        records,
        pad=_PAD,
        finest=1,
        available=[1, 2, 4, 8, 16, 32, 64, 128],
        max_texture=_MAX,
        min_process_pixels=16,
        tile_x_dim=256,
        tile_y_dim=256,
        max_tiles_x=4,
        max_tiles_y=4,
    )
    assert len(plans) == 1
    assert plans[0].downsample == 8
    width, height = plans[0].snap.pixel_size(256, 256)
    assert width == _MAX and height == _MAX
    assert plans[0].window_of[1] == (0, 1)


def test_1024_px_tiles_in_tile_cell_stays_one_by_one() -> None:
    records = [_record(1, _box_wkt(5200, 5200, 5300, 5300))]
    plans, _ = plan_section_crops(
        records,
        pad=_PAD,
        finest=1,
        available=_PYRAMID,
        max_texture=_MAX,
        min_process_pixels=16,
        tile_x_dim=1024,
        tile_y_dim=1024,
        max_tiles_x=1,
        max_tiles_y=1,
    )
    assert len(plans) == 1
    assert plans[0].downsample == 1
    assert plans[0].snap.n_x == 1
    assert plans[0].snap.n_y == 1
    width, height = plans[0].snap.pixel_size(1024, 1024)
    assert width == _MAX and height == _MAX


def test_coarsest_overflow_is_skipped() -> None:
    records = [_record(1, _box_wkt(0, 0, 40000, 40000))]
    plans, _ = _plan(records)
    assert plans == []


def _axis_pair() -> st.SearchStrategy[tuple[int, int]]:
    return st.integers(min_value=0, max_value=16000).flatmap(
        lambda start: st.tuples(
            st.just(start),
            st.integers(min_value=start + 2, max_value=start + 4000),
        )
    )


@given(boxes=st.lists(st.tuples(_axis_pair(), _axis_pair()), min_size=1, max_size=6))
@settings(max_examples=80, deadline=None)
def test_planned_snaps_fit_and_are_finest_single_window(
    boxes: list[tuple[tuple[int, int], tuple[int, int]]],
) -> None:
    records = [
        _record(index + 1, _box_wkt(xs[0], ys[0], xs[1], ys[1]))
        for index, (xs, ys) in enumerate(boxes)
    ]
    plans, _ = _plan(records)
    hosted: dict[int, int] = {}
    for plan in plans:
        width, height = plan.snap.pixel_size(_TILE, _TILE)
        assert width <= _MAX and height <= _MAX
        for location_id in plan.location_ids:
            assert location_id not in hosted
            hosted[location_id] = plan.downsample
            assert plan.window_of[location_id][1] == 1
    by_d: dict[int, list[TileRect]] = {}
    placements: dict[int, SnapPlan] = {}
    for record in records:
        padded = pad_bbox(*mosaic_bbox(parse_wkt_polygons(record.wkt)), _PAD)
        planned = downsample_and_align_snap(
            *padded,
            finest=1,
            available=_PYRAMID,
            max_texture=_MAX,
            tile_x_dim=_TILE,
            tile_y_dim=_TILE,
        )
        if planned is None:
            assert record.id not in hosted
            continue
        by_d.setdefault(planned.downsample, []).append(planned.window)
        placements[record.id] = planned
    for record in records:
        planned = placements.get(record.id)
        if planned is None:
            continue
        hosts = [window for window in by_d[planned.downsample] if window.contains(planned.tight)]
        expected = min(hosts, key=lambda rect: (rect.ix0, rect.iy0, rect.ix1, rect.iy1))
        assert hosted[record.id] == planned.downsample
        matching = [plan for plan in plans if record.id in plan.location_ids]
        assert len(matching) == 1
        assert matching[0].snap == expected
        assert matching[0].downsample == planned.downsample
