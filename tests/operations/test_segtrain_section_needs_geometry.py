"""Characterization of ingest._section_needs_geometry, the pass-B download decision per section."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from nornir_buildmanager.operations.segmentationtraining.ingest import (
    _section_needs_geometry,
    write_section_jsonl,
)
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord

Z = 7
STAMP = "2020-01-01T00:00:00+00:00"


def _record(record_id: int, *, stamp: str = STAMP, type_code: int | None = 6) -> LocationRecord:
    return LocationRecord(
        id=record_id, z=Z, wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))", parent_id=10, off_edge=False,
        last_modified=datetime.fromisoformat(stamp), type_id=1, type_name="Cell",
        structure_label="soma", type_code=type_code,
    )


def _entity(record_id: int, *, stamp: str = STAMP, off_edge: bool | None = False) -> dict[str, Any]:
    entity: dict[str, Any] = {"ID": record_id, "Z": Z, "LastModified": stamp}
    if off_edge is not None:
        entity["OffEdge"] = off_edge
    return entity


@pytest.fixture
def output(tmp_path: Path) -> Path:
    return tmp_path / "out"


def _needs(output: Path, entities: list[dict[str, Any]], *, include_off_edge: bool = False) -> bool:
    return _section_needs_geometry(output, Z, entities, include_off_edge=include_off_edge)


def test_nothing_incoming_nothing_existing_is_clean(output: Path) -> None:
    assert _needs(output, []) is False


def test_incoming_without_existing_spill_needs_geometry(output: Path) -> None:
    assert _needs(output, [_entity(1)]) is True


def test_existing_without_incoming_needs_geometry(output: Path) -> None:
    write_section_jsonl(output, [_record(1)])
    assert _needs(output, []) is True


def test_identical_ids_and_timestamps_is_clean(output: Path) -> None:
    write_section_jsonl(output, [_record(1), _record(2)])
    assert _needs(output, [_entity(2), _entity(1)]) is False


def test_existing_record_without_type_code_needs_geometry(output: Path) -> None:
    write_section_jsonl(output, [_record(1), _record(2, type_code=None)])
    assert _needs(output, [_entity(1), _entity(2)]) is True


def test_extra_incoming_id_needs_geometry(output: Path) -> None:
    write_section_jsonl(output, [_record(1)])
    assert _needs(output, [_entity(1), _entity(2)]) is True


def test_missing_incoming_id_needs_geometry(output: Path) -> None:
    write_section_jsonl(output, [_record(1), _record(2)])
    assert _needs(output, [_entity(1)]) is True


def test_same_count_different_ids_needs_geometry(output: Path) -> None:
    write_section_jsonl(output, [_record(1)])
    assert _needs(output, [_entity(2)]) is True


def test_changed_last_modified_needs_geometry(output: Path) -> None:
    write_section_jsonl(output, [_record(1), _record(2)])
    assert _needs(output, [_entity(1), _entity(2, stamp="2021-06-01T00:00:00+00:00")]) is True


def test_equivalent_timestamp_spellings_are_clean(output: Path) -> None:
    write_section_jsonl(output, [_record(1)])
    assert _needs(output, [_entity(1, stamp="2020-01-01T00:00:00Z")]) is False
    assert _needs(output, [_entity(1, stamp="/Date(1577836800000)/")]) is False


def test_off_edge_entities_ignored_unless_included(output: Path) -> None:
    write_section_jsonl(output, [_record(1)])
    entities = [_entity(1), _entity(2, off_edge=True)]
    assert _needs(output, entities, include_off_edge=False) is False
    assert _needs(output, entities, include_off_edge=True) is True


def test_only_off_edge_incoming_with_no_spill_is_clean_when_excluded(output: Path) -> None:
    entities = [_entity(1, off_edge=True)]
    assert _needs(output, entities, include_off_edge=False) is False
    assert _needs(output, entities, include_off_edge=True) is True


def test_missing_off_edge_key_counts_as_on_edge(output: Path) -> None:
    write_section_jsonl(output, [_record(1)])
    assert _needs(output, [_entity(1, off_edge=None)], include_off_edge=False) is False
