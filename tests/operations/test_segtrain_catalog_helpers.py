"""Characterization tests for the small pure and near-pure helpers in ``segmentationtraining.catalog``."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from nornir_buildmanager.operations.segmentationtraining.catalog import (
    _image_key_matches_z,
    _image_relpath,
    _iter_image_json_paths,
    _location_id_from_mask_name,
    _mask_relpath,
    _row_z,
    record_crop_size,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    IGNORED_FOLDER,
    MASKS_FOLDER,
    drop_product_index,
    get_product_index,
)
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord

_STAMP = datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)


def _record(z: int) -> LocationRecord:
    return LocationRecord(
        id=11,
        z=z,
        wkt="POLYGON((0 0,1 0,1 1,0 0))",
        parent_id=None,
        off_edge=False,
        last_modified=_STAMP,
        type_id=None,
        type_name=None,
        structure_label=None,
    )


def _touch(output: Path, folder: str, names: list[str]) -> None:
    target = output / folder
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        (target / name).write_bytes(b"x")


@pytest.fixture
def output(tmp_path: Path):
    path = tmp_path / "AnnotationCrops"
    path.mkdir()
    drop_product_index(path)
    yield path
    drop_product_index(path)


def test_image_key_matches_z_requires_delimited_section_and_dataset_marker() -> None:
    assert _image_key_matches_z("RC2_17_D1_X0_Y0", 17)
    assert not _image_key_matches_z("RC2_117_D1_X0_Y0", 17)
    assert not _image_key_matches_z("RC2_17_X0", 17)
    assert not _image_key_matches_z("RC2_17_D1", 18)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("RC2_17_D1_X0_Y0_5.png", 5),
        ("k_007.png", 7),
        ("k_x.png", None),
        ("k_5.jpg", None),
        ("nounderscore5.png", None),
    ],
)
def test_location_id_from_mask_name(name: str, expected: int | None) -> None:
    assert _location_id_from_mask_name(Path(name)) == expected


def test_row_z_prefers_record_then_annotation_then_image_key_then_zero() -> None:
    assert _row_z(_record(9), "RC2_17_D1", {"z": 3}) == 9
    assert _row_z(None, "RC2_17_D1", {"z": "3"}) == 3
    assert _row_z(None, "RC2_17_D1_X0", {}) == 17
    assert _row_z(None, "D1_X0", {}) == 0  # D token at index 0 has no preceding section
    assert _row_z(None, "RC2_abc_D1", {}) == 0
    assert _row_z(None, "D1_5", {}) == 0  # must not wrap around to the last token
    assert _row_z(None, "nothing", {}) == 0


def test_mask_relpath_exact_name_wins(output: Path) -> None:
    _touch(output, "masks", ["k_5.png", "other_5.png"])
    assert _mask_relpath(output, "k", 5, ignored=False) == "masks/k_5.png"


def test_mask_relpath_single_fragment_fallback(output: Path) -> None:
    _touch(output, "masks", ["frag_5.png"])
    assert _mask_relpath(output, "k", 5, ignored=False) == "masks/frag_5.png"


def test_mask_relpath_ambiguous_or_missing_keeps_expected_name(output: Path) -> None:
    _touch(output, "masks", ["a_5.png", "b_5.png"])
    assert _mask_relpath(output, "k", 5, ignored=False) == "masks/k_5.png"
    assert _mask_relpath(output, "k", 6, ignored=False) == "masks/k_6.png"


def test_mask_relpath_ignored_folder_is_separate(output: Path) -> None:
    _touch(output, "masks", ["k_5.png"])
    _touch(output, "ignored", ["frag_5.png"])
    assert _mask_relpath(output, "k", 5, ignored=True) == "ignored/frag_5.png"
    assert _mask_relpath(output, "k", 5, ignored=False) == "masks/k_5.png"


def _relpath_by_scan(names: set[str], folder: str, image_key: str, location_id: int) -> str:
    """Reference rule: exact name, else the one name ending ``_{id}.png``, else the exact name."""
    exact = f"{image_key}_{location_id}.png"
    if exact in names:
        return f"{folder}/{exact}"
    found = sorted(name for name in names if name.endswith(f"_{int(location_id)}.png"))
    return f"{folder}/{found[0]}" if len(found) == 1 else f"{folder}/{exact}"


_RELPATH_TOKENS = ["5", "15", "-5", "05", "x", "3_5", "0"]
_relpath_names = st.builds(
    "{}_{}{}".format,
    st.sampled_from(["k", "a", "k_1", "frag", ""]),
    st.sampled_from(_RELPATH_TOKENS),
    st.sampled_from([".png", ".png", ".jpg"]),
)
_relpath_lookups = st.lists(
    st.tuples(st.sampled_from(["k", "a", "z"]), st.sampled_from([0, 5, 15, -5, 3]), st.booleans()),
    min_size=1,
    max_size=12,
)


@settings(max_examples=150, deadline=None)
@given(
    masks=st.sets(_relpath_names, max_size=12),
    ignored=st.sets(_relpath_names, max_size=6),
    lookups=_relpath_lookups,
)
@example(
    masks={"frag_5.png", "k_15.png", "k_-5.png", "k_05.png"},
    ignored={"a_5.png"},
    lookups=[("k", 5, False), ("k", -5, False), ("z", 15, False), ("k", 5, True), ("a", 0, False)],
)
def test_mask_relpath_shared_snapshot_matches_suffix_scan(
    masks: set[str], ignored: set[str], lookups: list[tuple[str, int, bool]]
) -> None:
    """A *mask_names* cache shared across lookups returns what a fresh per-call suffix scan would."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        drop_product_index(out)
        products = get_product_index(out)
        for name in masks:
            products.note(MASKS_FOLDER, name, 0.0)
        for name in ignored:
            products.note(IGNORED_FOLDER, name, 0.0)
        shared: dict[str, dict[str, list[str]]] = {}
        for image_key, location_id, is_ignored in lookups:
            folder, names = (IGNORED_FOLDER, ignored) if is_ignored else (MASKS_FOLDER, masks)
            expected = _relpath_by_scan(names, folder, image_key, location_id)
            assert _mask_relpath(out, image_key, location_id, ignored=is_ignored, mask_names=shared) == expected
            assert _mask_relpath(out, image_key, location_id, ignored=is_ignored) == expected
        drop_product_index(out)


