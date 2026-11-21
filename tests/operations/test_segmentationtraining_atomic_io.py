"""Atomic text writes used by the segmentation-training sidecar and cache writers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from nornir_buildmanager.operations.segmentationtraining.atomic_io import (
    atomic_write_text,
    write_text_if_changed,
)
from nornir_buildmanager.operations.segmentationtraining.training_set import (
    SIDECAR_FILENAME,
    write_training_set_info,
)

TEXTS = st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"), max_size=200)


def _leftover_temps(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.endswith(".tmp"))


@settings(max_examples=50, deadline=None)
@given(first=TEXTS, second=TEXTS)
def test_write_if_changed_reports_change_and_leaves_no_temp(first: str, second: str) -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "manifest.jsonl"
        assert write_text_if_changed(path, first) is True
        assert path.read_text(encoding="utf-8") == first
        assert write_text_if_changed(path, first) is False
        assert write_text_if_changed(path, second) is (second != first)
        assert path.read_text(encoding="utf-8") == second
        assert _leftover_temps(Path(folder)) == []


def test_write_if_changed_keeps_mtime_when_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "shapes.jsonl"
    write_text_if_changed(path, "a\n")
    before = path.stat().st_mtime_ns
    path.with_name(path.name + ".tmp").write_text("stale", encoding="utf-8")
    assert write_text_if_changed(path, "a\n") is False
    assert path.stat().st_mtime_ns == before
    assert path.with_name(path.name + ".tmp").read_text(encoding="utf-8") == "stale"


def _on_disk(text: str) -> bytes:
    """Bytes ``Path.write_text`` produces: utf-8 with platform newline translation."""
    return text.replace("\n", os.linesep).encode()


def test_write_if_changed_writes_utf8_with_platform_newlines(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    write_text_if_changed(path, "é\nb\n")
    assert path.read_bytes() == _on_disk("é\nb\n")


def test_write_training_set_info_bytes_and_overwrite(tmp_path: Path) -> None:
    folder = tmp_path / "v1.0"
    path = write_training_set_info(folder, {"b": 1, "a": "é"})
    assert path == folder / SIDECAR_FILENAME
    assert path.read_bytes() == _on_disk(json.dumps({"a": "é", "b": 1}, indent=2, sort_keys=True) + "\n")
    write_training_set_info(folder, {"a": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 2}
    assert _leftover_temps(folder) == []


def test_atomic_write_text_replaces_and_ignores_stale_temp(tmp_path: Path) -> None:
    path = tmp_path / "_index.json"
    path.with_name(path.name + ".tmp").write_text("partial", encoding="utf-8")
    atomic_write_text(path, "old")
    atomic_write_text(path, "new\n")
    assert path.read_bytes() == _on_disk("new\n")
    assert _leftover_temps(tmp_path) == []


def test_a_replace_refused_while_the_target_is_briefly_held_open_is_retried(monkeypatch, tmp_path: Path) -> None:
    """On SMB, Windows refuses os.replace while a scanner holds the target open; the write retries, then lands."""
    from nornir_buildmanager.operations.segmentationtraining import atomic_io

    real_replace = os.replace
    refusals = iter([PermissionError(13, "Access is denied"), PermissionError(13, "Access is denied")])

    def flaky_replace(src, dst):
        error = next(refusals, None)
        if error is not None:
            raise error
        real_replace(src, dst)

    monkeypatch.setattr(atomic_io.os, "replace", flaky_replace)
    monkeypatch.setattr(atomic_io.time, "sleep", lambda _s: None)
    target = tmp_path / "manifest.jsonl"
    target.write_text("old\n", encoding="utf-8")

    atomic_write_text(target, "new\n")

    assert target.read_text(encoding="utf-8") == "new\n"
    assert _leftover_temps(tmp_path) == []


def test_a_target_that_stays_locked_still_raises(monkeypatch, tmp_path: Path) -> None:
    from nornir_buildmanager.operations.segmentationtraining import atomic_io

    def locked(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(atomic_io.os, "replace", locked)
    monkeypatch.setattr(atomic_io.time, "sleep", lambda _s: None)

    import pytest

    with pytest.raises(PermissionError):
        atomic_write_text(tmp_path / "manifest.jsonl", "new\n")
