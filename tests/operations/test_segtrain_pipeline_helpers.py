"""Characterization tests for the pure helpers in ``segmentationtraining.pipeline``."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ImageWatermark,
    SectionWatermark,
)
from nornir_buildmanager.operations.segmentationtraining.pipeline import (
    _as_int_list,
    _empty_to_none,
    _image_key_matches_sections,
    _level_mtime,
    _manifest_row,
    _max_tiles,
    _section_meta_numbers,
    missing_crop_marks,
    resolve_max_texture,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    IMAGES_FOLDER,
    CropProductIndex,
    drop_product_index,
)
from nornir_buildmanager.operations.segmentationtraining.workdir import work_root


@pytest.fixture
def output(tmp_path: Path) -> Iterator[Path]:
    """An output tree nested so its sibling work cache stays inside ``tmp_path``."""
    root = tmp_path / "tree" / "CPED"
    root.mkdir(parents=True)
    yield root
    drop_product_index(root)


def _mark(key: str) -> ImageWatermark:
    return ImageWatermark(
        key=key, downsample=1, ix0=0, ix1=1, iy0=0, iy1=1, member_ids=[1], max_last_modified=""
    )


def _watermark(keys: list[str]) -> SectionWatermark:
    return SectionWatermark(
        ids=[1], max_last_modified="", tileset_mtime=None, params_hash="h", images=[_mark(k) for k in keys]
    )


def test_empty_to_none() -> None:
    assert _empty_to_none(None) is None
    assert _empty_to_none("") is None
    assert _empty_to_none("   ") is None
    assert _empty_to_none(" a ") == " a "
    assert _empty_to_none(0) == 0
    assert _empty_to_none([]) == []


def test_as_int_list_missing_values() -> None:
    assert _as_int_list(None) is None
    assert _as_int_list("") is None
    assert _as_int_list([]) is None
    assert _as_int_list("5-3") is None  # empty range


def test_as_int_list_scalar_and_string() -> None:
    assert _as_int_list(5) == [5]
    assert _as_int_list("1,3,5-7") == [1, 3, 5, 6, 7]


def test_as_int_list_list_items() -> None:
    assert _as_int_list([1, "2", 3.0]) == [1, 2, 3]
    assert _as_int_list(["1,2", "4-6", "9"]) == [1, 2, 4, 5, 6, 9]


def test_image_key_matches_sections() -> None:
    key = "vol_12_D1_x0"
    assert _image_key_matches_sections(key, {12})
    assert _image_key_matches_sections(key, {3, 12})
    assert not _image_key_matches_sections(key, {1})
    assert not _image_key_matches_sections(key, set())
    assert not _image_key_matches_sections("vol_112_D1", {12})


def test_section_meta_numbers(output: Path) -> None:
    assert _section_meta_numbers(output) == []
    work = work_root(output)
    work.mkdir(parents=True)
    for name in ("section_10.meta.json", "section_2.meta.json", "section_x.meta.json", "other_5.meta.json"):
        (work / name).write_text("{}")
    assert _section_meta_numbers(output) == [2, 10]


def test_level_mtime_prefers_validation_time(tmp_path: Path) -> None:
    stamp = datetime(2024, 5, 6, 7, 8, 9)
    level = SimpleNamespace(ValidationTime=stamp, FullPath=str(tmp_path))
    assert _level_mtime(level) == stamp.timestamp()


def test_level_mtime_falls_back_to_directory(tmp_path: Path) -> None:
    os.utime(tmp_path, (1_000_000, 1_000_000))
    for validation in (None, datetime.min, "not a datetime"):
        level = SimpleNamespace(ValidationTime=validation, FullPath=str(tmp_path))
        assert _level_mtime(level) == 1_000_000
    assert _level_mtime(SimpleNamespace(FullPath=str(tmp_path))) == 1_000_000


def test_level_mtime_none_without_directory(tmp_path: Path) -> None:
    assert _level_mtime(SimpleNamespace(ValidationTime=None, FullPath=str(tmp_path / "gone"))) is None
    assert _level_mtime(SimpleNamespace(ValidationTime=None, FullPath="")) is None
    assert _level_mtime(SimpleNamespace()) is None


def test_resolve_max_texture() -> None:
    assert resolve_max_texture(None) == 1024
    assert resolve_max_texture(0) == 1024
    assert resolve_max_texture(2048) == 2048


def test_max_tiles_override_wins() -> None:
    assert _max_tiles(3, 1024, 512, 512) == (3, 3)
    assert _max_tiles(-4, 1024, 512, 512) == (1, 1)


def test_max_tiles_from_texture() -> None:
    assert _max_tiles(None, 1024, 512, 256) == (2, 4)
    assert _max_tiles(0, 1024, 512, 256) == (2, 4)
    assert _max_tiles(None, 100, 512, 512) == (1, 1)


def test_max_tiles_rejects_nonpositive_tile_size() -> None:
    with pytest.raises(NornirUserException):
        _max_tiles(None, 1024, 0, 512)
    with pytest.raises(NornirUserException):
        _max_tiles(None, 1024, 512, -1)
    assert _max_tiles(2, 1024, 0, 0) == (2, 2)


def test_missing_crop_marks_none_watermark(output: Path) -> None:
    assert missing_crop_marks(None, CropProductIndex(output)) == []
    assert missing_crop_marks(_watermark([]), CropProductIndex(output)) == []


def test_missing_crop_marks_png_or_jpg_counts_as_present(output: Path) -> None:
    images = output / IMAGES_FOLDER
    images.mkdir()
    (images / "a.png").write_bytes(b"x")
    (images / "b.jpg").write_bytes(b"x")
    (images / "c.txt").write_bytes(b"x")
    marks = missing_crop_marks(_watermark(["a", "b", "c", "d"]), CropProductIndex(output))
    assert [m.key for m in marks] == ["c", "d"]


def test_missing_crop_marks_dedups_by_key(output: Path) -> None:
    marks = missing_crop_marks(_watermark(["x", "x", "y"]), CropProductIndex(output))
    assert [m.key for m in marks] == ["x", "y"]


def test_manifest_row() -> None:
    plan = SimpleNamespace(image_key="vol_3_D1_a", downsample=4, location_ids=[7, 9])
    assert _manifest_row(plan, 3, "vol") == {  # type: ignore[arg-type]
        "z": 3,
        "volume": "vol",
        "imageKey": "vol_3_D1_a",
        "downsample": 4,
        "image": "images/vol_3_D1_a.png",
        "json": "images/vol_3_D1_a.json",
        "locationIds": [7, 9],
    }
