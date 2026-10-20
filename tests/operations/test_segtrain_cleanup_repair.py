"""Characterization tests for ``cleanup._repair_key`` and the ``CleanupAnnotationCrops`` entry."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.segmentationtraining import cleanup
from nornir_buildmanager.operations.segmentationtraining.cleanup import (
    CleanupAnnotationCrops,
    CleanupSummary,
    _repair_key,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    drop_product_index,
)
from nornir_buildmanager.operations.segmentationtraining.workdir import rle_dir
from PIL import Image

KEY = "Vol_5_D1_x0"


@pytest.fixture
def out(tmp_path: Path) -> Iterator[Path]:
    """An empty export folder with the standard product subfolders."""
    root = tmp_path / "export"
    for folder in ("images", "masks", "ignored", "overlays"):
        (root / folder).mkdir(parents=True)
    rle_dir(root).mkdir(parents=True)
    drop_product_index(root)
    yield root
    drop_product_index(root)


def _png(path: Path, size: tuple[int, int] = (4, 3)) -> None:
    Image.fromarray(np.zeros((size[1], size[0]), dtype=np.uint8), mode="L").save(path)


def _write_json(out: Path, payload: Any, key: str = KEY) -> Path:
    path = out / "images" / f"{key}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _good_payload(**image_overrides: Any) -> dict[str, Any]:
    image = {"file_name": f"{KEY}.png", "width": 4, "height": 3}
    image.update(image_overrides)
    return {"image": image, "annotations": []}


def _repair(out: Path, odata: str | None = None) -> CleanupSummary:
    drop_product_index(out)
    summary = CleanupSummary()
    _repair_key(out, KEY, summary, odata=odata)
    return summary


def test_overlay_with_no_image_is_deleted(out: Path) -> None:
    overlay = out / "overlays" / f"{KEY}.png"
    _png(overlay)
    summary = _repair(out)
    assert not overlay.exists()
    assert summary == CleanupSummary(deleted_overlays=1)


def test_members_with_no_image_are_removed(out: Path) -> None:
    mask = out / "masks" / f"{KEY}_7.png"
    ignored = out / "ignored" / f"{KEY}_8.png"
    rle = rle_dir(out) / f"{KEY}_9.json"
    other = out / "masks" / "Vol_6_D1_x0_7.png"
    for path in (mask, ignored, other):
        _png(path)
    rle.write_text("{}", encoding="utf-8")
    summary = _repair(out)
    assert not mask.exists() and not ignored.exists() and not rle.exists()
    assert other.exists()
    assert summary == CleanupSummary(deleted_members=3)


def test_no_image_and_nothing_else_changes_nothing(out: Path) -> None:
    assert _repair(out) == CleanupSummary()


def test_image_without_json_deletes_the_key(out: Path) -> None:
    _png(out / "images" / f"{KEY}.png")
    _png(out / "overlays" / f"{KEY}.png")
    summary = _repair(out)
    assert not (out / "images" / f"{KEY}.png").exists()
    assert not (out / "overlays" / f"{KEY}.png").exists()
    assert summary == CleanupSummary(deleted_keys=1)


def test_json_without_image_deletes_the_key(out: Path) -> None:
    path = _write_json(out, _good_payload())
    summary = _repair(out)
    assert not path.exists()
    assert summary.deleted_keys == 1


def test_consistent_key_is_left_untouched(out: Path) -> None:
    _png(out / "images" / f"{KEY}.png")
    path = _write_json(out, _good_payload())
    before = path.read_text(encoding="utf-8")
    summary = _repair(out)
    assert path.read_text(encoding="utf-8") == before
    assert summary == CleanupSummary()


def test_json_missing_image_block_raises(out: Path) -> None:
    _png(out / "images" / f"{KEY}.png")
    _write_json(out, {"annotations": []})
    with pytest.raises(NornirUserException, match="missing image"):
        _repair(out)


def test_image_block_not_a_dict_raises(out: Path) -> None:
    _png(out / "images" / f"{KEY}.png")
    _write_json(out, {"image": [1], "annotations": []})
    with pytest.raises(NornirUserException, match="missing image"):
        _repair(out)


def test_annotations_not_a_list_raises(out: Path) -> None:
    _png(out / "images" / f"{KEY}.png")
    _write_json(out, {**_good_payload(), "annotations": {"id": 1}})
    with pytest.raises(NornirUserException, match="not a list"):
        _repair(out)


def test_annotation_without_id_raises(out: Path) -> None:
    _png(out / "images" / f"{KEY}.png")
    _write_json(out, {**_good_payload(), "annotations": [{"area": 3}]})
    with pytest.raises(NornirUserException, match="without id"):
        _repair(out)


def test_annotation_not_a_dict_raises(out: Path) -> None:
    _png(out / "images" / f"{KEY}.png")
    _write_json(out, {**_good_payload(), "annotations": [5]})
    with pytest.raises(NornirUserException, match="without id"):
        _repair(out)


def test_missing_annotations_key_is_treated_as_empty(out: Path) -> None:
    _png(out / "images" / f"{KEY}.png")
    _write_json(out, {"image": _good_payload()["image"]})
    assert _repair(out) == CleanupSummary()


def test_unreadable_image_raises(out: Path) -> None:
    (out / "images" / f"{KEY}.png").write_bytes(b"not a png")
    _write_json(out, _good_payload())
    with pytest.raises(NornirUserException, match="unreadable"):
        _repair(out)


@pytest.mark.parametrize("text", ["{ not json", "[1, 2]"])
def test_bad_json_raises(out: Path, text: str) -> None:
    _png(out / "images" / f"{KEY}.png")
    (out / "images" / f"{KEY}.json").write_text(text, encoding="utf-8")
    with pytest.raises(NornirUserException, match="JSON"):
        _repair(out)


def test_wrong_file_name_and_size_are_rewritten(out: Path) -> None:
    _png(out / "images" / f"{KEY}.png", size=(4, 3))
    path = _write_json(out, _good_payload(file_name="old.jpg", width=9, height="x"))
    summary = _repair(out)
    image = json.loads(path.read_text(encoding="utf-8"))["image"]
    assert image["file_name"] == f"{KEY}.png"
    assert (image["width"], image["height"]) == (4, 3)
    assert summary.rewritten_json == 1


@pytest.mark.parametrize("override", [{"width": 9}, {"height": 9}, {"width": None}, {"height": None}])
def test_either_dimension_mismatch_rewrites_size(out: Path, override: dict[str, Any]) -> None:
    _png(out / "images" / f"{KEY}.png", size=(4, 3))
    path = _write_json(out, _good_payload(**override))
    assert _repair(out).rewritten_json == 1
    image = json.loads(path.read_text(encoding="utf-8"))["image"]
    assert (image["width"], image["height"]) == (4, 3)


def test_odata_recorded_only_when_it_differs(out: Path) -> None:
    _png(out / "images" / f"{KEY}.png")
    path = _write_json(out, _good_payload())
    assert _repair(out, odata=None).rewritten_json == 0
    assert _repair(out, odata="").rewritten_json == 0
    assert "odata" not in json.loads(path.read_text(encoding="utf-8"))["image"]
    assert _repair(out, odata="http://h/odata").rewritten_json == 1
    assert json.loads(path.read_text(encoding="utf-8"))["image"]["odata"] == "http://h/odata"
    assert _repair(out, odata="http://h/odata").rewritten_json == 0
    assert _repair(out, odata="http://other/odata").rewritten_json == 1
    assert json.loads(path.read_text(encoding="utf-8"))["image"]["odata"] == "http://other/odata"


def test_annotation_without_mask_is_dropped_and_orphan_members_removed(out: Path) -> None:
    _png(out / "images" / f"{KEY}.png")
    _png(out / "masks" / f"{KEY}_1.png")
    _png(out / "ignored" / f"{KEY}_2.png")
    _png(out / "masks" / f"{KEY}_99.png")
    (rle_dir(out) / f"{KEY}_99.json").write_text("{}", encoding="utf-8")
    payload = _good_payload()
    payload["annotations"] = [{"id": 1}, {"id": 2}, {"id": 3}]
    path = _write_json(out, payload)
    summary = _repair(out)
    kept = [a["id"] for a in json.loads(path.read_text(encoding="utf-8"))["annotations"]]
    assert kept == [1, 2]
    assert (out / "masks" / f"{KEY}_1.png").exists()
    assert (out / "ignored" / f"{KEY}_2.png").exists()
    assert not (out / "masks" / f"{KEY}_99.png").exists()
    assert not (rle_dir(out) / f"{KEY}_99.json").exists()
    assert summary.rewritten_json == 1
    assert summary.deleted_members == 2


def test_string_annotation_id_is_coerced_to_int_for_mask_lookup(out: Path) -> None:
    _png(out / "images" / f"{KEY}.png")
    _png(out / "masks" / f"{KEY}_4.png")
    payload = _good_payload()
    payload["annotations"] = [{"id": "4"}]
    _write_json(out, payload)
    summary = _repair(out)
    assert summary == CleanupSummary()
    assert (out / "masks" / f"{KEY}_4.png").exists()


def test_entry_is_noop_when_cleanup_falsy(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("must not run")

    monkeypatch.setattr(cleanup, "cleanup_annotation_crops", _boom)
    assert CleanupAnnotationCrops(OutputPath=None, Cleanup=False) is None
    assert CleanupAnnotationCrops(OutputPath="x", Cleanup=False) is None


@pytest.mark.parametrize("output_path", [None, ""])
def test_entry_requires_output_path(output_path: str | None) -> None:
    with pytest.raises(NornirUserException, match="output folder"):
        CleanupAnnotationCrops(OutputPath=output_path, Cleanup=True)


@pytest.mark.parametrize(
    ("sections", "expected"),
    [(None, None), ("", None), ([1, "3"], [1, 3]), (7, [7]), ("4", [4])],
)
def test_entry_passes_sections_as_int_list(
    monkeypatch: pytest.MonkeyPatch, sections: Any, expected: list[int] | None
) -> None:
    seen: dict[str, Any] = {}

    def _fake(path: Any, *, sections: Any = None) -> CleanupSummary:
        seen["path"] = path
        seen["sections"] = sections
        return CleanupSummary()

    monkeypatch.setattr(cleanup, "cleanup_annotation_crops", _fake)
    assert CleanupAnnotationCrops(OutputPath="out", Sections=sections, Cleanup=True, extra=1) is None
    assert seen == {"path": "out", "sections": expected}
