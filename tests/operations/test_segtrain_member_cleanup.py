"""Characterization of member-file deletion (``remove_member_files_not_in`` and friends).

The deleted set, return count, files left on disk, and the product index afterwards are
compared against the original rule: every indexed name in a member folder that starts with
``{key}_``, ends with the folder's suffix, and splits back to exactly ``(key, id)``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st

from nornir_buildmanager.operations.segmentationtraining.cleanup import (
    remove_image_key_products,
    remove_member_files_not_in,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    IGNORED_FOLDER,
    MASKS_FOLDER,
    RLE_FOLDER,
    CropProductIndex,
    drop_product_index,
    get_product_index,
    product_folder_path,
    split_member_stem,
)

_SUFFIX = {MASKS_FOLDER: ".png", IGNORED_FOLDER: ".png", RLE_FOLDER: ".json"}
_KEYS = ["K", "K_1", "K_1_2", "a.b", "K_x", "L"]
_TOKENS = ["5", "05", "0", "12", "x", ""]
_EXTS = [".png", ".json", ".jpg"]


def _name(key: str, token: str | None, ext: str) -> str:
    return f"{key}{ext}" if token is None else f"{key}_{token}{ext}"


_names = st.builds(
    _name,
    st.sampled_from(_KEYS),
    st.one_of(st.none(), st.sampled_from(_TOKENS)),
    st.sampled_from(_EXTS),
)
_tree = st.fixed_dictionaries({folder: st.sets(_names, max_size=14) for folder in _SUFFIX})
_keep = st.one_of(st.none(), st.sets(st.sampled_from([0, 1, 5, 12]), max_size=3))


def _expected_deleted(index: CropProductIndex, key: str, keep_ids: set[int] | None) -> set[tuple[str, str]]:
    """The original scan: prefix, suffix, exact key, numeric id, not kept."""
    deleted: set[tuple[str, str]] = set()
    for folder, suffix in _SUFFIX.items():
        for name in list(index.iter_names(folder)):
            if not name.startswith(f"{key}_") or not name.endswith(suffix):
                continue
            member_key, location_id = split_member_stem(Path(name).stem)
            if member_key != key or location_id is None:
                continue
            if keep_ids is not None and location_id in keep_ids:
                continue
            deleted.add((folder, name))
    return deleted


def _write_tree(output: Path, tree: dict[str, set[str]]) -> None:
    for folder, names in tree.items():
        directory = product_folder_path(output, folder)
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / name).write_bytes(b"x")


def _on_disk(output: Path) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for folder in _SUFFIX:
        directory = product_folder_path(output, folder)
        if directory.is_dir():
            found |= {(folder, path.name) for path in directory.iterdir() if path.is_file()}
    return found


def _indexed(index: CropProductIndex) -> set[tuple[str, str]]:
    return {(folder, name) for folder in _SUFFIX for name in index.iter_names(folder)}


def _check(output: Path, key: str, keep_ids: set[int] | None, *, explicit_index: bool) -> None:
    index = get_product_index(output)
    before_disk = _on_disk(output)
    before_index = _indexed(index)
    expected = _expected_deleted(index, key, keep_ids)
    products = index if explicit_index else None
    if keep_ids is None:
        removed = remove_image_key_products(output, key, products=products)
    else:
        removed = remove_member_files_not_in(output, key, keep_ids, products=products)
    assert removed == len(expected)
    assert _on_disk(output) == before_disk - expected
    assert _indexed(index) == before_index - expected
    for folder in _SUFFIX:
        assert not {(folder, name) for _id, name in index.members(folder, key)} & expected


@settings(max_examples=150, deadline=None)
@given(tree=_tree, key=st.sampled_from(_KEYS), keep_ids=_keep, explicit_index=st.booleans())
@example(
    tree={MASKS_FOLDER: {"K_5.png", "K_05.png", "K_5.json", "K_1_5.png", "K_x.png"}, IGNORED_FOLDER: set(), RLE_FOLDER: {"K_5.json", "K_5.png"}},
    key="K",
    keep_ids=set(),
    explicit_index=True,
)
@example(
    tree={MASKS_FOLDER: {"K_1_2.png", "K_1_12.png", "K_12.png"}, IGNORED_FOLDER: {"K_1_5.png"}, RLE_FOLDER: {"K_1_5.json"}},
    key="K_1",
    keep_ids={12},
    explicit_index=False,
)
def test_member_deletion_matches_original_scan(
    tree: dict[str, set[str]], key: str, keep_ids: set[int] | None, explicit_index: bool
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "out"
        output.mkdir()
        _write_tree(output, tree)
        drop_product_index(output)
        try:
            _check(output, key, keep_ids, explicit_index=explicit_index)
        finally:
            drop_product_index(output)


@settings(max_examples=100, deadline=None)
@given(
    tree=_tree,
    added=st.lists(st.tuples(st.sampled_from(sorted(_SUFFIX)), _names), max_size=6),
    forgotten=st.lists(st.tuples(st.sampled_from(sorted(_SUFFIX)), _names), max_size=6),
    rescan=st.one_of(st.none(), st.sampled_from(sorted(_SUFFIX))),
    key=st.sampled_from(_KEYS),
    keep_ids=_keep,
)
def test_member_deletion_after_index_updates(
    tree: dict[str, set[str]],
    added: list[tuple[str, str]],
    forgotten: list[tuple[str, str]],
    rescan: str | None,
    key: str,
    keep_ids: set[int] | None,
) -> None:
    """Write-through note/forget/rescan between the build and the delete keep the same result."""
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "out"
        output.mkdir()
        _write_tree(output, tree)
        drop_product_index(output)
        try:
            index = get_product_index(output)
            for folder, name in added:
                (product_folder_path(output, folder) / name).write_bytes(b"y")
                index.note(folder, name)
            for folder, name in forgotten:
                (product_folder_path(output, folder) / name).unlink(missing_ok=True)
                index.forget(folder, name)
            if rescan is not None:
                index.rescan(rescan)
            _check(output, key, keep_ids, explicit_index=True)
        finally:
            drop_product_index(output)


def test_forgotten_member_is_not_counted_again() -> None:
    """A name forgotten after the build (write-through unlink) is no longer a member of its key."""
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "out"
        output.mkdir()
        _write_tree(output, {MASKS_FOLDER: {"K_5.png", "K_6.png"}, IGNORED_FOLDER: set(), RLE_FOLDER: set()})
        drop_product_index(output)
        try:
            index = get_product_index(output)
            (product_folder_path(output, MASKS_FOLDER) / "K_5.png").unlink()
            index.forget(MASKS_FOLDER, "K_5.png")
            assert remove_member_files_not_in(output, "K", set()) == 1
            assert _on_disk(output) == set()
        finally:
            drop_product_index(output)


def test_repeated_calls_across_keys_delete_each_key_once() -> None:
    """A cleanup-style loop over every key removes exactly the unkept members of each."""
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "out"
        output.mkdir()
        keys = [f"V_{z}_D1_X0-1_Y{y}-{y + 1}" for z in (3, 30) for y in range(4)]
        tree = {
            folder: {f"{key}_{i}{suffix}" for key in keys for i in range(4)}
            for folder, suffix in _SUFFIX.items()
        }
        _write_tree(output, tree)
        drop_product_index(output)
        try:
            for key in keys:
                assert remove_member_files_not_in(output, key, {1, 3}) == 6
            expected = {
                (folder, f"{key}_{i}{suffix}")
                for folder, suffix in _SUFFIX.items()
                for key in keys
                for i in (1, 3)
            }
            assert _on_disk(output) == expected
            assert _indexed(get_product_index(output)) == expected
        finally:
            drop_product_index(output)