def test_image_relpath_png_then_jpeg_then_default_png_name(output: Path) -> None:
    assert _image_relpath(output, "k") == "images/k.png"
    _touch(output, "images", ["k.jpg"])
    assert _image_relpath(output, "k") == "images/k.jpg"
    _touch(output, "images", ["k.png"])
    assert _image_relpath(output, "k") == "images/k.png"


def test_iter_image_json_paths_filters_by_z_and_ignores_non_json(output: Path) -> None:
    _touch(output, "images", ["RC2_17_D1_a.json", "RC2_18_D1_b.json", "RC2_17_D1_a.png"])
    products = get_product_index(output)
    images = output / "images"
    assert set(_iter_image_json_paths(output, products, z=None, image_keys=None)) == {
        images / "RC2_17_D1_a.json",
        images / "RC2_18_D1_b.json",
    }
    assert list(_iter_image_json_paths(output, products, z=18, image_keys=None)) == [images / "RC2_18_D1_b.json"]


def test_iter_image_json_paths_explicit_keys_skip_unindexed_and_ignore_z(output: Path) -> None:
    _touch(output, "images", ["RC2_17_D1_a.json"])
    products = get_product_index(output)
    got = list(_iter_image_json_paths(output, products, z=99, image_keys={"RC2_17_D1_a", "missing"}))
    assert got == [output / "images" / "RC2_17_D1_a.json"]


def _stored_crop_size(out: Path) -> int:
    """Read the recorded crop size straight from the catalog (the gallery is the production reader)."""
    connection = sqlite3.connect(str(out / "annotation_crops.sqlite"))
    try:
        return connection.execute("SELECT crop_size FROM crop_geometry WHERE id = 1").fetchone()[0]
    finally:
        connection.close()


def test_crop_size_round_trip(tmp_path: Path) -> None:
    out = tmp_path / "vol"
    record_crop_size(out, 512)
    assert _stored_crop_size(out) == 512
    record_crop_size(out, 1024)
    assert _stored_crop_size(out) == 1024
