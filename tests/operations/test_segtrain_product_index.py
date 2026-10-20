"""Characterization tests for ``segmentationtraining.product_index.CropProductIndex``."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    IGNORED_FOLDER,
    IMAGES_FOLDER,
    MASKS_FOLDER,
    CropProductIndex,
    drop_product_index,
    product_folder_path,
    split_member_stem,
)
from nornir_buildmanager.operations.segmentationtraining.workdir import (
    RLE_FOLDER,
    rle_dir,
)


@pytest.fixture
def output(tmp_path: Path) -> Iterator[Path]:
    """An empty output tree nested so its sibling ``_work_cache`` stays inside ``tmp_path``."""
    root = tmp_path / "tree" / "CPED"
    root.mkdir(parents=True)
    yield root
    drop_product_index(root)


def _touch(path: Path, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_split_member_stem_edges() -> None:
    assert split_member_stem("noscore") == ("noscore", None)
    assert split_member_stem("img_abc") == ("img_abc", None)
    assert split_member_stem("img_12") == ("img", 12)
    assert split_member_stem("a_b_7") == ("a_b", 7)
    assert split_member_stem("img_") == ("img_", None)
    assert split_member_stem("img_05") == ("img", 5)


def test_product_folder_path_routes_rle_to_work_folder(output: Path) -> None:
    assert product_folder_path(output, RLE_FOLDER) == rle_dir(output)
    assert product_folder_path(output, "images") == output / "images"
    assert product_folder_path(output, "a/b") == output / "a" / "b"


def test_initial_scan_indexes_files_and_members(output: Path) -> None:
    _touch(output / IMAGES_FOLDER / "k.png", 100.0)
    _touch(output / MASKS_FOLDER / "k_3.png", 200.0)
    _touch(output / MASKS_FOLDER / "k_5.png")
    _touch(output / MASKS_FOLDER / "k_5.json")
    (output / MASKS_FOLDER / "subdir").mkdir()
    _touch(rle_dir(output) / "k_9.json")

    index = CropProductIndex(output)

    assert index.exists(IMAGES_FOLDER, "k.png")
    assert index.mtime(IMAGES_FOLDER, "k.png") == pytest.approx(100.0)
    assert not index.exists(MASKS_FOLDER, "subdir")
    assert not index.exists("nope", "k.png")
    assert index.mtime(MASKS_FOLDER, "missing.png") is None
    assert sorted(index.iter_names(MASKS_FOLDER)) == ["k_3.png", "k_5.json", "k_5.png"]
    assert index.exists(RLE_FOLDER, "k_9.json")
    # members maps one name per id (later same-id name wins), member_names keeps all.
    assert {i for i, _ in index.members(MASKS_FOLDER, "k")} == {3, 5}
    assert sorted(index.member_names(MASKS_FOLDER, "k")) == [("k_3.png", 3), ("k_5.json", 5), ("k_5.png", 5)]
    assert index.members(IMAGES_FOLDER, "k") == []
    assert index.member_names(MASKS_FOLDER, "other") == []


def test_same_id_variants_both_kept_and_forget_drops_empty_key(output: Path) -> None:
    index = CropProductIndex(output)
    index.note(MASKS_FOLDER, "k_05.png", 1.0)
    index.note(MASKS_FOLDER, "k_5.png", 2.0)
    assert sorted(index.member_names(MASKS_FOLDER, "k")) == [("k_05.png", 5), ("k_5.png", 5)]

    index.forget(MASKS_FOLDER, "k_05.png")
    assert index.member_names(MASKS_FOLDER, "k") == [("k_5.png", 5)]
    index.forget(MASKS_FOLDER, "k_5.png")
    assert index.member_names(MASKS_FOLDER, "k") == []
    assert "k" not in index._member_names[MASKS_FOLDER]


def test_member_names_is_a_snapshot(output: Path) -> None:
    index = CropProductIndex(output)
    index.note(MASKS_FOLDER, "k_1.png", 1.0)
    index.note(MASKS_FOLDER, "k_2.png", 1.0)
    for name, _ in index.member_names(MASKS_FOLDER, "k"):
        index.forget(MASKS_FOLDER, name)
    assert index.member_names(MASKS_FOLDER, "k") == []


def test_note_with_explicit_mtime_does_not_touch_disk(output: Path) -> None:
    index = CropProductIndex(output)
    index.note(IMAGES_FOLDER, "ghost.png", 42.0)
    assert index.mtime(IMAGES_FOLDER, "ghost.png") == 42.0
    # Non-member folders never register members.
    assert index.members(IMAGES_FOLDER, "ghost") == []


def test_note_without_mtime_stats_file_or_forgets_missing(output: Path) -> None:
    index = CropProductIndex(output)
    path = _touch(output / IMAGES_FOLDER / "a.png", 77.0)
    index.note(IMAGES_FOLDER, "a.png")
    assert index.mtime(IMAGES_FOLDER, "a.png") == pytest.approx(77.0)

    path.unlink()
    index.note(IMAGES_FOLDER, "a.png")
    assert not index.exists(IMAGES_FOLDER, "a.png")

    index.note(IMAGES_FOLDER, "never.png")
    assert not index.exists(IMAGES_FOLDER, "never.png")


def test_note_without_mtime_for_missing_member_unregisters_it(output: Path) -> None:
    index = CropProductIndex(output)
    index.note(MASKS_FOLDER, "k_4.png", 1.0)
    index.note(MASKS_FOLDER, "k_4.png")  # file absent on disk
    assert index.members(MASKS_FOLDER, "k") == []
    assert index.member_names(MASKS_FOLDER, "k") == []


def test_refresh_one_live_stat_and_forgets_missing(output: Path) -> None:
    index = CropProductIndex(output)
    path = _touch(output / MASKS_FOLDER / "k_8.png", 10.0)
    assert index.refresh_one(MASKS_FOLDER, "k_8.png") == pytest.approx(10.0)
    assert index.members(MASKS_FOLDER, "k") == [(8, "k_8.png")]

    os.utime(path, (20.0, 20.0))
    assert index.refresh_one(MASKS_FOLDER, "k_8.png") == pytest.approx(20.0)
    assert index.mtime(MASKS_FOLDER, "k_8.png") == pytest.approx(20.0)

    path.unlink()
    assert index.refresh_one(MASKS_FOLDER, "k_8.png") is None
    assert not index.exists(MASKS_FOLDER, "k_8.png")
    assert index.members(MASKS_FOLDER, "k") == []


def test_note_path_routes_by_location_and_forget_drops_names(output: Path) -> None:
    index = CropProductIndex(output)
    in_masks = _touch(output / MASKS_FOLDER / "k_1.png", 5.0)
    in_rle = _touch(rle_dir(output) / "k_1.json", 6.0)
    root_level = _touch(output / "stray.png")
    outside = _touch(output.parent / "elsewhere" / "k_1.png")

    index.note_path(in_masks)
    index.note_path(in_rle)
    index.note_path(root_level)
    index.note_path(outside)

    assert index.exists(MASKS_FOLDER, "k_1.png")
    assert index.exists(RLE_FOLDER, "k_1.json")
    assert index.members(RLE_FOLDER, "k") == [(1, "k_1.json")]
    assert not any(index.exists(f, "stray.png") for f in (MASKS_FOLDER, IMAGES_FOLDER, IGNORED_FOLDER))
    assert not index.exists(MASKS_FOLDER, "elsewhere")

    index.forget(MASKS_FOLDER, "k_1.png")
    index.forget(RLE_FOLDER, "k_1.json")
    assert not index.exists(MASKS_FOLDER, "k_1.png")
    assert not index.exists(RLE_FOLDER, "k_1.json")


def test_rescan_rebuilds_folder_and_members(output: Path) -> None:
    index = CropProductIndex(output)
    index.note(MASKS_FOLDER, "stale_1.png", 1.0)
    _touch(output / MASKS_FOLDER / "fresh_2.png", 3.0)

    index.rescan(MASKS_FOLDER)

    assert list(index.iter_names(MASKS_FOLDER)) == ["fresh_2.png"]
    assert index.members(MASKS_FOLDER, "stale") == []
    assert index.members(MASKS_FOLDER, "fresh") == [(2, "fresh_2.png")]
    assert index.member_names(MASKS_FOLDER, "fresh") == [("fresh_2.png", 2)]
