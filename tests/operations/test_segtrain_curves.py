"""Viking TypeCode 6 hydration must fit Catmull-Rom before SAM2 masks."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from hypothesis import given, settings, strategies as st
from PIL import Image

from nornir_buildmanager.operations.segmentationtraining.curves import (
    EPSILON,
    fit_closed_curve_ring,
    fit_curve,
    hydrate_polygons,
)
from nornir_buildmanager.operations.segmentationtraining.freshness import ExportParams
from nornir_buildmanager.operations.segmentationtraining.geometry import pixel_rings_in_crop
from nornir_buildmanager.operations.segmentationtraining.masks import MaskJob, rasterize_mask_job
from nornir_buildmanager.operations.segmentationtraining.planning import plan_section_crops
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord, LocationType
from nornir_buildmanager.operations.segmentationtraining.wkt import parse_wkt_polygons


def _box_wkt(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def _diamond_wkt() -> str:
    return "POLYGON((50 0,100 50,50 100,0 50,50 0))"


def _record(wkt: str, type_code: int, loc_id: int = 1) -> LocationRecord:
    return LocationRecord(
        id=loc_id,
        z=1,
        wkt=wkt,
        parent_id=10,
        off_edge=False,
        last_modified=datetime(2020, 1, 1, tzinfo=timezone.utc),
        type_id=1,
        type_name="Cell",
        structure_label=None,
        type_code=type_code,
    )


def _shoelace(ring: tuple[tuple[float, float], ...]) -> float:
    xs = [point[0] for point in ring]
    ys = [point[1] for point in ring]
    if xs[0] != xs[-1] or ys[0] != ys[-1]:
        xs.append(xs[0])
        ys.append(ys[0])
    area = 0.0
    for index in range(len(xs) - 1):
        area += xs[index] * ys[index + 1] - xs[index + 1] * ys[index]
    return abs(area) / 2.0


def _contains_point(ring: tuple[tuple[float, float], ...], target: tuple[float, float]) -> bool:
    return any(
        abs(point[0] - target[0]) <= EPSILON and abs(point[1] - target[1]) <= EPSILON
        for point in ring
    )


def test_polygon_type_keeps_wkt_vertices() -> None:
    polygons = parse_wkt_polygons(_box_wkt(0, 0, 10, 10))
    assert hydrate_polygons(polygons, LocationType.POLYGON) == polygons
    assert hydrate_polygons(polygons, None) == polygons


def test_curved_polygon_adds_samples_and_keeps_control_points() -> None:
    original = parse_wkt_polygons(_diamond_wkt())[0][0]
    fitted = fit_closed_curve_ring(original)
    unique_original = original[:-1]
    assert len(fitted) > len(original)
    assert fitted[0] == fitted[-1]
    for control in unique_original:
        assert _contains_point(fitted, control)


# Captured from Geometry.CatmullRom.FitCurve(..., 10, closed: true) in Viking.
_VIKING_CLOSED_FIT = (
    (0.0, 7.0),
    (0.16764307727072636, 6.9204967555116745),
    (0.32052289206475226, 6.828397876534469),
    (0.4589095604978481, 6.725714934996889),
    (0.5830731986857829, 6.61445950282744),
    (0.693283922744327, 6.496643151954636),
    (0.7898118487892509, 6.3742774543069824),
    (0.8729270929363246, 6.249373981812984),
    (0.9428997713013174, 6.123944306401155),
    (1.0, 6.0),
    (1.0379367932954726, 5.89447170872036),
    (1.066389388267077, 5.784756302567118),
    (1.085357784914813, 5.672050100075584),
    (1.0948419832386813, 5.557549419781081),
    (1.0948419832386813, 5.442450580218919),
    (1.085357784914813, 5.327949899924414),
    (1.066389388267077, 5.215243697432884),
    (1.0379367932954724, 5.105528291279641),
    (1.0, 5.0),
    (0.9410035393276189, 4.872526975526024),
    (0.8662902810283799, 4.73827550493214),
    (0.7770122829667864, 4.601903698701475),
    (0.674321603007342, 4.468069667317151),
    (0.5593702990145516, 4.341431521262293),
    (0.4333104288529183, 4.226647371020025),
    (0.29729405038694584, 4.12837532707347),
    (0.15247322148113837, 4.051273499905753),
    (0.0, 4.0),
    (-0.12331142156399172, 3.979784750503666),
    (-0.2591673046126111, 3.9735450841373208),
    (-0.4061719283182847, 3.9803319938814137),
    (-0.5629295718534393, 3.9991964727163927),
    (-0.9001210351018971, 4.069362109580797),
    (-1.259575927737398, 4.1764499385741205),
    (-1.6301284831393548, 4.312867903539948),
    (-2.0006129346871813, 4.471023948321865),
    (-2.359863515760288, 4.643326016763456),
    (-2.6967144597380903, 4.822182052708307),
    (-3.0, 5.0),
    (-3.28508451957045, 5.186656652906618),
    (-3.5941273306739276, 5.4085330692800095),
    (-3.910125347937538, 5.654345661489661),
    (-4.21607548598839, 5.912810841905052),
    (-4.4949746594535895, 6.172645022895667),
    (-4.729819782960244, 6.422564616830988),
    (-4.825408611800088, 6.540280322518376),
    (-4.9036077711354595, 6.651286036080494),
    (-4.962291875294748, 6.754171309063532),
    (-4.999335538606343, 6.8475256930136705),
    (-5.01261337539863, 6.929938739477099),
    (-5.0, 7.0),
    (-4.932904799174361, 7.07878516440499),
    (-4.805863236878592, 7.146709241186469),
    (-4.625467846102629, 7.204070776235493),
    (-4.398311159836414, 7.2511683154431275),
    (-3.830084032792996, 7.315765589898488),
    (-3.153922119667865, 7.342889431681061),
    (-2.4225656843805483, 7.334928207919355),
    (-1.6887549908505741, 7.294270285741886),
    (-1.0052303029974732, 7.223304032277163),
    (-0.42473188474077245, 7.124417814653697),
    (0.0, 7.0),
)


def test_fit_curve_matches_viking_closed_catmull_rom() -> None:
    control = ((0.0, 7.0), (1.0, 6.0), (1.0, 5.0), (0.0, 4.0), (-3.0, 5.0), (-5.0, 7.0), (0.0, 7.0))
    fitted = fit_curve(control, 10, closed=True)
    assert len(fitted) == len(_VIKING_CLOSED_FIT)
    for got, expected in zip(fitted, _VIKING_CLOSED_FIT, strict=True):
        assert abs(got[0] - expected[0]) < 1e-9
        assert abs(got[1] - expected[1]) < 1e-9


def test_curved_square_overshoots_control_polygon() -> None:
    """Closed Catmull-Rom leaves the control AABB; pad/snap must use fitted rings."""
    original = parse_wkt_polygons(_box_wkt(0, 0, 100, 100))[0][0]
    fitted = fit_closed_curve_ring(original)
    xs = [point[0] for point in fitted]
    ys = [point[1] for point in fitted]
    assert min(xs) < 0.0 or min(ys) < 0.0 or max(xs) > 100.0 or max(ys) > 100.0
    assert _shoelace(fitted) != _shoelace(original)


def test_hole_rings_are_fitted_for_type_code_6() -> None:
    wkt = "POLYGON((0 0,80 0,80 80,0 80,0 0),(20 20,60 20,60 60,20 60,20 20))"
    polygons = parse_wkt_polygons(wkt)
    fitted = hydrate_polygons(polygons, LocationType.CURVEPOLYGON)
    assert len(fitted[0]) == 2
    assert len(fitted[0][0]) > len(polygons[0][0])
    assert len(fitted[0][1]) > len(polygons[0][1])


def test_plan_uses_fitted_rings_for_curved_polygons() -> None:
    record = _record(_diamond_wkt(), LocationType.CURVEPOLYGON)
    _plans, polygons = plan_section_crops(
        [record],
        pad=0.0,
        finest=1,
        available=[1],
        max_texture=4096,
        min_process_pixels=16,
        tile_x_dim=256,
        tile_y_dim=256,
        max_tiles_x=16,
        max_tiles_y=16,
    )
    fitted = polygons[record.id][0][0]
    control = parse_wkt_polygons(record.wkt)[0][0]
    assert len(fitted) > len(control)
    for point in control[:-1]:
        assert _contains_point(fitted, point)


def test_curved_mask_differs_from_linear_control_polygon(tmp_path: Path) -> None:
    wkt = _box_wkt(8, 8, 120, 120)
    linear = parse_wkt_polygons(wkt)
    curved = hydrate_polygons(linear, LocationType.CURVEPOLYGON)
    width = height = 128
    linear_px = pixel_rings_in_crop(linear, origin_x=0.0, origin_y=0.0, downsample=1.0, width=width, height=height)
    curved_px = pixel_rings_in_crop(curved, origin_x=0.0, origin_y=0.0, downsample=1.0, width=width, height=height)
    linear_job = MaskJob(
        location_id=1,
        image_key="linear",
        width=width,
        height=height,
        rings=tuple(linear_px),
        mask_path=str(tmp_path / "linear.png"),
        rle_path=str(tmp_path / "linear.json"),
    )
    curved_job = MaskJob(
        location_id=2,
        image_key="curved",
        width=width,
        height=height,
        rings=tuple(curved_px),
        mask_path=str(tmp_path / "curved.png"),
        rle_path=str(tmp_path / "curved.json"),
    )
    linear_area = rasterize_mask_job(linear_job)["area"]
    curved_area = rasterize_mask_job(curved_job)["area"]
    assert curved_area != linear_area
    linear_mask = np.array(Image.open(tmp_path / "linear.png"))
    curved_mask = np.array(Image.open(tmp_path / "curved.png"))
    assert not np.array_equal(linear_mask, curved_mask)


def test_export_params_hash_includes_curve_hydration_version() -> None:
    base = ExportParams(
        pad=1.0,
        downsample=1,
        max_texture=4096,
        min_process_pixels=16,
        include_off_edge=False,
        channel="TEM",
        filter_name="Leveled",
        volume="v",
    )
    older = ExportParams(
        pad=1.0,
        downsample=1,
        max_texture=4096,
        min_process_pixels=16,
        include_off_edge=False,
        channel="TEM",
        filter_name="Leveled",
        volume="v",
        geometry_version="pre-curve",
    )
    assert base.hash() != older.hash()


@given(
    st.lists(
        st.tuples(
            st.floats(min_value=0.0, max_value=200.0, allow_nan=False, allow_infinity=False, width=64),
            st.floats(min_value=0.0, max_value=200.0, allow_nan=False, allow_infinity=False, width=64),
        ),
        min_size=4,
        max_size=8,
        unique=True,
    ).filter(
        lambda pts: all(
            (pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2 > 1.0
            for i in range(len(pts))
            for j in range(i + 1, len(pts))
        )
    )
)
@settings(max_examples=30)
def test_fitted_closed_curve_preserves_control_points(
    points: list[tuple[float, float]],
) -> None:
    ring = tuple(points + [points[0]])
    fitted = fit_closed_curve_ring(ring)
    assert fitted[0] == fitted[-1]
    for control in points:
        assert _contains_point(fitted, control)
