"""Manifest-row reads and member-stem splitting shared across the segmentation-training modules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from nornir_buildmanager.operations.segmentationtraining.atomic_io import (
    read_manifest_rows,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    split_member_stem,
)


def test_read_manifest_rows_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_manifest_rows(tmp_path) == []


def test_read_manifest_rows_skips_blank_lines_and_keeps_order(tmp_path: Path) -> None:
    (tmp_path / "manifest.jsonl").write_text('{"a": 1}\n\n   \n{"b": "\u00e9"}\n', encoding="utf-8")
    assert read_manifest_rows(tmp_path) == [{"a": 1}, {"b": "\u00e9"}]


def test_read_manifest_rows_malformed_line_raises(tmp_path: Path) -> None:
    (tmp_path / "manifest.jsonl").write_text('{"a": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_manifest_rows(tmp_path)


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("plain", ("plain", None)),
        ("key_12", ("key", 12)),
        ("a_b_7", ("a_b", 7)),
        ("key_abc", ("key_abc", None)),
        ("key_", ("key_", None)),
        ("key_-3", ("key_-3", None)),
        ("_5", ("", 5)),
        ("key_007", ("key", 7)),
    ],
)
def test_split_member_stem(stem: str, expected: tuple[str, int | None]) -> None:
    assert split_member_stem(stem) == expected
