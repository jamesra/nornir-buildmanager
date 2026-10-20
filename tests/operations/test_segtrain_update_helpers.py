"""Characterization of the small ID-set helpers in segmentationtraining.update."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nornir_buildmanager.operations.segmentationtraining import update
from nornir_buildmanager.operations.segmentationtraining.catalog import connect
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ImageWatermark,
    SectionWatermark,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    IGNORED_FOLDER,
    IMAGES_FOLDER,
    MASKS_FOLDER,
    RLE_FOLDER,
    drop_product_index,
    get_product_index,
    product_folder_path,
)
from nornir_buildmanager.operations.segmentationtraining.records import (
    LocationRecord,
    LocationType,
)


def _mark(key: str, members: list[int]) -> ImageWatermark:
    """Crop watermark at the origin listing *members*."""
    return ImageWatermark(
        key=key, downsample=1, ix0=0, ix1=1, iy0=0, iy1=1, member_ids=members,
        max_last_modified="", origin_x=0, origin_y=0, width=64, height=64,
    )


def _watermark(
    image_keys: list[str] | None = None, images: list[ImageWatermark] | None = None
) -> SectionWatermark:
    """Section watermark with only the key lists populated."""
    return SectionWatermark(
        ids=[], max_last_modified="", tileset_mtime=0.0, params_hash="", downsample=1,
        image_keys=image_keys, images=images,
    )


def _record(loc_id: int, modified: datetime) -> LocationRecord:
    """Polygon location whose only interesting field is *modified*."""
    wkt = "POLYGON((0 0,4 0,4 4,0 4,0 0))"
    return LocationRecord(
        id=loc_id, z=1, wkt=wkt, parent_id=None, off_edge=False, last_modified=modified,
        type_id=1, type_name="Cell", structure_label=None, type_code=LocationType.POLYGON,
    )


def _write_crop_json(output: Path, key: str, annotations: list[dict]) -> None:
    """Write ``images/<key>.json`` with *annotations*."""
    folder = output / IMAGES_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{key}.json").write_text(json.dumps({"annotations": annotations}), encoding="utf-8")


T0 = datetime(2020, 1, 1, tzinfo=UTC)
T1 = datetime(2021, 6, 1, tzinfo=UTC)


# --- _section_image_keys ----------------------------------------------------------------------


def test_section_image_keys_none_is_empty() -> None:
    assert update._section_image_keys(None) == []


def test_section_image_keys_merges_without_duplicates_in_order() -> None:
    previous = _watermark(image_keys=["a", "b"], images=[_mark("b", [1]), _mark("c", [2])])
    assert update._section_image_keys(previous) == ["a", "b", "c"]


def test_section_image_keys_handles_missing_lists() -> None:
    assert update._section_image_keys(_watermark()) == []


# --- _stored_last_modified --------------------------------------------------------------------


def test_stored_last_modified_reads_ids_and_skips_missing_files_and_idless_items(tmp_path: Path) -> None:
    _write_crop_json(
        tmp_path,
        "k1",
        [
            {"id": 5, "last_modified": "2020-01-01T00:00:00Z"},
            {"last_modified": "2021-06-01T00:00:00Z"},  # no id: skipped
            {"id": "7", "last_modified": "2021-06-01T00:00:00Z"},  # id coerced to int
        ],
    )
    found = update._stored_last_modified(tmp_path, ["k1", "absent"])
    assert found == {5: T0, 7: T1}


def test_stored_last_modified_missing_timestamp_parses_to_minimum(tmp_path: Path) -> None:
    _write_crop_json(tmp_path, "k1", [{"id": 3}])
    found = update._stored_last_modified(tmp_path, ["k1"])
    assert found[3] == datetime.min.replace(tzinfo=UTC)


def test_stored_last_modified_later_key_overrides_earlier(tmp_path: Path) -> None:
    _write_crop_json(tmp_path, "k1", [{"id": 5, "last_modified": "2020-01-01T00:00:00Z"}])
    _write_crop_json(tmp_path, "k2", [{"id": 5, "last_modified": "2021-06-01T00:00:00Z"}])
    assert update._stored_last_modified(tmp_path, ["k1", "k2"]) == {5: T1}


# --- _changed_survivor_ids --------------------------------------------------------------------


def test_changed_survivor_ids_early_returns(tmp_path: Path) -> None:
    records = [_record(1, T1)]
    previous = _watermark(image_keys=["k1"])
    assert update._changed_survivor_ids(tmp_path, records, set(), previous) == []
    assert update._changed_survivor_ids(tmp_path, records, {1}, None) == []
    assert update._changed_survivor_ids(tmp_path, records, {1}, _watermark()) == []


def test_changed_survivor_ids_unchanged_changed_and_missing_prior(tmp_path: Path) -> None:
    _write_crop_json(
        tmp_path,
        "k1",
        [
            {"id": 1, "last_modified": "2020-01-01T00:00:00Z"},  # unchanged
            {"id": 2, "last_modified": "2020-01-01T00:00:00Z"},  # record is newer
        ],
    )
    records = [_record(1, T0), _record(2, T1), _record(3, T0), _record(4, T1)]
    previous = _watermark(images=[_mark("k1", [1, 2, 3])])
    # id 3 has no stored annotation (missing prior); id 4 is not a survivor.
    assert update._changed_survivor_ids(tmp_path, records, {1, 2, 3}, previous) == [2, 3]


# --- _watermark_keys_for_ids ------------------------------------------------------------------


def test_watermark_keys_for_ids() -> None:
    assert update._watermark_keys_for_ids(None, {1}) == {}
    previous = _watermark(images=[_mark("a", [1, 2]), _mark("b", [2, 3]), _mark("c", [9])])
    assert update._watermark_keys_for_ids(previous, {2, 3, 4}) == {2: {"a", "b"}, 3: {"b"}}
    assert update._watermark_keys_for_ids(_watermark(), {1}) == {}


# --- _catalog_mask_rows -----------------------------------------------------------------------


def test_catalog_mask_rows_without_database_is_empty(tmp_path: Path) -> None:
    assert update._catalog_mask_rows(tmp_path, 1, 5) == []


def test_catalog_mask_rows_filters_by_section_and_location(tmp_path: Path) -> None:
    connection = connect(tmp_path)
    try:
        connection.executemany(
            "INSERT INTO locations (location_id, z, image_key, mask_relpath) VALUES (?, ?, ?, ?)",
            [
                (5, 1, "k1", "masks/k1_5.png"),
                (5, 1, "k2", None),  # NULL relpath becomes ""
                (5, 2, "k3", "masks/k3_5.png"),  # other section
                (6, 1, "k1", "masks/k1_6.png"),  # other location
            ],
        )
        connection.commit()
    finally:
        connection.close()
    rows = update._catalog_mask_rows(tmp_path, 1, 5)
    assert sorted(rows) == [("k1", "masks/k1_5.png"), ("k2", "")]


# --- _unlink_computed_member / _prune_removed -------------------------------------------------


@pytest.fixture
def products(tmp_path: Path):
    """Output folder whose cached product index is dropped before and after the test."""
    drop_product_index(tmp_path)
    yield tmp_path
    drop_product_index(tmp_path)


def _touch_member_files(output: Path, key: str, location_id: int) -> list[Path]:
    """Create the mask, ignored mask, and RLE files for one member."""
    paths = [
        product_folder_path(output, MASKS_FOLDER) / f"{key}_{location_id}.png",
        product_folder_path(output, IGNORED_FOLDER) / f"{key}_{location_id}.png",
        product_folder_path(output, RLE_FOLDER) / f"{key}_{location_id}.json",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return paths


def test_unlink_computed_member_deletes_files_and_forgets_index_entries(products: Path) -> None:
    output = products
    victim = _touch_member_files(output, "k1", 5)
    other = _touch_member_files(output, "k1", 6)
    index = get_product_index(output)
    assert index.exists(MASKS_FOLDER, "k1_5.png")

    update._unlink_computed_member(output, "k1", 5, index)

    assert not any(path.exists() for path in victim)
    assert all(path.exists() for path in other)
    assert not index.exists(MASKS_FOLDER, "k1_5.png")
    assert not index.exists(IGNORED_FOLDER, "k1_5.png")
    assert not index.exists(RLE_FOLDER, "k1_5.json")
    assert index.exists(MASKS_FOLDER, "k1_6.png")


def test_unlink_computed_member_tolerates_missing_files(products: Path) -> None:
    update._unlink_computed_member(products, "k1", 5, get_product_index(products))


def test_prune_removed_nothing_removed_touches_nothing(products: Path) -> None:
    assert update._prune_removed(products, 1, set(), get_product_index(products), None) == set()


def test_prune_removed_uses_watermark_and_rewrites_json(products: Path) -> None:
    output = products
    paths = _touch_member_files(output, "k1", 5)
    _touch_member_files(output, "k1", 6)
    _write_crop_json(output, "k1", [{"id": 5}, {"id": 6}])
    previous = _watermark(images=[_mark("k1", [5, 6])])

    touched = update._prune_removed(output, 1, {5}, get_product_index(output), previous)

    assert touched == {"k1"}
    assert not any(path.exists() for path in paths)
    payload = json.loads((output / IMAGES_FOLDER / "k1.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in payload["annotations"]] == [6]


def test_prune_removed_falls_back_to_catalog_for_unlisted_ids(products: Path) -> None:
    output = products
    paths = _touch_member_files(output, "k9", 5)
    _write_crop_json(output, "k9", [{"id": 5}])
    connection = connect(output)
    try:
        connection.execute(
            "INSERT INTO locations (location_id, z, image_key, mask_relpath) VALUES (5, 1, 'k9', 'masks/k9_5.png')"
        )
        connection.commit()
    finally:
        connection.close()
    previous = _watermark(images=[_mark("k1", [1])])  # does not list id 5

    touched = update._prune_removed(output, 1, {5}, get_product_index(output), previous)

    assert touched == {"k9"}
    assert not any(path.exists() for path in paths)
    # The only annotation was removed, so the crop JSON is deleted with the crop.
    assert not (output / IMAGES_FOLDER / "k9.json").exists()


def test_prune_removed_id_known_nowhere_is_a_noop(products: Path) -> None:
    previous = _watermark(images=[_mark("k1", [1])])
    assert update._prune_removed(products, 1, {42}, get_product_index(products), previous) == set()


def test_changed_survivor_ids_uses_image_keys_when_no_image_marks(tmp_path: Path) -> None:
    _write_crop_json(tmp_path, "k1", [{"id": 1, "last_modified": "2020-01-01T00:00:00Z"}])
    previous = _watermark(image_keys=["k1"])
    assert update._changed_survivor_ids(tmp_path, [_record(1, T1)], {1}, previous) == [1]
