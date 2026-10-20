"""manifest.jsonl rewrites (_strip_manifest_keys, replace_manifest_rows) are byte-stable and atomic."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from nornir_buildmanager.operations.segmentationtraining.pipeline import (
    _strip_manifest_keys,
)
from nornir_buildmanager.operations.segmentationtraining.sam2.write import (
    replace_manifest_rows,
)


def _dump(rows: list[dict[str, Any]]) -> bytes:
    """Bytes the exporter has always written: compact JSON, one row per line, platform newlines."""
    text = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    return text.replace("\n", os.linesep).encode("utf-8")


def _rows() -> list[dict[str, Any]]:
    return [
        {"z": 1, "imageKey": "a", "n": 1},
        {"z": 1, "imageKey": "b", "n": 2},
        {"z": 2, "imageKey": "a", "n": 3},
        {"imageKey": "a", "n": 4},
    ]


def _write(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "manifest.jsonl"
    path.write_bytes(_dump(rows))
    return path


def _fail_replace(src: object, dst: object) -> None:
    raise OSError("simulated crash before the swap")


def test_strip_keeps_other_rows_byte_identical(tmp_path: Path) -> None:
    path = _write(tmp_path, _rows())
    _strip_manifest_keys(tmp_path, 1, {"a"})
    assert path.read_bytes() == _dump([_rows()[1], _rows()[2], _rows()[3]])
    assert not list(tmp_path.glob("*.tmp"))


def test_strip_everything_leaves_empty_file(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"z": 1, "imageKey": "a"}])
    _strip_manifest_keys(tmp_path, 1, {"a"})
    assert path.read_bytes() == b""


def test_strip_without_keys_or_file_does_nothing(tmp_path: Path) -> None:
    _strip_manifest_keys(tmp_path, 1, {"a"})
    assert not (tmp_path / "manifest.jsonl").exists()
    path = _write(tmp_path, _rows())
    before = path.read_bytes()
    _strip_manifest_keys(tmp_path, 1, set())
    assert path.read_bytes() == before


def test_strip_failed_write_keeps_old_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, _rows())
    before = path.read_bytes()
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        _strip_manifest_keys(tmp_path, 1, {"a"})
    monkeypatch.undo()
    assert path.read_bytes() == before


def test_replace_rows_swaps_one_section(tmp_path: Path) -> None:
    path = _write(tmp_path, _rows())
    new = [{"z": 2, "imageKey": "c", "n": 9}]
    replace_manifest_rows(tmp_path, 2, new)
    assert path.read_bytes() == _dump([_rows()[0], _rows()[1], _rows()[3], new[0]])
    assert not list(tmp_path.glob("*.tmp"))


def test_replace_rows_creates_missing_manifest(tmp_path: Path) -> None:
    replace_manifest_rows(tmp_path, 1, [{"z": 1, "imageKey": "a"}])
    assert (tmp_path / "manifest.jsonl").read_bytes() == _dump([{"z": 1, "imageKey": "a"}])


def test_replace_rows_with_no_rows_clears_section(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"z": 1, "imageKey": "a"}])
    replace_manifest_rows(tmp_path, 1, [])
    assert path.read_bytes() == b""


def test_replace_rows_failed_write_keeps_old_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, _rows())
    before = path.read_bytes()
    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        replace_manifest_rows(tmp_path, 1, [{"z": 1, "imageKey": "x"}])
    monkeypatch.undo()
    assert path.read_bytes() == before
