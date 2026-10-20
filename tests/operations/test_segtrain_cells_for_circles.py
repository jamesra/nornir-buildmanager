"""Equivalence of circles.cells_for_circles with its original per-pair scalar loop."""

from __future__ import annotations

import math
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from nornir_buildmanager.operations.segmentationtraining import (
    circles as circles_module,
)
from nornir_buildmanager.operations.segmentationtraining import geometry
from nornir_buildmanager.operations.segmentationtraining.circles import (
    CropRef,
    MosaicCircle,
    cells_for_circles,
    circle_in_crop_pixels,
    circle_intersects_window,
)
from nornir_buildmanager.operations.segmentationtraining.geometry import CropWindow


def _old_cells_for_circles(
    crops: list[CropRef], circles: list[MosaicCircle]
) -> dict[str, list[dict[str, Any]]]:
    """Body of cells_for_circles before the vectorized prefilter (reference)."""
    cells: dict[str, list[dict[str, Any]]] = {}
    circle_list = list(circles)
    for crop in crops:
        entries: list[dict[str, Any]] = []
        for circle in circle_list:
            px, py, radius = circle_in_crop_pixels(
                circle.center_x,
                circle.center_y,
                circle.radius,
                window=crop.window,
                downsample=crop.downsample,
            )
            if not circle_intersects_window(
                px, py, radius, width=crop.window.width, height=crop.window.height
            ):
                continue
            entries.append(
                {
                    "location_id": circle.location_id,
                    "center_x": round(px, 3),
                    "center_y": round(py, 3),
                    "radius": round(radius, 3),
                    "structure_label": circle.structure_label,
                    "has_polygon_mask": circle.has_polygon_mask,
                    "source": circle.source,
                }
            )
        if entries:
            entries.sort(key=lambda item: item["location_id"])
            cells[crop.image_key] = entries
    return cells


def _crop(index: int, downsample: int, x0: int, y0: int, width: int, height: int) -> CropRef:
    key = f"k{index}_D{downsample}_X{x0}-{x0 + width}_Y{y0}-{y0 + height}"
    return CropRef(key, downsample, CropWindow(x0, y0, width, height))


def _circle(location_id: int, x: float, y: float, r: float) -> MosaicCircle:
    return MosaicCircle(
        location_id=location_id,
        center_x=x,
        center_y=y,
        radius=r,
        structure_label=f"s{location_id % 3}",
        has_polygon_mask=bool(location_id % 2),
        source="annotated" if location_id % 2 else "inscribed",
    )


_crop_st = st.builds(
    lambda ds, x0, y0, w, h: (ds, x0, y0, w, h),
    st.sampled_from([1, 2, 4, 8, 16]),
    st.integers(0, 60_000),
    st.integers(0, 60_000),
    st.integers(1, 2048),
    st.integers(1, 2048),
)


@st.composite
def _scene(draw: st.DrawFn) -> tuple[list[CropRef], list[MosaicCircle]]:
    """Crops plus circles placed around them, including edge- and corner-tangent discs."""
    raw_crops = draw(st.lists(_crop_st, min_size=1, max_size=6))
    crops = [_crop(i, *spec) for i, spec in enumerate(raw_crops)]
    circles: list[MosaicCircle] = []
    n_circles = draw(st.integers(0, 25))
    for index in range(n_circles):
        ds, x0, y0, w, h = raw_crops[draw(st.integers(0, len(raw_crops) - 1))]
        radius = draw(
            st.one_of(
                st.floats(0.0, 5000.0, allow_nan=False),
                st.sampled_from([0.0, 1e-9, 0.5, 1.0, 3.0]),
                st.just(math.inf),
                st.just(math.nan),
            )
        )
        left, top = float(x0 * ds), float(y0 * ds)
        right, bottom = float((x0 + w) * ds), float((y0 + h) * ds)
        finite_r = radius if math.isfinite(radius) else 10.0
        edge_x = draw(st.sampled_from([left - finite_r, right + finite_r, left, right]))
        edge_y = draw(st.sampled_from([top - finite_r, bottom + finite_r, top, bottom]))
        x = draw(st.one_of(st.floats(left - 6000, right + 6000, allow_nan=False), st.just(edge_x)))
        y = draw(st.one_of(st.floats(top - 6000, bottom + 6000, allow_nan=False), st.just(edge_y)))
        # Unordered ids, with duplicates, so the location_id sort is exercised.
        location_id = draw(st.integers(0, 40))
        circles.append(_circle(location_id, x, y, radius))
    return crops, circles


@pytest.mark.parametrize("y_increases", [True, False])
@settings(max_examples=600, deadline=None)
@given(scene=_scene())
@example(
    scene=(
        [_crop(0, 4, 100, 200, 256, 128)],
        [
            _circle(5, 400.0 - 8.0, 900.0, 8.0),  # touches left edge at a point
            _circle(4, 400.0 + 1024.0 + 8.0, 900.0, 8.0),  # touches right edge
            _circle(3, 400.0 - 8.0, 800.0 - 8.0, 8.0 * math.sqrt(2.0)),  # corner tangent
            _circle(2, 400.0 - 7.999, 900.0, 8.0),  # just overlaps
            _circle(1, 600.0, 900.0, 0.0),  # zero radius inside
        ],
    )
)
def test_cells_for_circles_matches_scalar_loop(
    y_increases: bool, scene: tuple[list[CropRef], list[MosaicCircle]]
) -> None:
    crops, circles = scene
    saved = geometry.MOSAIC_Y_INCREASES_WITH_IMAGE_ROW
    geometry.MOSAIC_Y_INCREASES_WITH_IMAGE_ROW = y_increases
    try:
        expected = _old_cells_for_circles(crops, circles)
        actual = cells_for_circles(crops, iter(circles))
    finally:
        geometry.MOSAIC_Y_INCREASES_WITH_IMAGE_ROW = saved
    assert actual == expected
    assert list(actual) == list(expected)
    for key in expected:
        assert [e["location_id"] for e in actual[key]] == [e["location_id"] for e in expected[key]]


def test_prefilter_skips_scalar_check_for_distant_and_tangent_circles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only circles that can overlap reach the exact scalar test (pins the prefilter's speed)."""
    checked: list[tuple[float, float, float]] = []

    def counting(px: float, py: float, radius: float, *, width: int, height: int) -> bool:
        checked.append((px, py, radius))
        return circle_intersects_window(px, py, radius, width=width, height=height)

    monkeypatch.setattr(circles_module, "circle_intersects_window", counting)
    # Mosaic extent of this crop: x 400..1424, y 800..1312; radius 8 is 2 crop pixels.
    crop = _crop(0, 4, 100, 200, 256, 128)
    scene = [
        _circle(1, 900.0, 1000.0, 8.0),  # inside
        _circle(2, 0.0, 1000.0, 8.0),  # far left
        _circle(3, 900.0, 5000.0, 8.0),  # x inside, far below
        _circle(6, 900.0, 0.0, 8.0),  # x inside, far above
        _circle(4, 380.0, 1000.0, 8.0),  # 5 px left: reaches only if radius were not scaled
        _circle(5, 392.0, 1000.0, 8.0),  # touches the left edge at a point
    ]
    cells = cells_for_circles([crop], scene)
    assert [entry["location_id"] for entry in cells[crop.image_key]] == [1]
    assert checked == [(125.0, 50.0, 2.0)]


def test_cells_for_circles_no_circles_or_crops() -> None:
    crop = _crop(0, 2, 0, 0, 64, 64)
    assert cells_for_circles([crop], []) == {}
    assert cells_for_circles([], [_circle(1, 10.0, 10.0, 5.0)]) == {}
