"""Characterization of ``catalog.apply_ignore_moves``.

The files moved into ``ignored/``, their order, the return count, the files left on disk,
and the product index afterwards are compared against the original rule: for each ignored
id (in ``load_ignore_ids`` order), every indexed name in ``masks/`` that ends with
``_{id}.png``, in sorted order.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st

from nornir_buildmanager.operations.segmentationtraining import catalog
from nornir_buildmanager.operations.segmentationtraining.catalog import (
    apply_ignore_moves,
    load_ignore_ids,
    save_ignore_ids,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    IGNORED_FOLDER,
    MASKS_FOLDER,
    CropProductIndex,
    drop_product_index,
    get_product_index,
)

_KEYS = ["K", "K_1", "a.b", "a_3", "", "K_x", "5"]
_TOKENS = ["5", "05", "0", "15", "-5", "x", "", "3_5"]
# No case-only variants: Windows file systems fold case.
_EXTS = [".png", ".json", ".png.bak", ".jpg"]
_IDS = [0, 3, 5, 15, -5, 12]


def _name(key: str, token: str | None, ext: str) -> str:
    return f"{key}{ext}" if token is None else f"{key}_{token}{ext}"


_names = st.builds(
    _name,
    st.sampled_from(_KEYS),
    st.one_of(st.none(), st.sampled_from(_TOKENS)),
    st.sampled_from(_EXTS),
).filter(lambda name: name not in ("", "."))
_masks = st.sets(_names, max_size=16)
_preexisting_ignored = st.sets(_names, max_size=6)
_ignore_ids = st.sets(st.sampled_from(_IDS), max_size=4)


def _expected_moves(names: set[str], ids: set[int]) -> list[str]:
    """The original per-id scan: names ending ``_{id}.png``, sorted, per id in set order."""
    moves: list[str] = []
    for location_id in ids:
        suffix = f"_{int(location_id)}.png"
        moves.extend(sorted(name for name in names if name.endswith(suffix)))
    return moves


def _write(directory: Path, names: set[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(directory.name.encode())


def _listing(directory: Path) -> dict[str, bytes]:
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}


@settings(max_examples=150, deadline=None)
@given(masks=_masks, ignored=_preexisting_ignored, ids=_ignore_ids)
@example(masks={"k_05.png", "k_5.png", "a_3_5.png", "k_15.png", "k_5.json"}, ignored={"k_5.png"}, ids={5})
@example(masks={"k_-5.png", "k_5.png", "_5.png"}, ignored=set(), ids={-5, 5})
@example(masks={"k_5.png"}, ignored=set(), ids=set())
def test_apply_ignore_moves_matches_per_id_scan(masks: set[str], ignored: set[str], ids: set[int]) -> None:
    """Same files moved in the same order, same count, same disk and index state."""
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "out"
        masks_dir = output / MASKS_FOLDER
        ignored_dir = output / IGNORED_FOLDER
        _write(masks_dir, masks)
        if ignored:
            _write(ignored_dir, ignored)
        save_ignore_ids(output, ids)
        drop_product_index(output)

        expected = _expected_moves(masks, load_ignore_ids(output))
        expected_masks = {name: b"masks" for name in masks if name not in expected}
        expected_ignored = {name: b"ignored" for name in ignored}
        expected_ignored.update({name: b"masks" for name in expected})

        recorded: list[str] = []
        original = catalog._relocate_indexed

        def _recording(
            products: CropProductIndex, source: Path, destination: Path, source_folder: str, dest_folder: str
        ) -> None:
            recorded.append(source.name)
            assert source.parent == masks_dir
            assert destination == ignored_dir / source.name
            original(products, source, destination, source_folder, dest_folder)

        catalog._relocate_indexed = _recording
        try:
            moved = apply_ignore_moves(output)
        finally:
            catalog._relocate_indexed = original

        assert recorded == expected
        assert moved == len(expected)
        assert _listing(masks_dir) == expected_masks
        assert _listing(ignored_dir) == expected_ignored
        products = get_product_index(output)
        assert set(products.iter_names(MASKS_FOLDER)) == set(expected_masks)
        assert set(products.iter_names(IGNORED_FOLDER)) == set(expected_ignored)
        # Steady state: everything already moved, nothing more to do.
        assert apply_ignore_moves(output) == 0
        drop_product_index(output)


@settings(max_examples=150, deadline=None)
@given(masks=_masks, location_id=st.sampled_from(_IDS))
@example(masks={"k_05.png", "k_5.png", "a_3_5.png", "_5.png", "k_5.json", "k_-5.png"}, location_id=5)
def test_mask_names_by_id_token_matches_suffix_scan(masks: set[str], location_id: int) -> None:
    """The one-pass grouping names the same files, sorted, as a per-id ``_{id}.png`` suffix scan."""
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "out"
        drop_product_index(output)
        products = get_product_index(output)
        for name in masks:
            products.note(MASKS_FOLDER, name, 0.0)
        grouped = catalog._mask_names_by_id_token(output, MASKS_FOLDER)
        expected = _expected_moves(set(products.iter_names(MASKS_FOLDER)), {location_id})
        assert sorted(grouped.get(str(int(location_id)), [])) == expected
        drop_product_index(output)
