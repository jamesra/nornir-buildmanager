"""Characterization tests for ``cleanup._repair_watermarks`` (section watermark repair)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from nornir_buildmanager.operations.segmentationtraining.cleanup import (
    CleanupSummary,
    _repair_watermarks,
)
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ImageWatermark,
    SectionWatermark,
    load_section_watermark,
    save_section_watermark,
    section_meta_path,
)


def _image_watermark(key: str) -> ImageWatermark:
    return ImageWatermark(
        key=key,
        downsample=1,
        ix0=0,
        ix1=1,
        iy0=0,
        iy1=1,
        member_ids=[1],
        max_last_modified="2020-01-01T00:00:00",
    )


def _watermark(keys: list[str]) -> SectionWatermark:
    return SectionWatermark(
        ids=[1],
        max_last_modified="2020-01-01T00:00:00",
        tileset_mtime=None,
        params_hash="h",
        downsample=1,
        image_keys=list(keys),
        images=[_image_watermark(key) for key in keys],
    )


def _make_crop(output: Path, key: str) -> None:
    images = output / "images"
    images.mkdir(exist_ok=True)
    Image.fromarray(np.zeros((2, 2), dtype=np.uint8), mode="L").save(images / f"{key}.png")


def _out(tmp_path: Path) -> Path:
    out = tmp_path / "CPED"
    out.mkdir()
    return out


def test_no_work_folder_is_a_noop(tmp_path: Path) -> None:
    summary = CleanupSummary()
    _repair_watermarks(_out(tmp_path), None, summary)
    assert summary == CleanupSummary()


def test_nondigit_token_and_missing_loader_result_are_skipped(tmp_path: Path) -> None:
    out = _out(tmp_path)
    save_section_watermark(out, 5, _watermark(["gone"]))
    odd = section_meta_path(out, 5).parent / "section_abc.meta.json"
    odd.write_text("not json", encoding="utf-8")
    summary = CleanupSummary()
    _repair_watermarks(out, {99}, summary)  # 5 not wanted; abc skipped before parse
    assert summary == CleanupSummary()
    assert section_meta_path(out, 5).is_file()
    assert odd.is_file()


def test_section_outside_wanted_is_untouched(tmp_path: Path) -> None:
    out = _out(tmp_path)
    save_section_watermark(out, 3, _watermark(["gone"]))
    summary = CleanupSummary()
    _repair_watermarks(out, {4}, summary)
    assert summary.deleted_watermarks == 0
    assert section_meta_path(out, 3).is_file()


def test_empty_after_filtering_deletes_meta(tmp_path: Path) -> None:
    out = _out(tmp_path)
    save_section_watermark(out, 3, _watermark(["gone1", "gone2"]))
    summary = CleanupSummary()
    _repair_watermarks(out, None, summary)
    assert summary.deleted_watermarks == 1
    assert summary.rewritten_watermarks == 0
    assert not section_meta_path(out, 3).is_file()


def test_partial_missing_rewrites_with_only_present_keys(tmp_path: Path) -> None:
    out = _out(tmp_path)
    _make_crop(out, "keep")
    save_section_watermark(out, 3, _watermark(["keep", "gone"]))
    summary = CleanupSummary()
    _repair_watermarks(out, {3}, summary)
    assert summary.rewritten_watermarks == 1
    assert summary.deleted_watermarks == 0
    saved = load_section_watermark(out, 3)
    assert saved is not None
    assert saved.image_keys == ["keep"]
    assert [item.key for item in saved.images or []] == ["keep"]


def test_all_present_is_not_rewritten(tmp_path: Path) -> None:
    out = _out(tmp_path)
    _make_crop(out, "a")
    _make_crop(out, "b")
    save_section_watermark(out, 3, _watermark(["a", "b"]))
    before = section_meta_path(out, 3).read_bytes()
    summary = CleanupSummary()
    _repair_watermarks(out, None, summary)
    assert summary == CleanupSummary()
    assert section_meta_path(out, 3).read_bytes() == before


def test_images_and_keys_are_filtered_independently(tmp_path: Path) -> None:
    out = _out(tmp_path)
    _make_crop(out, "a")
    wm = _watermark(["a"])
    wm.image_keys = ["a", "gone"]  # keys list carries a stale extra entry
    save_section_watermark(out, 3, wm)
    summary = CleanupSummary()
    _repair_watermarks(out, None, summary)
    # to_json writes image_keys as given when non-empty, so the stale key is dropped.
    assert summary.rewritten_watermarks == 1
    saved = load_section_watermark(out, 3)
    assert saved is not None and saved.image_keys == ["a"]


def test_stale_image_entry_alone_triggers_rewrite_not_delete(tmp_path: Path) -> None:
    out = _out(tmp_path)
    _make_crop(out, "a")
    wm = _watermark(["gone"])
    wm.image_keys = ["a"]  # the key survives, the only ImageWatermark does not
    save_section_watermark(out, 3, wm)
    summary = CleanupSummary()
    _repair_watermarks(out, None, summary)
    assert summary.deleted_watermarks == 0
    assert summary.rewritten_watermarks == 1
    saved = load_section_watermark(out, 3)
    assert saved is not None
    assert saved.images == []
    assert saved.image_keys == ["a"]
