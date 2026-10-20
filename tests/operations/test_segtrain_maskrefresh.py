"""Characterization tests for the private helpers in ``maskrefresh``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from nornir_buildmanager.operations.segmentationtraining.freshness import ImageWatermark
from nornir_buildmanager.operations.segmentationtraining.maskrefresh import (
    _crop_image,
    _mark_with_extent,
    _marks_from_crop_json,
    _move_refreshed_masks_if_ignored,
)


def _mark(key: str = "a", width: int = 0, height: int = 0) -> ImageWatermark:
    return ImageWatermark(
        key=key,
        downsample=2,
        ix0=1,
        ix1=2,
        iy0=3,
        iy1=4,
        member_ids=[7],
        max_last_modified="t",
        origin_x=5,
        origin_y=6,
        width=width,
        height=height,
    )


def _mask(output: Path, key: str, location_id: int, content: bytes = b"new") -> Path:
    path = output / "masks" / f"{key}_{location_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _ignore(output: Path, text: str) -> None:
    (output / "ignore.json").write_text(text, encoding="utf-8")


def test_ignored_id_moves_masks_and_overwrites_destination(tmp_path: Path) -> None:
    _ignore(tmp_path, json.dumps([3, 7]))
    source = _mask(tmp_path, "a", 7, b"new")
    stale = tmp_path / "ignored" / "a_7.png"
    stale.parent.mkdir()
    stale.write_bytes(b"old")
    _move_refreshed_masks_if_ignored(tmp_path, 7, ["a"])
    assert not source.exists()
    assert stale.read_bytes() == b"new"


def test_unlisted_id_leaves_masks(tmp_path: Path) -> None:
    _ignore(tmp_path, json.dumps([3]))
    source = _mask(tmp_path, "a", 7)
    _move_refreshed_masks_if_ignored(tmp_path, 7, ["a"])
    assert source.is_file()
    assert not (tmp_path / "ignored").exists()


def test_other_locations_masks_are_not_moved(tmp_path: Path) -> None:
    _ignore(tmp_path, json.dumps([7]))
    other = _mask(tmp_path, "a", 8)
    _move_refreshed_masks_if_ignored(tmp_path, 7, ["a"])
    assert other.is_file()


def test_missing_mask_is_skipped(tmp_path: Path) -> None:
    _ignore(tmp_path, json.dumps([7]))
    present = _mask(tmp_path, "b", 7)
    _move_refreshed_masks_if_ignored(tmp_path, 7, ["a", "b"])
    assert not present.exists()
    assert (tmp_path / "ignored" / "b_7.png").is_file()


@pytest.mark.parametrize("text", ["{not json", '{"a": 7}', "7", '"7"'])
def test_corrupt_or_non_list_ignore_file_changes_nothing(tmp_path: Path, text: str) -> None:
    _ignore(tmp_path, text)
    source = _mask(tmp_path, "a", 7)
    _move_refreshed_masks_if_ignored(tmp_path, 7, ["a"])
    assert source.is_file()


def test_missing_ignore_file_or_no_keys_changes_nothing(tmp_path: Path) -> None:
    source = _mask(tmp_path, "a", 7)
    _move_refreshed_masks_if_ignored(tmp_path, 7, ["a"])
    _ignore(tmp_path, json.dumps([7]))
    _move_refreshed_masks_if_ignored(tmp_path, 7, [])
    assert source.is_file()


def test_non_int_entries_are_skipped_and_numeric_strings_match(tmp_path: Path) -> None:
    _ignore(tmp_path, json.dumps([None, "x", [1], "7"]))
    source = _mask(tmp_path, "a", 7)
    _move_refreshed_masks_if_ignored(tmp_path, 7, ["a"])
    assert not source.exists()
    assert (tmp_path / "ignored" / "a_7.png").is_file()


def _crop_json(output: Path, key: str, payload: object) -> None:
    images = output / "images"
    images.mkdir(exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (images / f"{key}.json").write_text(text, encoding="utf-8")


def test_marks_from_crop_json_without_images_dir(tmp_path: Path) -> None:
    assert _marks_from_crop_json(tmp_path, 7) == []


def test_marks_from_crop_json_builds_mark_from_matching_crop(tmp_path: Path) -> None:
    _crop_json(
        tmp_path,
        "a",
        {
            "image": {"downsample": 4, "width": 30, "height": 20},
            "annotations": [
                {"id": 1},
                {"id": 7, "last_modified": "2024-01-01", "originX": 5, "originY": 6},
            ],
        },
    )
    (mark,) = _marks_from_crop_json(tmp_path, 7)
    assert (mark.key, mark.downsample, mark.member_ids) == ("a", 4, [7])
    assert (mark.ix0, mark.ix1, mark.iy0, mark.iy1) == (0, 0, 0, 0)
    assert mark.max_last_modified == "2024-01-01"
    assert (mark.origin_x, mark.origin_y, mark.width, mark.height) == (5, 6, 30, 20)


def test_marks_from_crop_json_defaults_when_fields_missing(tmp_path: Path) -> None:
    _crop_json(tmp_path, "a", {"annotations": [{"id": 7}]})
    (mark,) = _marks_from_crop_json(tmp_path, 7)
    assert (mark.downsample, mark.origin_x, mark.origin_y, mark.width, mark.height) == (1, 0, 0, 0, 0)
    assert mark.max_last_modified == ""


def test_marks_from_crop_json_skips_unreadable_and_non_matching(tmp_path: Path) -> None:
    _crop_json(tmp_path, "bad", "{nope")
    _crop_json(tmp_path, "other", {"annotations": [{"id": 8}, "junk"]})
    _crop_json(tmp_path, "none", {})
    _crop_json(tmp_path, "good", {"annotations": ["junk", {"id": 7}]})
    assert [m.key for m in _marks_from_crop_json(tmp_path, 7)] == ["good"]


def test_marks_from_crop_json_uses_first_matching_annotation(tmp_path: Path) -> None:
    _crop_json(
        tmp_path,
        "a",
        {
            "annotations": [
                "junk",
                {"id": 8, "originX": 99},
                {"id": "7", "originX": 1, "originY": 2, "last_modified": "first"},
                {"id": 7, "originX": 3, "originY": 4, "last_modified": "second"},
            ]
        },
    )
    (mark,) = _marks_from_crop_json(tmp_path, 7)
    assert (mark.origin_x, mark.origin_y, mark.max_last_modified) == (1, 2, "first")


def test_mark_with_extent_none_when_image_missing(tmp_path: Path) -> None:
    (tmp_path / "images").mkdir()
    assert _mark_with_extent(tmp_path, _mark()) is None


def test_mark_with_extent_keeps_mark_with_known_size(tmp_path: Path) -> None:
    (tmp_path / "images").mkdir()
    Image.new("L", (9, 8)).save(tmp_path / "images" / "a.png")
    mark = _mark(width=3, height=4)
    assert _mark_with_extent(tmp_path, mark) is mark


@pytest.mark.parametrize("known", [(0, 5), (5, 0), (0, 0)])
def test_mark_with_extent_fills_size_from_image(tmp_path: Path, known: tuple[int, int]) -> None:
    (tmp_path / "images").mkdir()
    Image.new("L", (9, 8)).save(tmp_path / "images" / "a.png")
    filled = _mark_with_extent(tmp_path, _mark(width=known[0], height=known[1]))
    assert filled is not None
    assert (filled.width, filled.height) == (9, 8)
    assert (filled.key, filled.downsample, filled.member_ids) == ("a", 2, [7])
    assert (filled.ix0, filled.ix1, filled.iy0, filled.iy1) == (1, 2, 3, 4)
    assert (filled.origin_x, filled.origin_y, filled.max_last_modified) == (5, 6, "t")


def test_mark_with_extent_reads_jpeg_fallback(tmp_path: Path) -> None:
    (tmp_path / "images").mkdir()
    Image.new("RGB", (6, 5)).save(tmp_path / "images" / "a.jpg")
    filled = _mark_with_extent(tmp_path, _mark())
    assert filled is not None
    assert (filled.width, filled.height) == (6, 5)


def test_crop_image_prefers_png_then_jpg_then_png_path(tmp_path: Path) -> None:
    assert _crop_image(tmp_path, "a") == tmp_path / "a.png"
    (tmp_path / "a.jpg").write_bytes(b"x")
    assert _crop_image(tmp_path, "a") == tmp_path / "a.jpg"
    (tmp_path / "a.png").write_bytes(b"x")
    assert _crop_image(tmp_path, "a") == tmp_path / "a.png"
