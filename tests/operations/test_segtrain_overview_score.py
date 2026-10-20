"""Characterization tests for segmentationtraining overview (split-mask cards) and score (SAM2 columns)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.segmentationtraining.catalog import (
    connect,
    list_catalog_rows,
)
from nornir_buildmanager.operations.segmentationtraining.freshness import ImageWatermark
from nornir_buildmanager.operations.segmentationtraining.overview import (
    _grid_cell,
    write_split_mask_overviews,
)
from nornir_buildmanager.operations.segmentationtraining.score import (
    ScoreAnnotationCrops,
    box_iou,
    mask_bbox,
)


def _mark(key: str, ids: list[int], x: int, y: int, width: int = 8, height: int = 8) -> ImageWatermark:
    """Build a watermark for one crop with the given origin and size."""
    return ImageWatermark(
        key=key, downsample=1, ix0=0, ix1=1, iy0=0, iy1=1, member_ids=ids,
        max_last_modified="", origin_x=x, origin_y=y, width=width, height=height,
    )


def _parts(output: Path, location_id: int) -> Path:
    return output / "overlays" / "parts" / str(location_id)


def _payload(output: Path, location_id: int) -> dict[str, Any]:
    return json.loads(_parts(output, location_id).with_suffix(".json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- _grid_cell


def test_grid_cell_uses_the_shared_edge() -> None:
    assert _grid_cell([_mark("a", [1], 0, 0, 8, 8), _mark("b", [1], 8, 0, 8, 8)], 1024) == 8


def test_grid_cell_mixed_edges_fall_back_to_max_edge() -> None:
    assert _grid_cell([_mark("a", [1], 0, 0, 8, 4)], 1024) == 1024
    assert _grid_cell([_mark("a", [1], 0, 0, 8, 8), _mark("b", [1], 8, 0, 4, 8)], 512) == 512


def test_grid_cell_ignores_zero_edges() -> None:
    assert _grid_cell([_mark("a", [1], 0, 0, 8, 0)], 1024) == 8


def test_grid_cell_clamps_max_edge_to_one() -> None:
    assert _grid_cell([_mark("a", [1], 0, 0, 8, 4)], 0) == 1
    assert _grid_cell([_mark("a", [1], 0, 0, 8, 4)], -5) == 1


# ------------------------------------------------- write_split_mask_overviews


def test_overview_skips_zero_size_marks(tmp_path: Path) -> None:
    marks = [_mark("a", [1], 0, 0), _mark("zero_w", [1], 8, 0, 0, 8), _mark("zero_h", [1], 16, 0, 8, 0)]
    assert write_split_mask_overviews(tmp_path, marks, {1}, max_edge=8) == 0
    assert not _parts(tmp_path, 1).with_suffix(".json").exists()


def test_overview_only_writes_requested_location_ids(tmp_path: Path) -> None:
    marks = [_mark("a", [1, 2], 0, 0), _mark("b", [1, 2], 8, 0)]
    assert write_split_mask_overviews(tmp_path, marks, {2}, max_edge=8) == 1
    assert _parts(tmp_path, 2).with_suffix(".json").is_file()
    assert not _parts(tmp_path, 1).with_suffix(".json").exists()


def test_overview_duplicate_keys_count_as_one_crop_and_remove_stale_parts(tmp_path: Path) -> None:
    folder = tmp_path / "overlays" / "parts"
    folder.mkdir(parents=True)
    (folder / "4.json").write_text("{}", encoding="utf-8")
    (folder / "4.png").write_bytes(b"x")
    (folder / "99.json").write_text("{}", encoding="utf-8")
    marks = [_mark("a", [4], 0, 0), _mark("a", [4], 0, 0)]
    assert write_split_mask_overviews(tmp_path, marks, {4}, max_edge=8) == 0
    assert not (folder / "4.json").exists()
    assert not (folder / "4.png").exists()
    # A location the caller did not ask about keeps its card.
    assert (folder / "99.json").exists()


def test_overview_payload_geometry_and_part_order(tmp_path: Path) -> None:
    # Offset origin; supplied out of order. Parts sort by (origin_y, origin_x, key).
    marks = [
        _mark("d", [7], 108, 58),
        _mark("c", [7], 100, 58),
        _mark("b", [7], 108, 50),
        _mark("a", [7], 100, 50),
    ]
    assert write_split_mask_overviews(tmp_path, marks, {7}, max_edge=1024) == 1
    payload = _payload(tmp_path, 7)
    assert payload["locationId"] == 7
    assert (payload["originX"], payload["originY"]) == (100, 50)
    assert (payload["width"], payload["height"]) == (16, 16)
    assert (payload["gridColumns"], payload["gridRows"]) == (2, 2)
    assert [part["imageKey"] for part in payload["parts"]] == ["a", "b", "c", "d"]
    assert [part["windowIndex"] for part in payload["parts"]] == [0, 1, 2, 3]
    assert {part["windowCount"] for part in payload["parts"]} == {4}
    # Part coordinates are relative to the shared origin.
    assert [(part["x"], part["y"]) for part in payload["parts"]] == [(0, 0), (8, 0), (0, 8), (8, 8)]
    assert [(part["gridX"], part["gridY"]) for part in payload["parts"]] == [(0, 0), (1, 0), (0, 1), (1, 1)]


def test_overview_png_flag_controls_the_png(tmp_path: Path) -> None:
    marks = [_mark("a", [3], 0, 0), _mark("b", [3], 8, 0)]
    write_split_mask_overviews(tmp_path, marks, {3}, max_edge=8, png=False)
    assert not _parts(tmp_path, 3).with_suffix(".png").exists()
    write_split_mask_overviews(tmp_path, marks, {3}, max_edge=8, png=True)
    assert _parts(tmp_path, 3).with_suffix(".png").is_file()


def test_overview_png_survives_a_missing_mask_file(tmp_path: Path) -> None:
    marks = [_mark("a", [3], 0, 0), _mark("b", [3], 8, 0)]
    (tmp_path / "masks").mkdir()
    Image.new("L", (8, 8), 255).save(tmp_path / "masks" / "a_3.png")
    # "b" has no mask anywhere; its region stays black.
    assert write_split_mask_overviews(tmp_path, marks, {3}, max_edge=8, png=True) == 1
    with Image.open(_parts(tmp_path, 3).with_suffix(".png")) as canvas:
        rgb = canvas.convert("RGB")
        assert rgb.size == (16, 8)
        assert rgb.getpixel((2, 2)) == (255, 0, 0)  # part 0 colour
        assert rgb.getpixel((10, 2)) == (0, 0, 0)


def test_overview_png_falls_back_to_the_ignored_folder(tmp_path: Path) -> None:
    marks = [_mark("a", [3], 0, 0), _mark("b", [3], 8, 0)]
    (tmp_path / "ignored").mkdir()
    Image.new("L", (8, 8), 255).save(tmp_path / "ignored" / "b_3.png")
    write_split_mask_overviews(tmp_path, marks, {3}, max_edge=8, png=True)
    with Image.open(_parts(tmp_path, 3).with_suffix(".png")) as canvas:
        assert canvas.convert("RGB").getpixel((10, 2)) == (0, 255, 0)  # part 1 colour


# ---------------------------------------------------------------- mask_bbox


def test_mask_bbox_empty_mask_is_none(tmp_path: Path) -> None:
    path = tmp_path / "empty.png"
    Image.new("L", (10, 10), 0).save(path)
    assert mask_bbox(path) is None


def test_mask_bbox_off_origin_rectangle(tmp_path: Path) -> None:
    path = tmp_path / "rect.png"
    image = Image.new("L", (20, 20), 0)
    for x in range(3, 8):
        for y in range(5, 9):
            image.putpixel((x, y), 255)
    image.save(path)
    assert mask_bbox(path) == [3, 5, 5, 4]
    assert mask_bbox(str(path)) == [3, 5, 5, 4]


def test_mask_bbox_single_pixel(tmp_path: Path) -> None:
    path = tmp_path / "dot.png"
    image = Image.new("L", (6, 6), 0)
    image.putpixel((5, 0), 1)
    image.save(path)
    assert mask_bbox(path) == [5, 0, 1, 1]


# ------------------------------------------------------------------ box_iou


def test_box_iou_partial_overlap() -> None:
    # 10x10 boxes offset by 5 in x: inter 50, union 150.
    assert box_iou([0, 0, 10, 10], [5, 0, 10, 10]) == pytest.approx(1 / 3)


def test_box_iou_zero_size_box_is_zero() -> None:
    assert box_iou([0, 0, 0, 10], [0, 0, 10, 10]) == 0.0
    assert box_iou([0, 0, 10, 10], [0, 0, 10, 0]) == 0.0
    assert box_iou([0, 0, -1, 10], [0, 0, 10, 10]) == 0.0


def test_box_iou_touching_edges_is_zero() -> None:
    assert box_iou([0, 0, 5, 5], [5, 0, 5, 5]) == 0.0


def test_box_iou_contained_box() -> None:
    assert box_iou([0, 0, 10, 10], [2, 2, 5, 5]) == pytest.approx(25 / 100)


def test_box_iou_is_symmetric() -> None:
    first, second = [1, 2, 7, 4], [3, 0, 6, 9]
    assert box_iou(first, second) == box_iou(second, first)


# ------------------------------------------------------ ScoreAnnotationCrops


def _catalog(output: Path, *, with_files: bool = True, location_id: int = 5) -> None:
    """Create a one-row catalog whose mask covers [4, 4, 8, 8] of a 16x16 image."""
    (output / "images").mkdir(parents=True, exist_ok=True)
    (output / "masks").mkdir(exist_ok=True)
    if with_files:
        Image.new("L", (16, 16), 20).save(output / "images" / "k.png")
        mask = Image.new("L", (16, 16), 0)
        mask.paste(255, (4, 4, 12, 12))
        mask.save(output / "masks" / f"k_{location_id}.png")
    connection = connect(output)
    try:
        connection.execute(
            "INSERT INTO locations (location_id, z, image_key, image_relpath, mask_relpath, ignored) "
            "VALUES (?, 1, 'k', 'images/k.png', ?, 0)",
            (location_id, f"masks/k_{location_id}.png"),
        )
        connection.commit()
    finally:
        connection.close()


def test_score_requires_output_path() -> None:
    with pytest.raises(NornirUserException):
        ScoreAnnotationCrops(OutputPath=None)
    with pytest.raises(NornirUserException):
        ScoreAnnotationCrops(OutputPath="")


def test_score_without_sqlite_returns_none_and_creates_nothing(tmp_path: Path) -> None:
    calls: list[object] = []
    result = ScoreAnnotationCrops(OutputPath=str(tmp_path), Predictor=lambda **kw: calls.append(kw) or {})
    assert result is None
    assert calls == []
    assert not (tmp_path / "annotation_crops.sqlite").exists()


def test_score_fills_gt_iou_from_pred_bbox_and_defaults(tmp_path: Path) -> None:
    _catalog(tmp_path)
    seen: dict[str, Any] = {}

    def predictor(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"sam2PredIou": 0.5, "pred_bbox": [8, 4, 8, 8]}  # half of the gt box overlaps

    ScoreAnnotationCrops(OutputPath=str(tmp_path), Checkpoint="ck", Predictor=predictor)
    assert seen["bbox"] == [4, 4, 8, 8]
    assert seen["location_id"] == 5
    assert seen["checkpoint"] == "ck"
    row = list_catalog_rows(tmp_path)[0]
    assert row["sam2GtIou"] == pytest.approx(box_iou([4, 4, 8, 8], [8, 4, 8, 8]))
    assert row["sam2PredIou"] == pytest.approx(0.5)
    assert row["sam2Checkpoint"] == "ck"
    assert row["sam2ScoredAt"]


def test_score_does_not_overwrite_payload_values(tmp_path: Path) -> None:
    _catalog(tmp_path)

    def predictor(**kwargs: Any) -> dict[str, Any]:
        return {
            "sam2GtIou": 0.11,
            "sam2Checkpoint": "from-payload",
            "sam2ScoredAt": "2020-01-01T00:00:00+00:00",
            "pred_bbox": [4, 4, 8, 8],
        }

    ScoreAnnotationCrops(OutputPath=str(tmp_path), Checkpoint="ck", Predictor=predictor)
    row = list_catalog_rows(tmp_path)[0]
    assert row["sam2GtIou"] == pytest.approx(0.11)
    assert row["sam2Checkpoint"] == "from-payload"
    assert row["sam2ScoredAt"] == "2020-01-01T00:00:00+00:00"


def test_score_leaves_gt_iou_empty_without_pred_bbox(tmp_path: Path) -> None:
    _catalog(tmp_path)
    ScoreAnnotationCrops(OutputPath=str(tmp_path), Predictor=lambda **kw: {"sam2PredIou": 0.9})
    row = list_catalog_rows(tmp_path)[0]
    assert row["sam2GtIou"] is None
    assert row["sam2Checkpoint"] is None  # Checkpoint argument was None


def test_score_skips_rows_whose_files_are_missing(tmp_path: Path) -> None:
    _catalog(tmp_path, with_files=False)
    calls: list[object] = []
    ScoreAnnotationCrops(OutputPath=str(tmp_path), Predictor=lambda **kw: calls.append(kw) or {"sam2PredIou": 1.0})
    assert calls == []
    assert list_catalog_rows(tmp_path)[0]["sam2PredIou"] is None


@pytest.mark.parametrize("missing", ["images/k.png", "masks/k_5.png"])
def test_score_skips_rows_missing_either_file(tmp_path: Path, missing: str) -> None:
    _catalog(tmp_path)
    (tmp_path / missing).unlink()
    calls: list[object] = []
    ScoreAnnotationCrops(OutputPath=str(tmp_path), Predictor=lambda **kw: calls.append(kw) or {"sam2PredIou": 1.0})
    assert calls == []


def test_score_without_predictor_logs_and_returns_when_stack_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A RuntimeError from load_predictor is logged, not raised, and no scores are written."""
    from nornir_buildmanager.operations.segmentationtraining import score

    _catalog(tmp_path)
    requested: list[object] = []

    def _unavailable(checkpoint: object) -> None:
        requested.append(checkpoint)
        raise RuntimeError("SAM2 is not installed")

    monkeypatch.setattr(score, "load_predictor", _unavailable)
    assert ScoreAnnotationCrops(OutputPath=str(tmp_path), Checkpoint="ck") is None
    assert requested == ["ck"]
    row = list_catalog_rows(tmp_path)[0]
    assert row["sam2PredIou"] is None
    assert row["sam2ScoredAt"] is None


@pytest.mark.parametrize("mode,fill", [("1", 1), ("RGB", (255, 255, 255))])
def test_mask_bbox_converts_non_grayscale_modes(tmp_path: Path, mode: str, fill: object) -> None:
    path = tmp_path / f"mask_{mode}.png"
    image = Image.new(mode, (12, 12), 0)
    image.paste(fill, (2, 3, 6, 9))
    image.save(path)
    assert mask_bbox(path) == [2, 3, 4, 6]


def test_score_scores_every_row_with_its_own_location_id(tmp_path: Path) -> None:
    _catalog(tmp_path, location_id=5)
    _catalog(tmp_path, location_id=9)
    seen: list[int] = []

    def predictor(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs["location_id"])
        return {"sam2PredIou": float(kwargs["location_id"]) / 10}

    ScoreAnnotationCrops(OutputPath=str(tmp_path), Predictor=predictor)
    assert sorted(seen) == [5, 9]
    scores = {row["location_id"]: row["sam2PredIou"] for row in list_catalog_rows(tmp_path)}
    assert scores == {5: pytest.approx(0.5), 9: pytest.approx(0.9)}
