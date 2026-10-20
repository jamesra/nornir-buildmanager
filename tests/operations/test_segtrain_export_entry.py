"""Characterization tests for the ExportSectionCrops body (``_export_section_crops_entry``).

Pins the -Repair precheck (no watermark, every image present), record loading
(off-edge filter, empty-section purge), and the stage cleanup around the repair,
update, and fresh-export branches: each branch releases stage pools only on
success and always schedules removal of the section stage tree.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nornir_buildmanager.operations.segmentationtraining import pipeline as pipeline_mod

_Z = 2
_SAVE_NODE = object()
_STAGE = "STAGE-ROOT"


class _Products:
    """Fake product index: ``exists`` answers from a fixed set of image names."""

    def __init__(self, names: set[str]) -> None:
        self._names = names

    def exists(self, _folder: str, name: str) -> bool:
        return name in self._names


def _mark(key: str) -> SimpleNamespace:
    return SimpleNamespace(key=key)


def _watermark(keys: list[str], image_keys: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(images=[_mark(k) for k in keys], image_keys=image_keys or [])


def _entry_kwargs(out: Path, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "OutputPath": str(out),
        "VolumeNode": None,
        "Pad": 0.0,
        "Downsample": 1,
        "Exporter": "",
        "MaxTexture": 8,
        "MaxTiles": None,
        "MinProcessPixels": 16,
        "IncludeOffEdge": False,
        "Channels": "TEM",
        "Filters": "Leveled",
        "Workers": 1,
        "MaskWorkers": 1,
        "StageTiles": None,
        "Force": False,
        "RefreshOData": False,
        "Update": False,
        "Repair": False,
        "FailMissingTileset": False,
        "Overlay": False,
        "TileXDim": 8,
        "TileYDim": 8,
        "LevelDirs": None,
        "FilePrefix": "",
        "FilePostfix": ".png",
        "TilesetMtime": None,
        "AvailableDownsamples": None,
        "TileFetcher": None,
        "VolumeName": "Vol",
        "filter_node": None,
        "section": None,
        "z": _Z,
    }
    kwargs.update(overrides)
    return kwargs


class _Harness:
    """Patches every collaborator of the entry and records the calls in order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.logs: list[str] = []
        self.watermark: Any = None
        self.records: list[Any] = []
        self.indexed: set[str] = set()
        self.fail: str | None = None
        self.fresh = False
        mp = monkeypatch
        mp.setattr(pipeline_mod.prettyoutput, "Log", self.logs.append)
        mp.setattr(pipeline_mod, "_images_index_for_repair", self._index)
        mp.setattr(pipeline_mod, "get_product_index", self._index)
        mp.setattr(pipeline_mod, "load_section_watermark", self._load_watermark)
        mp.setattr(pipeline_mod, "load_section_records", self._load_records)
        mp.setattr(pipeline_mod, "_purge_empty_section", self._record("purge"))
        mp.setattr(pipeline_mod, "_resolve_tileset", self._resolve_tileset)
        mp.setattr(pipeline_mod, "ExportParams", lambda **kw: SimpleNamespace(**kw))
        mp.setattr(pipeline_mod, "SectionCropRun", lambda **kw: SimpleNamespace(**kw))
        mp.setattr(pipeline_mod, "resolve_section_stage_root", lambda *_a, **_k: _STAGE)
        mp.setattr(pipeline_mod, "section_is_fresh", lambda *_a, **_k: self.fresh)
        mp.setattr(pipeline_mod, "drop_recorded_crops_missing_tiles", self._drop)
        mp.setattr(pipeline_mod, "stitch_missing_crop_marks", self._work("repair"))
        mp.setattr(pipeline_mod, "update_section_crops", self._work("update"))
        mp.setattr(pipeline_mod, "export_section_crops", self._work("export"))
        mp.setattr(pipeline_mod.nornir_pools, "ReleaseStagePools", self._record("release"))
        mp.setattr(pipeline_mod, "schedule_remove_stage_tree", self._record("remove"))

    def _record(self, name: str):
        def _call(*args: Any, **_kwargs: Any) -> None:
            self.calls.append((name, *args))

        return _call

    def _index(self, _output: Path) -> _Products:
        return _Products(self.indexed)

    def _load_watermark(self, _output: str, z: int) -> Any:
        self.calls.append(("watermark", z))
        return self.watermark

    def _load_records(self, _output: str, z: int) -> list[Any]:
        self.calls.append(("records", z))
        return list(self.records)

    def _resolve_tileset(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        self.calls.append(("tileset",))
        return SimpleNamespace(
            save_node=_SAVE_NODE, tile_shape=SimpleNamespace(x=8, y=8), mtime=0.0
        )

    def _drop(self, *_args: Any, only_keys: set[str] | None = None, **_kwargs: Any) -> bool:
        self.calls.append(("drop", None if only_keys is None else sorted(only_keys)))
        return False

    def _work(self, name: str):
        def _call(**kwargs: Any) -> None:
            marks = kwargs.get("marks")
            records = kwargs.get("records")
            self.calls.append(
                (
                    name,
                    None if marks is None else [m.key for m in marks],
                    None if records is None else [r.id for r in records],
                )
            )
            if self.fail == name:
                raise RuntimeError(f"{name} failed")

        return _call

    def names(self) -> list[Any]:
        return [call[0] for call in self.calls]


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    return _Harness(monkeypatch)


def _rec(loc_id: int, off_edge: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=loc_id, off_edge=off_edge)


def test_repair_without_watermark_skips_before_tileset(harness: _Harness, tmp_path: Path) -> None:
    harness.watermark = None
    result = pipeline_mod._export_section_crops_entry(**_entry_kwargs(tmp_path, Repair=True))
    assert result is None
    assert harness.calls == [("watermark", _Z)]
    assert harness.logs == [
        f"ExportTrainingData -Repair: section {_Z} has no watermark, skipping"
    ]


def test_repair_with_every_image_present_skips(harness: _Harness, tmp_path: Path) -> None:
    harness.watermark = _watermark(["a", "a", "b"], image_keys=["a", "b"])
    harness.indexed = {"a.png", "b.jpg"}
    result = pipeline_mod._export_section_crops_entry(**_entry_kwargs(tmp_path, Repair=True))
    assert result is None
    assert harness.calls == [("watermark", _Z)]
    assert harness.logs == [
        f"ExportTrainingData -Repair: section {_Z} — all 2 images present, skipping"
    ]


def test_repair_logs_keys_without_geometry_before_all_present_skip(
    harness: _Harness, tmp_path: Path
) -> None:
    harness.watermark = _watermark(["a"], image_keys=["a", "c", "d"])
    harness.indexed = {"a.png", "d.png"}
    result = pipeline_mod._export_section_crops_entry(**_entry_kwargs(tmp_path, Repair=True))
    assert result is None
    assert harness.logs == [
        (
            f"ExportTrainingData -Repair: section {_Z} image c "
            "is missing and has no crop geometry; skipping"
        ),
        f"ExportTrainingData -Repair: section {_Z} — all 1 images present, skipping",
    ]


def test_repair_with_missing_crop_stitches_without_loading_records(
    harness: _Harness, tmp_path: Path
) -> None:
    harness.watermark = _watermark(["a", "b"])
    harness.indexed = {"a.png"}
    result = pipeline_mod._export_section_crops_entry(**_entry_kwargs(tmp_path, Repair=True))
    assert result is _SAVE_NODE
    assert harness.calls == [
        ("watermark", _Z),
        ("tileset",),
        ("watermark", _Z),
        ("drop", ["b"]),
        ("watermark", _Z),
        ("repair", ["b"], None),
        ("release",),
        ("remove", _STAGE),
    ]
    assert harness.logs == []


def test_repair_returns_save_node_when_drop_leaves_nothing_missing(
    harness: _Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.watermark = _watermark(["a", "b"])
    harness.indexed = {"a.png"}

    def _drop_then_index(*_args: Any, only_keys: set[str] | None = None, **_kw: Any) -> bool:
        harness.calls.append(("drop", sorted(only_keys or ())))
        harness.watermark = _watermark(["a"])
        return True

    monkeypatch.setattr(pipeline_mod, "drop_recorded_crops_missing_tiles", _drop_then_index)
    result = pipeline_mod._export_section_crops_entry(**_entry_kwargs(tmp_path, Repair=True))
    assert result is _SAVE_NODE
    assert "repair" not in harness.names()
    assert "remove" not in harness.names()


def test_no_records_purges_section_and_skips(harness: _Harness, tmp_path: Path) -> None:
    harness.records = []
    result = pipeline_mod._export_section_crops_entry(**_entry_kwargs(tmp_path))
    assert result is None
    assert harness.calls == [("records", _Z), ("purge", tmp_path, _Z)]
    assert harness.logs == [f"ExportTrainingData: no geometries for section {_Z}"]


def test_only_off_edge_records_purge_when_excluded(harness: _Harness, tmp_path: Path) -> None:
    harness.records = [_rec(1, off_edge=True), _rec(2, off_edge=True)]
    result = pipeline_mod._export_section_crops_entry(**_entry_kwargs(tmp_path))
    assert result is None
    assert harness.calls == [("records", _Z), ("purge", tmp_path, _Z)]


@pytest.mark.parametrize("include_off_edge", [False, True])
def test_off_edge_filter_controls_exported_records(
    harness: _Harness, tmp_path: Path, include_off_edge: bool
) -> None:
    harness.records = [_rec(1), _rec(2, off_edge=True), _rec(3)]
    result = pipeline_mod._export_section_crops_entry(
        **_entry_kwargs(tmp_path, IncludeOffEdge=include_off_edge)
    )
    assert result is _SAVE_NODE
    expected = [1, 2, 3] if include_off_edge else [1, 3]
    assert harness.calls == [
        ("records", _Z),
        ("tileset",),
        ("watermark", _Z),
        ("export", None, expected),
        ("release",),
        ("remove", _STAGE),
    ]


def test_fresh_section_skips_without_stage_work(harness: _Harness, tmp_path: Path) -> None:
    harness.records = [_rec(1), _rec(2)]
    harness.fresh = True
    result = pipeline_mod._export_section_crops_entry(**_entry_kwargs(tmp_path))
    assert result is _SAVE_NODE
    assert harness.names() == ["records", "tileset", "watermark", "drop"]
    assert harness.logs == [
        f"ExportTrainingData: section {_Z} is fresh, skipping (2 annotation(s))"
    ]


def test_update_runs_update_then_cleanup(harness: _Harness, tmp_path: Path) -> None:
    harness.records = [_rec(5)]
    result = pipeline_mod._export_section_crops_entry(**_entry_kwargs(tmp_path, Update=True))
    assert result is _SAVE_NODE
    assert harness.calls[-3:] == [("update", None, [5]), ("release",), ("remove", _STAGE)]


@pytest.mark.parametrize(
    ("work", "overrides"),
    [
        ("repair", {"Repair": True}),
        ("update", {"Update": True}),
        ("export", {}),
    ],
)
def test_failed_branch_still_removes_stage_tree_without_release(
    harness: _Harness, tmp_path: Path, work: str, overrides: dict[str, Any]
) -> None:
    harness.watermark = _watermark(["a"])
    harness.records = [_rec(7)]
    harness.fail = work
    with pytest.raises(RuntimeError, match=f"{work} failed"):
        pipeline_mod._export_section_crops_entry(**_entry_kwargs(tmp_path, **overrides))
    names = harness.names()
    assert names[-2:] == [work, "remove"]
    assert "release" not in names
    assert harness.calls[-1] == ("remove", _STAGE)


@pytest.mark.parametrize("overrides", [{"Repair": True}, {"Update": True}, {}])
def test_branches_run_under_numpy_backend(
    harness: _Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any]
) -> None:
    harness.watermark = _watermark(["a"])
    harness.records = [_rec(7)]
    state = {"inside": False}

    class _Backend:
        def __enter__(self) -> None:
            harness.calls.append(("numpy-enter",))
            state["inside"] = True

        def __exit__(self, *_exc: object) -> None:
            harness.calls.append(("numpy-exit",))
            state["inside"] = False

    monkeypatch.setattr(pipeline_mod, "force_numpy_computation", _Backend)
    monkeypatch.setattr(
        pipeline_mod.nornir_pools,
        "ReleaseStagePools",
        lambda: harness.calls.append(("release", state["inside"])),
    )
    pipeline_mod._export_section_crops_entry(**_entry_kwargs(tmp_path, **overrides))
    names = harness.names()
    start = names.index("numpy-enter")
    assert names[start + 2 :] == ["release", "numpy-exit", "remove"]
    assert ("release", True) in harness.calls
