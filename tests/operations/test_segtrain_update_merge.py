"""Characterization tests for update.py helpers: _merge_added_json, _rewrite_manifest, _place_added."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nornir_buildmanager.operations.segmentationtraining import update
from nornir_buildmanager.operations.segmentationtraining.freshness import ImageWatermark
from nornir_buildmanager.operations.segmentationtraining.records import (
    LocationRecord,
    LocationType,
)
from nornir_buildmanager.operations.segmentationtraining.sam2.write import (
    sa1b_annotation,
)
from nornir_buildmanager.operations.segmentationtraining.workdir import rle_dir

RLE = {"size": [4, 4], "counts": [4, 4, 8]}
OLD_RLE = {"size": [4, 4], "counts": [16]}


def _record(loc_id: int) -> LocationRecord:
    return LocationRecord(
        id=loc_id, z=1, wkt="POLYGON((0 0,4 0,4 4,0 4,0 0))", parent_id=None, off_edge=False,
        last_modified=datetime(2020, 1, 1), type_id=1, type_name="Cell", structure_label=None,
        type_code=LocationType.POLYGON,
    )


class _Products:
    """Stands in for CropProductIndex: records noted paths, answers ``exists`` from a set."""

    def __init__(self, existing: set[tuple[str, str]] | None = None) -> None:
        self.noted: list[Any] = []
        self.existing = existing or set()

    def note_path(self, path: Any) -> None:
        self.noted.append(path)

    def exists(self, folder: str, name: str) -> bool:
        return (folder, name) in self.existing


def _products(existing: set[tuple[str, str]] | None = None) -> Any:
    """Return a `_Products` typed as `Any` so it can stand in for `CropProductIndex`."""
    return _Products(existing)


def _plan(key: str, ids: list[int], window_of: dict[int, tuple[int, int]] | None = None) -> Any:
    return SimpleNamespace(
        image_key=key,
        location_ids=ids,
        window_of=window_of or {},
        downsample=2,
        snap="snap",
        output_size=lambda: (40, 30),
        window=SimpleNamespace(crop_offset=lambda tx, ty: (tx + 1, ty + 2)),
    )


def _output(tmp_path: Path) -> Path:
    out = tmp_path / "vol" / "out"
    (out / "images").mkdir(parents=True)
    rle_dir(out).mkdir(parents=True)
    return out


def _write_rle(out: Path, key: str, loc_id: int) -> None:
    (rle_dir(out) / f"{key}_{loc_id}.json").write_text(json.dumps(RLE), encoding="utf-8")


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ---- _merge_added_json -------------------------------------------------------------------------


def test_merge_creates_json_for_new_crop_with_window_info(tmp_path: Path) -> None:
    out = _output(tmp_path)
    _write_rle(out, "k1", 7)
    products = _products()
    plan = _plan("k1", [7], {7: (1, 3)})
    update._merge_added_json(out, [plan], {7: _record(7)}, products, overlay=False)

    payload = _read(out / "images" / "k1.json")
    assert payload["image"] == {"file_name": "k1.png", "width": 40, "height": 30, "downsample": 2}
    (item,) = payload["annotations"]
    assert item["id"] == 7 and item["segmentation"] == RLE
    assert (item["windowIndex"], item["windowCount"]) == (1, 3)
    assert products.noted == [out / "images" / "k1.json"]


def test_merge_defaults_window_to_single(tmp_path: Path) -> None:
    out = _output(tmp_path)
    _write_rle(out, "k1", 7)
    update._merge_added_json(out, [_plan("k1", [7])], {7: _record(7)}, _products(), overlay=False)
    (item,) = _read(out / "images" / "k1.json")["annotations"]
    assert (item["windowIndex"], item["windowCount"]) == (0, 1)


def test_merge_keeps_existing_and_skips_duplicate_ids(tmp_path: Path) -> None:
    out = _output(tmp_path)
    old = sa1b_annotation(_record(7), rle=OLD_RLE, bbox=[9, 9, 9, 9], area=99)
    meta = {"file_name": "custom.png", "width": 11, "height": 12, "volume": "VolA"}
    (out / "images" / "k1.json").write_text(json.dumps({"image": meta, "annotations": [old]}), encoding="utf-8")
    _write_rle(out, "k1", 7)  # id 7 already present: must not replace or duplicate it
    _write_rle(out, "k1", 8)
    update._merge_added_json(
        out, [_plan("k1", [7, 8])], {7: _record(7), 8: _record(8)}, _products(), overlay=False
    )
    payload = _read(out / "images" / "k1.json")
    assert [a["id"] for a in payload["annotations"]] == [7, 8]
    assert payload["annotations"][0] == old
    assert payload["image"] == {"file_name": "custom.png", "width": 11, "height": 12, "downsample": 2, "volume": "VolA"}


def test_merge_skips_ids_without_rle_sidecar(tmp_path: Path) -> None:
    out = _output(tmp_path)
    _write_rle(out, "k1", 8)
    update._merge_added_json(
        out, [_plan("k1", [7, 8])], {7: _record(7), 8: _record(8)}, _products(), overlay=False
    )
    assert [a["id"] for a in _read(out / "images" / "k1.json")["annotations"]] == [8]


def test_merge_writes_nothing_when_no_existing_and_no_new(tmp_path: Path) -> None:
    out = _output(tmp_path)
    products = _products()
    update._merge_added_json(out, [_plan("k1", [7])], {7: _record(7)}, products, overlay=False)
    assert not (out / "images" / "k1.json").exists()
    assert products.noted == []


def test_merge_rewrites_existing_only_json(tmp_path: Path) -> None:
    out = _output(tmp_path)
    old = sa1b_annotation(_record(7), rle=OLD_RLE, bbox=[0, 0, 0, 0], area=0)
    (out / "images" / "k1.json").write_text(json.dumps({"image": {}, "annotations": [old]}), encoding="utf-8")
    products = _products()
    update._merge_added_json(out, [_plan("k1", [7])], {7: _record(7)}, products, overlay=False)
    payload = _read(out / "images" / "k1.json")
    assert payload["annotations"] == [old]
    assert payload["image"]["width"] == 40 and payload["image"]["height"] == 30
    assert products.noted == [out / "images" / "k1.json"]


def test_merge_keeps_existing_odata_when_no_export_source(tmp_path: Path) -> None:
    out = _output(tmp_path)
    old = sa1b_annotation(_record(7), rle=OLD_RLE, bbox=[0, 0, 0, 0], area=0)
    meta = {"odata": " http://host/odata "}
    (out / "images" / "k1.json").write_text(json.dumps({"image": meta, "annotations": [old]}), encoding="utf-8")
    update._merge_added_json(out, [_plan("k1", [7])], {7: _record(7)}, _products(), overlay=False)
    assert _read(out / "images" / "k1.json")["image"]["odata"] == "http://host/odata"


def test_merge_overlay_lists_masks_for_all_annotations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _output(tmp_path)
    old = sa1b_annotation(_record(7), rle=OLD_RLE, bbox=[0, 0, 0, 0], area=0)
    (out / "images" / "k1.json").write_text(
        json.dumps({"image": {"width": 5, "height": 6}, "annotations": [old]}), encoding="utf-8"
    )
    _write_rle(out, "k1", 8)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(update, "write_overlay", lambda path, **kw: calls.append({"path": path, **kw}))
    monkeypatch.setattr(update, "resolve_crop_image", lambda d, key: Path(d) / f"{key}.png")
    products = _products()
    update._merge_added_json(
        out, [_plan("k1", [8])], {7: _record(7), 8: _record(8)}, products, overlay=True
    )
    (call,) = calls
    assert call["path"] == out / "overlays" / "k1.png"
    assert (call["width"], call["height"]) == (5, 6)
    assert call["mask_paths"] == [str(out / "masks" / "k1_7.png"), str(out / "masks" / "k1_8.png")]
    assert call["tem_path"] == out / "images" / "k1.png"
    assert products.noted[-1] == out / "overlays" / "k1.png"


# ---- _rewrite_manifest -------------------------------------------------------------------------


def _mark(key: str, ids: list[int], downsample: int = 2) -> ImageWatermark:
    return ImageWatermark(
        key=key, downsample=downsample, ix0=0, ix1=1, iy0=0, iy1=1, member_ids=ids, max_last_modified="x"
    )


def _manifest(out: Path) -> list[dict[str, Any]]:
    path = out / "manifest.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_rewrite_manifest_creates_rows_from_marks(tmp_path: Path) -> None:
    update._rewrite_manifest(tmp_path, 5, "VolA", [_mark("k1", [1, 2])], set())
    assert _manifest(tmp_path) == [
        {
            "z": 5, "volume": "VolA", "imageKey": "k1", "downsample": 2,
            "image": "images/k1.png", "json": "images/k1.json", "locationIds": [1, 2],
        }
    ]


def test_rewrite_manifest_updates_existing_row_and_keeps_other_sections(tmp_path: Path) -> None:
    rows = [
        {"z": 5, "imageKey": "k1", "extra": "keep", "locationIds": [1, 2, 3]},
        {"z": 6, "imageKey": "other", "locationIds": [9]},
    ]
    (tmp_path / "manifest.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n\n", encoding="utf-8")
    update._rewrite_manifest(tmp_path, 5, "VolA", [_mark("k1", [1, 2, 4])], {3})
    out = _manifest(tmp_path)
    assert {"z": 6, "imageKey": "other", "locationIds": [9]} in out
    (row,) = [r for r in out if r["z"] == 5]
    assert row == {"z": 5, "imageKey": "k1", "extra": "keep", "locationIds": [1, 2, 4]}


def test_rewrite_manifest_drops_rows_without_marks(tmp_path: Path) -> None:
    rows = [{"z": 5, "imageKey": "gone", "locationIds": [1]}, {"z": 5, "imageKey": "k1", "locationIds": [2]}]
    (tmp_path / "manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    update._rewrite_manifest(tmp_path, 5, "VolA", [_mark("k1", [2])], set())
    assert [r["imageKey"] for r in _manifest(tmp_path)] == ["k1"]


def test_rewrite_manifest_fully_removed_row_is_rebuilt_from_mark(tmp_path: Path) -> None:
    """A row whose ids were all removed is dropped, so a surviving mark gets a fresh default row."""
    (tmp_path / "manifest.jsonl").write_text(
        json.dumps({"z": 5, "imageKey": "k1", "extra": "stale", "locationIds": [1]}) + "\n", encoding="utf-8"
    )
    update._rewrite_manifest(tmp_path, 5, "VolA", [_mark("k1", [7])], {1})
    (row,) = _manifest(tmp_path)
    assert "extra" not in row and row["locationIds"] == [7] and row["volume"] == "VolA"


def test_rewrite_manifest_follows_mark_order(tmp_path: Path) -> None:
    update._rewrite_manifest(tmp_path, 5, "V", [_mark("b", [1]), _mark("a", [2])], set())
    assert [r["imageKey"] for r in _manifest(tmp_path)] == ["b", "a"]


# ---- _place_added ------------------------------------------------------------------------------


def _patch_place(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"order": []}

    def fake_mask_jobs(output: Path, plan: Any, polygons: Any) -> list[Any]:
        return [SimpleNamespace(mask_path=f"m_{plan.image_key}", rle_path=f"r_{plan.image_key}")]

    def fake_run(jobs: list[Any], workers: int) -> None:
        seen["order"].append("masks")
        seen["mask_jobs"], seen["workers"] = jobs, workers

    def fake_stitch(jobs: list[Any], run: Any, products: Any) -> list[str]:
        seen["order"].append("stitch")
        seen["stitch_jobs"] = jobs
        return ["blank"]

    def fake_merge(output: Path, plans: list[Any], by_id: Any, products: Any, overlay: bool) -> None:
        seen["order"].append("merge")
        seen["merge"] = (plans, overlay)

    monkeypatch.setattr(update, "mask_jobs_for_plan", fake_mask_jobs)
    monkeypatch.setattr(update, "run_mask_jobs", fake_run)
    monkeypatch.setattr(update, "apply_ignore_moves", lambda out: seen["order"].append("ignore"))
    monkeypatch.setattr(update, "_stitch", fake_stitch)
    monkeypatch.setattr(update, "_merge_added_json", fake_merge)
    return seen


def _run() -> Any:
    return SimpleNamespace(
        tileset=SimpleNamespace(tile_shape=SimpleNamespace(x=100, y=200)), mask_workers=3, overlay=True
    )


def test_place_added_stitches_only_missing_crops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_place(monkeypatch)
    products = _products({("images", "onDisk.png")})
    previous: Any = SimpleNamespace(images=[SimpleNamespace(key="inMark")], image_keys=["inKeys"])
    plans = [_plan(k, [1]) for k in ("new", "onDisk", "inMark", "inKeys")]
    blanks = update._place_added(tmp_path, 1, plans, {}, {}, previous, products, _run())

    assert blanks == ["blank"]
    (job,) = seen["stitch_jobs"]
    assert job.image_key == "new" and job.downsample == 2 and job.snap == "snap"
    assert (job.crop_x, job.crop_y, job.crop_width, job.crop_height) == (101, 202, 40, 30)
    assert job.image_path == str(tmp_path / "images" / "new.png")
    # masks are made for every plan, whether or not the image exists
    assert len(seen["mask_jobs"]) == 4 and seen["workers"] == 3
    assert seen["order"] == ["masks", "ignore", "stitch", "merge"]
    assert seen["merge"] == (plans, True)
    assert "m_new" in products.noted and "r_new" in products.noted


def test_place_added_treats_legacy_jpg_as_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_place(monkeypatch)
    products = _products({("images", "old.jpg")})
    blanks = update._place_added(tmp_path, 1, [_plan("old", [1])], {}, {}, None, products, _run())
    assert blanks == [] and "stitch_jobs" not in seen
    assert seen["order"] == ["masks", "ignore", "merge"]


def test_place_added_without_masks_skips_mask_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_place(monkeypatch)
    monkeypatch.setattr(update, "mask_jobs_for_plan", lambda *a: [])
    update._place_added(tmp_path, 1, [_plan("old", [1])], {}, {}, None, _products({("images", "old.png")}), _run())
    assert "mask_jobs" not in seen

