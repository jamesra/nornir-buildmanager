"""training_set.json: set naming, creation-rule fingerprints, and what ExportTrainingData writes next to its data."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import nornir_buildmanager
import pytest
from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations import segmentationtraining
from nornir_buildmanager.operations.segmentationtraining.training_set import (
    SIDECAR_FILENAME,
    CheckTrainingSet,
    WriteTrainingSetInfo,
    creation_rules,
    parse_set_version,
    plan_training_set_info,
    read_training_set_info,
    rules_fingerprint,
    write_training_set_info,
)

PIPELINES = Path(nornir_buildmanager.__file__).parent / "config" / "Pipelines.xml"
T0 = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


def export(tmp_path: Path, **overrides) -> dict:
    """What the pipeline passes to both steps, with ``overrides`` on top."""
    arguments = {
        "OutputPath": str(tmp_path),
        "Pad": 1.0,
        "Downsample": 1,
        "Exporter": "tiled",
        "Channels": "TEM",
        "Filters": "Leveled",
        "StructureTypeId": [1, 3],
        "OData": "http://lab.example/RC1/OData",
    }
    arguments.update(overrides)
    return arguments


def sidecar(tmp_path: Path) -> dict:
    return json.loads((tmp_path / SIDECAR_FILENAME).read_text(encoding="utf-8"))


@pytest.mark.parametrize("text, expected", [("v2", (2, 0)), ("v2.1", (2, 1)), ("v10", (10, 0)), ("v2.10", (2, 10))])
def test_set_names_parse(text: str, expected: tuple[int, int]) -> None:
    assert parse_set_version(text) == expected


@pytest.mark.parametrize("text", ["v2.0", "v2.01", "v2.", "v2.1.1", "v1b", "2", "vx", "current", "", "v2.1b"])
def test_other_names_are_refused(text: str) -> None:
    with pytest.raises(NornirUserException, match="v2 or v2.1"):
        parse_set_version(text)


def test_revisions_order_numerically() -> None:
    names = ["v3", "v2.10", "v2", "v2.2", "v2.1"]
    assert sorted(names, key=parse_set_version) == ["v2", "v2.1", "v2.2", "v2.10", "v3"]


def test_the_fingerprint_follows_the_rules_not_the_key_order() -> None:
    rules = creation_rules(pad=1, downsample=1)
    assert rules_fingerprint(rules) == rules_fingerprint(dict(reversed(list(rules.items()))))
    assert len(rules_fingerprint(rules)) == 12
    assert rules_fingerprint(rules) != rules_fingerprint(creation_rules(pad=2, downsample=1))


def test_run_time_settings_and_the_source_are_not_creation_rules(tmp_path: Path) -> None:
    first = plan_training_set_info(
        tmp_path, rules=creation_rules(), source={"odata": "http://a"}, set_version="v2", now=T0
    )
    second = plan_training_set_info(
        tmp_path, rules=creation_rules(), source={"odata": "http://b"}, set_version="v2", now=T0
    )
    assert first["creation_rules_fingerprint"] == second["creation_rules_fingerprint"]
    assert "workers" not in first["creation_rules"] and "overlay" not in first["creation_rules"]


def test_the_rules_defaults_match_the_pipelines_own_defaults() -> None:
    root = ET.parse(PIPELINES).getroot()
    pipeline = next(p for p in root.iter("Pipeline") if p.get("Name") == "ExportTrainingData")
    defaults = {a.get("dest"): a.get("default") for a in pipeline.iter("Argument")}
    rules = creation_rules()
    assert rules["channels"] == defaults["Channels"]
    assert rules["filters"] == defaults["Filters"]
    assert rules["exporter"] == defaults["Exporter"]
    assert rules["downsample"] == int(defaults["Downsample"])
    assert rules["pad"] == float(defaults["Pad"])
    assert rules["min_process_pixels"] == int(defaults["MinProcessPixels"])


def test_the_first_export_writes_the_sidecar_next_to_the_data(tmp_path: Path) -> None:
    WriteTrainingSetInfo(**export(tmp_path, SetVersion="v2", SetDescription="First set"))

    record = sidecar(tmp_path)
    assert record["schema_version"] == 1
    assert record["set_version"] == "v2"
    assert record["parent"] is None
    assert record["description"] == "First set"
    assert record["creation_rules"]["structure_type_ids"] == [1, 3]
    assert record["creation_rules_fingerprint"] == rules_fingerprint(record["creation_rules"])
    assert record["source"] == {"odata": "http://lab.example/RC1/OData"}
    assert record["generator"]["name"] == "nornir-buildmanager"
    assert not list(tmp_path.glob("*.tmp"))  # written through a temp file that was replaced


def test_a_revision_defaults_its_parent_to_its_set(tmp_path: Path) -> None:
    WriteTrainingSetInfo(**export(tmp_path, SetVersion="v2.1"))

    assert sidecar(tmp_path)["parent"] == "v2"


def test_an_export_with_no_set_version_leaves_it_null_and_a_later_one_stamps_it(tmp_path: Path) -> None:
    WriteTrainingSetInfo(**export(tmp_path))
    assert sidecar(tmp_path)["set_version"] is None

    WriteTrainingSetInfo(**export(tmp_path, SetVersion="v3"))
    assert sidecar(tmp_path)["set_version"] == "v3"


def test_a_re_export_keeps_the_set_the_creation_time_and_the_operators_description(tmp_path: Path) -> None:
    write_training_set_info(tmp_path, plan_training_set_info(tmp_path, rules=creation_rules(), set_version="v2", now=T0))
    edited = sidecar(tmp_path)
    edited["description"] = "Re-picked the ignore list."
    (tmp_path / SIDECAR_FILENAME).write_text(json.dumps(edited), encoding="utf-8")

    again = plan_training_set_info(tmp_path, rules=creation_rules(), now=T1)

    assert again["set_version"] == "v2"  # kept from the folder when -SetVersion is omitted
    assert again["created_at"] == edited["created_at"]
    assert again["updated_at"] != edited["created_at"]
    assert again["description"] == "Re-picked the ignore list."
    assert plan_training_set_info(tmp_path, rules=creation_rules(), description="New text")["description"] == "New text"


def test_changed_rules_under_the_same_set_number_are_refused(tmp_path: Path) -> None:
    WriteTrainingSetInfo(**export(tmp_path, SetVersion="v2"))

    with pytest.raises(NornirUserException, match="new set number"):
        CheckTrainingSet(**export(tmp_path, SetVersion="v2.1", Pad=3.0))  # a revision keeps the rules


def test_changed_rules_under_a_new_set_number_are_allowed(tmp_path: Path) -> None:
    WriteTrainingSetInfo(**export(tmp_path, SetVersion="v2"))
    before = sidecar(tmp_path)["creation_rules_fingerprint"]

    WriteTrainingSetInfo(**export(tmp_path, SetVersion="v3", ParentSet="v2", Pad=3.0))

    record = sidecar(tmp_path)
    assert record["set_version"] == "v3" and record["parent"] == "v2"
    assert record["creation_rules_fingerprint"] != before
    assert record["created_at"] != ""


def test_the_same_rules_under_a_revision_are_allowed(tmp_path: Path) -> None:
    WriteTrainingSetInfo(**export(tmp_path, SetVersion="v2"))
    first = sidecar(tmp_path)

    WriteTrainingSetInfo(**export(tmp_path, SetVersion="v2.1"))

    assert sidecar(tmp_path)["creation_rules_fingerprint"] == first["creation_rules_fingerprint"]


def test_a_set_number_does_not_go_backwards_in_a_folder(tmp_path: Path) -> None:
    WriteTrainingSetInfo(**export(tmp_path, SetVersion="v3"))

    with pytest.raises(NornirUserException, match="only goes forward"):
        CheckTrainingSet(**export(tmp_path, SetVersion="v2"))


def test_a_bad_name_or_parent_is_refused_before_anything_is_exported(tmp_path: Path) -> None:
    with pytest.raises(NornirUserException, match="v2 or v2.1"):
        CheckTrainingSet(**export(tmp_path, SetVersion="v1b"))
    with pytest.raises(NornirUserException, match="earlier set"):
        CheckTrainingSet(**export(tmp_path, SetVersion="v2", ParentSet="v2"))
    with pytest.raises(NornirUserException, match="needs -SetVersion"):
        CheckTrainingSet(**export(tmp_path, ParentSet="v1"))
    assert not (tmp_path / SIDECAR_FILENAME).exists()


def test_the_check_step_writes_nothing(tmp_path: Path) -> None:
    CheckTrainingSet(**export(tmp_path, SetVersion="v2"))

    assert read_training_set_info(tmp_path) is None


def test_a_cleanup_or_repair_run_leaves_the_sidecar_alone(tmp_path: Path) -> None:
    WriteTrainingSetInfo(**export(tmp_path, SetVersion="v2"))
    before = (tmp_path / SIDECAR_FILENAME).read_bytes()

    WriteTrainingSetInfo(**export(tmp_path, SetVersion="v9", Cleanup=True))
    WriteTrainingSetInfo(**export(tmp_path, Repair=True))

    assert (tmp_path / SIDECAR_FILENAME).read_bytes() == before


def test_an_unreadable_sidecar_is_treated_as_absent(tmp_path: Path) -> None:
    (tmp_path / SIDECAR_FILENAME).write_text("{not json", encoding="utf-8")

    WriteTrainingSetInfo(**export(tmp_path, SetVersion="v2"))

    assert sidecar(tmp_path)["set_version"] == "v2"


def test_a_missing_output_folder_is_a_usage_error() -> None:
    with pytest.raises(NornirUserException, match="-Output"):
        CheckTrainingSet(Pad=1.0)


def test_the_steps_are_exposed_to_the_pipeline_by_name() -> None:
    assert segmentationtraining.CheckTrainingSet is CheckTrainingSet
    assert segmentationtraining.WriteTrainingSetInfo is WriteTrainingSetInfo


def test_the_pipeline_checks_first_and_writes_last_and_takes_the_set_arguments() -> None:
    root = ET.parse(PIPELINES).getroot()
    pipeline = next(p for p in root.iter("Pipeline") if p.get("Name") == "ExportTrainingData")
    calls = [c.get("Function") for c in pipeline.iter("PythonCall")]
    assert calls[0] == "segmentationtraining.CheckTrainingSet"
    assert calls[-1] == "segmentationtraining.WriteTrainingSetInfo"
    flags = {a.get("flag") for a in pipeline.iter("Argument")}
    assert {"-SetVersion", "-ParentSet", "-SetDescription"} <= flags
    # Every argument either step reads is one the pipeline declares.
    declared = {a.get("dest") for a in pipeline.iter("Argument")}
    for call in pipeline.iter("PythonCall"):
        if call.get("Function") in ("segmentationtraining.CheckTrainingSet", "segmentationtraining.WriteTrainingSetInfo"):
            used = {value[1:] for value in call.attrib.values() if value.startswith("#")}
            assert used <= declared, used - declared
