"""``annotation_crops.ignore``: ``ignore.json`` round-trips, keeps its format, and survives a failed write."""

from __future__ import annotations

import errno
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
from annotation_crops import ignore as ignore_module
from annotation_crops.ignore import ignore_path, load_ignore_ids, save_ignore_ids
from hypothesis import given, settings
from hypothesis import strategies as st

_REAL_PATH_OPEN = Path.open


class _DiskFullWriter(io.TextIOWrapper):
    """A text file that is opened (and so truncated) but whose ``write`` fails like a full disk."""

    def write(self, text: str) -> int:
        raise OSError(errno.ENOSPC, "simulated disk full")


def _open_disk_full(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
    """``Path.open`` that fails every text write after the file is created or truncated."""
    if "w" not in mode:
        return _REAL_PATH_OPEN(self, mode, *args, **kwargs)
    raw = open(self, "wb")  # noqa: SIM115 - wrapped and closed by the caller's ``with``
    return _DiskFullWriter(raw, encoding="utf-8")


def _fail_replace(src: object, dst: object) -> None:
    raise OSError("simulated crash before the swap")


def _old_format(ids: set[int]) -> bytes:
    """Bytes the pre-atomic ``save_ignore_ids`` wrote: ``json.dumps`` of the sorted ids, utf-8."""
    return json.dumps(sorted(ids)).encode("utf-8")


@settings(max_examples=50, deadline=None)
@given(ids=st.sets(st.integers(min_value=-(2**63), max_value=2**63 - 1), max_size=40))
def test_round_trip_and_bytes_match_old_format(tmp_path_factory: pytest.TempPathFactory, ids: set[int]) -> None:
    crops = tmp_path_factory.mktemp("crops")
    save_ignore_ids(crops, ids)
    assert load_ignore_ids(crops) == ids
    assert ignore_path(crops).read_bytes() == _old_format(ids)
    assert sorted(p.name for p in crops.iterdir()) == ["ignore.json"]


def test_overwrite_replaces_previous_list(tmp_path: Path) -> None:
    save_ignore_ids(tmp_path, [3, 1, 2])
    save_ignore_ids(tmp_path, [9])
    assert load_ignore_ids(tmp_path) == {9}
    assert ignore_path(tmp_path).read_bytes() == b"[9]"


def test_failed_write_keeps_previous_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A write that dies after the file is opened must not leave a truncated ``ignore.json``.

    Before the atomic write, the target itself was truncated, so the next load saw an
    empty file and silently un-ignored every listed location.
    """
    save_ignore_ids(tmp_path, [5, 7])
    monkeypatch.setattr(Path, "open", _open_disk_full)
    with pytest.raises(OSError):
        save_ignore_ids(tmp_path, [5, 7, 11])
    monkeypatch.undo()
    assert ignore_path(tmp_path).read_bytes() == b"[5, 7]"
    assert load_ignore_ids(tmp_path) == {5, 7}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ignore.json"]


def test_failed_replace_keeps_previous_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_ignore_ids(tmp_path, [5, 7])
    monkeypatch.setattr(ignore_module.os, "replace", _fail_replace)
    with pytest.raises(OSError):
        save_ignore_ids(tmp_path, [1])
    monkeypatch.undo()
    assert load_ignore_ids(tmp_path) == {5, 7}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ignore.json"]


def test_temp_name_is_unique_per_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The temp file sits beside the target and carries the pid, so two processes never share it."""
    seen: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def _record_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        seen.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(ignore_module.os, "replace", _record_replace)
    save_ignore_ids(tmp_path, [1])
    monkeypatch.undo()
    assert len(seen) == 1
    temp, target = seen[0]
    assert target == ignore_path(tmp_path)
    assert temp.parent == target.parent
    assert temp.name.startswith("ignore.json.")
    assert temp.name.endswith(".tmp")
    assert str(os.getpid()) in temp.name
    assert load_ignore_ids(tmp_path) == {1}
