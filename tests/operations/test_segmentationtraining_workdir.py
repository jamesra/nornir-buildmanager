"""The re-run cache lives beside ``-Output`` so the exported folder holds only training data."""

from __future__ import annotations

from pathlib import Path

from nornir_buildmanager.operations.segmentationtraining.ingest import work_dir
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    RLE_FOLDER,
    drop_product_index,
    get_product_index,
)
from nornir_buildmanager.operations.segmentationtraining.workdir import (
    WORK_CACHE_FOLDER,
    rle_dir,
    work_root,
)


def test_work_root_is_a_sibling_cache_folder_named_for_the_volume(tmp_path: Path) -> None:
    output = tmp_path / "SEM" / "v1" / "CPED"
    assert work_root(output) == (tmp_path / "SEM" / "v1" / WORK_CACHE_FOLDER / "CPED").resolve()
    assert WORK_CACHE_FOLDER.startswith("_")


def test_work_root_is_outside_the_output_folder(tmp_path: Path) -> None:
    output = tmp_path / "v1" / "CPED"
    assert output.resolve() not in work_root(output).parents


def test_work_root_is_distinct_per_volume(tmp_path: Path) -> None:
    assert work_root(tmp_path / "v1" / "CPED") != work_root(tmp_path / "v1" / "NasalMonkey")


def test_work_dir_creates_the_work_folder_and_not_one_in_the_output(tmp_path: Path) -> None:
    output = tmp_path / "v1" / "CPED"
    output.mkdir(parents=True)
    created = work_dir(output)
    assert created.is_dir() and created == work_root(output)
    assert not (output / "_work").exists()
    assert {path.name for path in output.parent.iterdir()} == {WORK_CACHE_FOLDER, "CPED"}


def test_product_index_finds_rle_sidecars_in_the_work_folder(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    rle_dir(output).mkdir(parents=True)
    sidecar = rle_dir(output) / "Vol_1_D1_X0_Y0_7.json"
    sidecar.write_text("{}", encoding="utf-8")
    drop_product_index(output)
    index = get_product_index(output)
    assert index.exists(RLE_FOLDER, sidecar.name)
    assert index.members(RLE_FOLDER, "Vol_1_D1_X0_Y0") == [(7, sidecar.name)]

    later = rle_dir(output) / "Vol_1_D1_X0_Y0_8.json"
    later.write_text("{}", encoding="utf-8")
    index.note_path(later)
    assert index.exists(RLE_FOLDER, later.name)
    index.forget_path(later)
    assert not index.exists(RLE_FOLDER, later.name)
    drop_product_index(output)
