"""Characterization of segmentationtraining.update._rebuild_watermarks.

The function is only reached through ``update_section_crops``; these tests pin its
removal, drop, timestamp and merge rules directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from nornir_buildmanager.operations.segmentationtraining import update
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ImageWatermark,
    SectionWatermark,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    IMAGES_FOLDER,
)
from nornir_buildmanager.operations.segmentationtraining.records import (
    LocationRecord,
    LocationType,
)

T0 = datetime(2020, 1, 1, tzinfo=UTC)
T1 = datetime(2021, 6, 1, tzinfo=UTC)
T2 = datetime(2022, 3, 1, tzinfo=UTC)


def _record(loc_id: int, modified: datetime) -> LocationRecord:
    """Polygon location whose only interesting field is *modified*."""
    return LocationRecord(
        id=loc_id, z=1, wkt="POLYGON((0 0,4 0,4 4,0 4,0 0))", parent_id=None, off_edge=False,
        last_modified=modified, type_id=1, type_name="Cell", structure_label=None,
        type_code=LocationType.POLYGON,
    )


def _mark(key: str, members: list[int], stamp: str = "stored") -> ImageWatermark:
    """Crop watermark with distinctive geometry so copies are detectable."""
    return ImageWatermark(
        key=key, downsample=4, ix0=1, ix1=2, iy0=3, iy1=4, member_ids=members,
        max_last_modified=stamp, origin_x=10, origin_y=20, width=64, height=48,
    )


def _section(images: list[ImageWatermark] | None) -> SectionWatermark:
    return SectionWatermark(
        ids=[], max_last_modified="", tileset_mtime=0.0, params_hash="", downsample=1,
        image_keys=None, images=images,
    )


def _plan(key: str, ids: list[int]) -> SimpleNamespace:
    """Duck-typed PlannedCrop: ``image_watermark`` reads snap/window/downsample too."""
    return SimpleNamespace(
        image_key=key, location_ids=ids, downsample=2,
        snap=SimpleNamespace(ix0=5, ix1=6, iy0=7, iy1=8),
        window=SimpleNamespace(origin_x=1, origin_y=2, width=30, height=40),
    )


def _png(output: Path, key: str) -> None:
    folder = output / IMAGES_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{key}.png").write_bytes(b"x")


def _json(output: Path, key: str) -> None:
    folder = output / IMAGES_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{key}.json").write_text("{}", encoding="utf-8")


def _by_key(marks: list[ImageWatermark]) -> dict[str, ImageWatermark]:
    return {m.key: m for m in marks}


def test_no_previous_plans_with_png_or_json_get_fresh_watermarks(tmp_path: Path) -> None:
    _png(tmp_path, "a")
    _json(tmp_path, "b")
    by_id = {1: _record(1, T0), 2: _record(2, T1)}
    plans = [_plan("a", [1, 2]), _plan("b", [2]), _plan("c", [1])]
    marks = update._rebuild_watermarks(tmp_path, None, by_id, plans, set())
    got = _by_key(marks)
    assert [m.key for m in marks] == ["a", "b"]
    assert got["a"].member_ids == [1, 2]
    assert got["a"].max_last_modified == T1.isoformat()
    assert (got["a"].downsample, got["a"].ix0, got["a"].iy1) == (2, 5, 8)
    assert (got["a"].origin_x, got["a"].width, got["a"].height) == (1, 30, 40)
    assert got["b"].max_last_modified == T1.isoformat()


def test_previous_drops_removed_members_and_empty_items(tmp_path: Path) -> None:
    _png(tmp_path, "a")
    _png(tmp_path, "b")
    prev = _section([_mark("a", [1, 2, 3]), _mark("b", [4])])
    by_id = {1: _record(1, T0), 3: _record(3, T2)}
    marks = update._rebuild_watermarks(tmp_path, prev, by_id, [], {2, 4})
    assert [m.key for m in marks] == ["a"]
    assert marks[0].member_ids == [1, 3]
    assert marks[0].max_last_modified == T2.isoformat()


def test_previous_item_without_crop_image_is_dropped(tmp_path: Path) -> None:
    _png(tmp_path, "a")
    prev = _section([_mark("a", [1]), _mark("gone", [1])])
    marks = update._rebuild_watermarks(tmp_path, prev, {1: _record(1, T0)}, [], set())
    assert [m.key for m in marks] == ["a"]


def test_previous_json_alone_does_not_keep_an_item(tmp_path: Path) -> None:
    _json(tmp_path, "a")
    prev = _section([_mark("a", [1])])
    assert update._rebuild_watermarks(tmp_path, prev, {1: _record(1, T0)}, [], set()) == []


def test_previous_keeps_geometry_and_stored_stamp_when_no_member_resolves(tmp_path: Path) -> None:
    _png(tmp_path, "a")
    prev = _section([_mark("a", [7, 8], stamp="old-stamp")])
    (mark,) = update._rebuild_watermarks(tmp_path, prev, {}, [], set())
    assert mark.max_last_modified == "old-stamp"
    assert mark.member_ids == [7, 8]
    assert (mark.downsample, mark.ix0, mark.ix1, mark.iy0, mark.iy1) == (4, 1, 2, 3, 4)
    assert (mark.origin_x, mark.origin_y, mark.width, mark.height) == (10, 20, 64, 48)


def test_previous_stamp_is_max_of_resolved_members_only(tmp_path: Path) -> None:
    _png(tmp_path, "a")
    prev = _section([_mark("a", [1, 2, 99], stamp="old-stamp")])
    by_id = {1: _record(1, T0), 2: _record(2, T1)}
    (mark,) = update._rebuild_watermarks(tmp_path, prev, by_id, [], set())
    assert mark.max_last_modified == T1.isoformat()
    assert mark.member_ids == [1, 2, 99]


def test_plan_on_kept_key_appends_only_new_ids_in_order_and_keeps_geometry(tmp_path: Path) -> None:
    _png(tmp_path, "a")
    prev = _section([_mark("a", [1, 2])])
    by_id = {i: _record(i, T0) for i in (1, 2, 3, 4)}
    by_id[4] = _record(4, T2)
    plans = [_plan("a", [4, 2, 3, 1])]
    (mark,) = update._rebuild_watermarks(tmp_path, prev, by_id, plans, set())
    assert mark.member_ids == [1, 2, 4, 3]
    assert mark.max_last_modified == T2.isoformat()
    # geometry comes from the prior item, not from the plan
    assert (mark.downsample, mark.ix0, mark.ix1, mark.iy0, mark.iy1) == (4, 1, 2, 3, 4)
    assert (mark.origin_x, mark.origin_y, mark.width, mark.height) == (10, 20, 64, 48)


def test_plan_ids_beyond_prior_members_are_appended_after_removal(tmp_path: Path) -> None:
    _png(tmp_path, "a")
    prev = _section([_mark("a", [1, 2])])
    by_id = {1: _record(1, T0), 3: _record(3, T1)}
    (mark,) = update._rebuild_watermarks(tmp_path, prev, by_id, [_plan("a", [1, 3])], {2})
    assert mark.member_ids == [1, 3]


def test_plan_without_image_or_json_is_skipped_even_with_prior(tmp_path: Path) -> None:
    # the prior item is dropped (no crop image) and the plan is skipped too
    prev = _section([_mark("a", [1])])
    by_id = {1: _record(1, T0), 2: _record(2, T0)}
    assert update._rebuild_watermarks(tmp_path, prev, by_id, [_plan("a", [1, 2])], set()) == []


def test_plan_with_image_replaces_prior_of_other_key_untouched(tmp_path: Path) -> None:
    _png(tmp_path, "a")
    _png(tmp_path, "b")
    prev = _section([_mark("a", [1])])
    by_id = {1: _record(1, T0), 2: _record(2, T1)}
    marks = update._rebuild_watermarks(tmp_path, prev, by_id, [_plan("b", [2])], set())
    got = _by_key(marks)
    assert list(got) == ["a", "b"]
    assert got["a"].member_ids == [1]
    assert got["b"].member_ids == [2]
    assert got["b"].origin_x == 1  # fresh from the plan


def test_previous_images_none_is_treated_as_empty(tmp_path: Path) -> None:
    _png(tmp_path, "a")
    marks = update._rebuild_watermarks(
        tmp_path, _section(None), {1: _record(1, T0)}, [_plan("a", [1])], set()
    )
    assert [m.key for m in marks] == ["a"]


def test_plan_merge_stamp_falls_back_to_prior_when_no_member_resolves(tmp_path: Path) -> None:
    _png(tmp_path, "a")
    prev = _section([_mark("a", [1], stamp="old-stamp")])
    (mark,) = update._rebuild_watermarks(tmp_path, prev, {}, [_plan("a", [1, 5])], set())
    assert mark.member_ids == [1, 5]
    assert mark.max_last_modified == "old-stamp"


def test_plan_merge_stamp_covers_prior_members_not_just_new_ones(tmp_path: Path) -> None:
    _png(tmp_path, "a")
    prev = _section([_mark("a", [1])])
    by_id = {1: _record(1, T2), 2: _record(2, T0)}
    (mark,) = update._rebuild_watermarks(tmp_path, prev, by_id, [_plan("a", [2])], set())
    assert mark.member_ids == [1, 2]
    assert mark.max_last_modified == T2.isoformat()
