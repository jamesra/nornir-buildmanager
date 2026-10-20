"""Characterization tests for ``pipeline._collect_plan_annotations``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nornir_buildmanager.operations.segmentationtraining.pipeline import (
    _collect_plan_annotations,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    MASKS_FOLDER,
)
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord
from nornir_buildmanager.operations.segmentationtraining.workdir import (
    RLE_FOLDER,
    rle_dir,
)

KEY = "vol_3_D1_a"
# Column-major RLE of a 4x4 mask: pixels 5..7 set -> column 1, rows 1..3.
RLE = {"size": [4, 4], "counts": [5, 3, 8]}
RLE_BBOX = [1, 1, 1, 3]
RLE_AREA = 3


class _FakeProducts:
    """Records ``exists`` answers and ``refresh_one`` calls like ``CropProductIndex``."""

    present: set[tuple[str, str]]
    refreshed: list[tuple[str, str]]

    def __init__(self, present: set[tuple[str, str]]) -> None:
        self.present = present
        self.refreshed = []

    def exists(self, folder: str, name: str) -> bool:
        return (folder, name) in self.present

    def refresh_one(self, folder: str, name: str) -> None:
        self.refreshed.append((folder, name))


def _record(location_id: int) -> LocationRecord:
    return LocationRecord(
        id=location_id,
        z=3,
        wkt="POLYGON ((0 0, 1 0, 1 1, 0 0))",
        parent_id=100 + location_id,
        off_edge=False,
        last_modified=datetime(2024, 1, 2, tzinfo=UTC),
        type_id=1,
        type_name="Cell",
        structure_label="C",
    )


def _plan(location_ids: list[int], window_of: dict[int, tuple[int, int]] | None = None) -> Any:
    return SimpleNamespace(
        image_key=KEY,
        location_ids=location_ids,
        window_of=window_of or {},
        window=SimpleNamespace(origin_x=64, origin_y=128),
    )


def _write_rle(output: Path, location_id: int, rle: dict[str, Any] = RLE) -> str:
    folder = rle_dir(output)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{KEY}_{location_id}.json"
    (folder / name).write_text(json.dumps(rle), encoding="utf-8")
    return name


def _collect(
    output: Path,
    plan: Any,
    products: _FakeProducts,
    stats: dict[tuple[str, int], dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    by_id = {location_id: _record(location_id) for location_id in plan.location_ids}
    return _collect_plan_annotations(output, plan, by_id, stats or {}, products)  # type: ignore[arg-type]


@pytest.fixture
def output(tmp_path: Path) -> Path:
    root = tmp_path / "tree" / "CPED"
    root.mkdir(parents=True)
    return root


def test_member_without_indexed_rle_is_skipped(output: Path) -> None:
    _write_rle(output, 1)  # on disk but not indexed: the index is the source of truth
    products = _FakeProducts({(MASKS_FOLDER, f"{KEY}_1.png")})
    assert _collect(output, _plan([1]), products) == ([], [])
    assert products.refreshed == []


def test_unreadable_rle_refreshes_index_and_skips(output: Path) -> None:
    name = f"{KEY}_1.json"
    (rle_dir(output) / name).mkdir(parents=True)  # reading a directory raises OSError
    good = _write_rle(output, 2)
    products = _FakeProducts({(RLE_FOLDER, name), (RLE_FOLDER, good)})
    annotations, mask_paths = _collect(output, _plan([1, 2]), products)
    assert [item["id"] for item in annotations] == [2]
    assert mask_paths == []
    assert products.refreshed == [(RLE_FOLDER, name)]


def test_bbox_and_area_fall_back_to_rle(output: Path) -> None:
    products = _FakeProducts({(RLE_FOLDER, _write_rle(output, 1))})
    annotations, _ = _collect(output, _plan([1]), products)
    assert annotations[0]["bbox"] == RLE_BBOX
    assert annotations[0]["area"] == RLE_AREA
    assert annotations[0]["segmentation"] == RLE


def test_mask_stats_win_over_rle(output: Path) -> None:
    products = _FakeProducts({(RLE_FOLDER, _write_rle(output, 1))})
    stats = {(KEY, 1): {"bbox": [9, 8, 7, 6], "area": 42.0}}
    annotations, _ = _collect(output, _plan([1]), products, stats)
    assert annotations[0]["bbox"] == [9, 8, 7, 6]
    assert annotations[0]["area"] == 42
    assert isinstance(annotations[0]["area"], int)


def test_stats_fall_back_per_field(output: Path) -> None:
    products = _FakeProducts({(RLE_FOLDER, _write_rle(output, 1)), (RLE_FOLDER, _write_rle(output, 2))})
    stats = {(KEY, 1): {"bbox": [], "area": 0}, (KEY, 2): {"bbox": [9, 8, 7, 6]}}
    annotations, _ = _collect(output, _plan([1, 2]), products, stats)
    # An empty bbox is falsy and falls back; area 0 is kept because only None falls back.
    assert (annotations[0]["bbox"], annotations[0]["area"]) == (RLE_BBOX, 0)
    assert (annotations[1]["bbox"], annotations[1]["area"]) == ([9, 8, 7, 6], RLE_AREA)


def test_stats_for_other_image_key_are_ignored(output: Path) -> None:
    products = _FakeProducts({(RLE_FOLDER, _write_rle(output, 1))})
    stats = {("other_key", 1): {"bbox": [9, 8, 7, 6], "area": 42}}
    annotations, _ = _collect(output, _plan([1]), products, stats)
    assert (annotations[0]["bbox"], annotations[0]["area"]) == (RLE_BBOX, RLE_AREA)


def test_window_and_origin_fields(output: Path) -> None:
    products = _FakeProducts({(RLE_FOLDER, _write_rle(output, 1)), (RLE_FOLDER, _write_rle(output, 2))})
    annotations, _ = _collect(output, _plan([1, 2], window_of={2: (3, 5)}), products)
    assert (annotations[0]["windowIndex"], annotations[0]["windowCount"]) == (0, 1)
    assert (annotations[1]["windowIndex"], annotations[1]["windowCount"]) == (3, 5)
    assert all((item["originX"], item["originY"]) == (64, 128) for item in annotations)
    assert annotations[1]["structure_label"] == "C"


def test_mask_paths_only_for_indexed_masks_in_member_order(output: Path) -> None:
    present = {(RLE_FOLDER, _write_rle(output, i)) for i in (3, 1, 2)}
    present |= {(MASKS_FOLDER, f"{KEY}_3.png"), (MASKS_FOLDER, f"{KEY}_2.png")}
    annotations, mask_paths = _collect(output, _plan([3, 1, 2]), _FakeProducts(present))
    assert [item["id"] for item in annotations] == [3, 1, 2]
    masks = output / MASKS_FOLDER
    assert mask_paths == [str(masks / f"{KEY}_3.png"), str(masks / f"{KEY}_2.png")]


def test_mask_without_rle_is_not_listed(output: Path) -> None:
    products = _FakeProducts({(MASKS_FOLDER, f"{KEY}_1.png")})
    assert _collect(output, _plan([1]), products) == ([], [])
