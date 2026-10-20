"""Characterization of ``plan_training_set_info`` against its original single-function body.

For generated folder sidecars (absent, unreadable, letter-style, revisions, other types in each field) and generated
``-SetVersion`` / ``-ParentSet`` / ``-SetDescription`` values, the planned record or the ``NornirUserException`` text
must equal what the original implementation (copied below as ``_reference_plan``) gives.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hypothesis import example, given, settings
from hypothesis import strategies as st

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.segmentationtraining import training_set
from nornir_buildmanager.operations.segmentationtraining.training_set import (
    SCHEMA_VERSION,
    SET_VERSION_RE,
    SIDECAR_FILENAME,
    creation_rules,
    parse_set_version,
    plan_training_set_info,
    read_training_set_info,
    rules_fingerprint,
)

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
RULES_A = creation_rules()
RULES_B = creation_rules(pad=3.0)
MISSING = object()


def _reference_plan(
    output_path: str,
    *,
    rules: Mapping[str, Any],
    source: Mapping[str, Any] | None = None,
    set_version: str | None = None,
    parent: str | None = None,
    description: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The body of ``plan_training_set_info`` before it was split into helpers (2026-10-19)."""
    existing = read_training_set_info(output_path)
    existing_version = existing.get("set_version") if existing else None
    if existing_version is not None and not isinstance(existing_version, str):
        existing_version = None
    fingerprint = rules_fingerprint(rules)

    wanted = set_version if set_version else existing_version
    if set_version:
        new_number = parse_set_version(set_version)
        if (
            existing_version
            and SET_VERSION_RE.match(existing_version)
            and new_number < parse_set_version(existing_version, what="existing set version")
        ):
            raise NornirUserException(
                f"{output_path} already holds set {existing_version}; a set number only goes forward, "
                f"so {set_version} cannot be written there"
            )
    if parent:
        if wanted is None:
            raise NornirUserException("-ParentSet needs -SetVersion: a parent is the set that one revises")
        if parse_set_version(parent, what="parent set") >= parse_set_version(wanted):
            raise NornirUserException(f"the parent set {parent} must be an earlier set than {wanted}")

    old_fingerprint = existing.get("creation_rules_fingerprint") if existing else None
    if wanted and existing_version and old_fingerprint and old_fingerprint != fingerprint:
        same_number = (
            SET_VERSION_RE.match(existing_version) is not None
            and parse_set_version(wanted)[0] == parse_set_version(existing_version)[0]
        )
        if same_number:
            raise NornirUserException(
                f"The creation rules changed since set {existing_version} was started in {output_path} "
                f"(fingerprint {old_fingerprint}, now {fingerprint}). A set number keeps one set of rules; "
                f"use a new set number (-SetVersion v{parse_set_version(wanted)[0] + 1}) or restore the rules."
            )

    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    same_set = existing is not None and existing_version == wanted
    if parent:
        resolved_parent: str | None = parent
    elif wanted and parse_set_version(wanted)[1] > 0:
        resolved_parent = f"v{parse_set_version(wanted)[0]}"
    elif same_set and isinstance(existing.get("parent"), str):  # type: ignore[union-attr]
        resolved_parent = existing["parent"]  # type: ignore[index]
    else:
        resolved_parent = None
    if description is not None:
        text = description
    else:
        kept = existing.get("description") if existing else None
        text = kept if isinstance(kept, str) else ""
    created = existing.get("created_at") if same_set and existing else None
    return {
        "schema_version": SCHEMA_VERSION,
        "set_version": wanted,
        "parent": resolved_parent,
        "created_at": created if isinstance(created, str) else stamp,
        "updated_at": stamp,
        "generator": training_set._generator(),
        "creation_rules": dict(rules),
        "creation_rules_fingerprint": fingerprint,
        "source": dict(source or {}),
        "description": text,
    }


_NAMES = ["v1", "v2", "v2.1", "v2.2", "v3", "v10", "v1b", "v2.0", "", "bad"]


def _field(values: list[Any]) -> st.SearchStrategy[Any]:
    return st.sampled_from([MISSING, None, *values])


_SIDECARS = st.one_of(
    st.just(None),  # no file
    st.just("{not json"),  # unreadable
    st.just("[1, 2]"),  # not an object
    st.fixed_dictionaries(
        {
            "set_version": _field([*_NAMES, 5]),
            "creation_rules_fingerprint": _field(["", rules_fingerprint(RULES_A), rules_fingerprint(RULES_B)]),
            "parent": _field(["v1", "v2", 3]),
            "description": _field(["", "kept text", 7]),
            "created_at": _field(["2026-01-01T00:00:00+00:00", 1]),
        }
    ),
)


def _outcome(plan, folder: str, **kwargs: Any) -> tuple[Any, ...]:
    try:
        return ("ok", plan(folder, **kwargs))
    except NornirUserException as error:
        return ("raised", str(error))


@settings(max_examples=1500, deadline=None)
@given(
    sidecar=_SIDECARS,
    set_version=st.sampled_from([None, *_NAMES]),
    parent=st.sampled_from([None, "", "v1", "v2", "v2.1", "v3", "bad"]),
    description=st.sampled_from([None, "", "new text"]),
    rules=st.sampled_from([RULES_A, RULES_B]),
)
@example(sidecar={"set_version": "v2", "creation_rules_fingerprint": rules_fingerprint(RULES_A)}, set_version="v2.1",
         parent=None, description=None, rules=RULES_B)  # changed rules under a revision
@example(sidecar={"set_version": "v3"}, set_version="v2", parent=None, description=None, rules=RULES_A)  # backwards
@example(sidecar={"set_version": "v1b"}, set_version=None, parent=None, description=None, rules=RULES_A)  # letter name
@example(sidecar={"set_version": ""}, set_version=None, parent="v1", description=None, rules=RULES_A)
@example(sidecar=None, set_version="", parent="v1", description=None, rules=RULES_A)  # parent without a set
def test_plan_matches_the_original_body(
    sidecar: Any, set_version: str | None, parent: str | None, description: str | None, rules: dict[str, Any]
) -> None:
    with tempfile.TemporaryDirectory() as folder:
        if isinstance(sidecar, str):
            (Path(folder) / SIDECAR_FILENAME).write_text(sidecar, encoding="utf-8")
        elif isinstance(sidecar, dict):
            record = {key: value for key, value in sidecar.items() if value is not MISSING}
            (Path(folder) / SIDECAR_FILENAME).write_text(json.dumps(record), encoding="utf-8")
        kwargs = {
            "rules": rules,
            "source": {"odata": "http://a"},
            "set_version": set_version,
            "parent": parent,
            "description": description,
            "now": NOW,
        }

        assert _outcome(plan_training_set_info, folder, **kwargs) == _outcome(_reference_plan, folder, **kwargs)


def test_a_revision_under_changed_rules_names_the_next_set_number(tmp_path: Path) -> None:
    """The refusal suggests the next set number, computed from the wanted set, not the folder's revision."""
    (tmp_path / SIDECAR_FILENAME).write_text(
        json.dumps({"set_version": "v2.1", "creation_rules_fingerprint": rules_fingerprint(RULES_A)}), encoding="utf-8"
    )

    try:
        plan_training_set_info(tmp_path, rules=RULES_B, set_version="v2.2", now=NOW)
    except NornirUserException as error:
        message = str(error)
    else:
        raise AssertionError("changed rules under the same set number were accepted")

    assert message == (
        f"The creation rules changed since set v2.1 was started in {tmp_path} "
        f"(fingerprint {rules_fingerprint(RULES_A)}, now {rules_fingerprint(RULES_B)}). A set number keeps one set "
        "of rules; use a new set number (-SetVersion v3) or restore the rules."
    )


def test_a_folder_holding_a_letter_style_set_needs_an_explicit_set_version(tmp_path: Path) -> None:
    """``v1b`` in the sidecar is kept as the wanted set when -SetVersion is omitted, and then refused as malformed."""
    (tmp_path / SIDECAR_FILENAME).write_text(json.dumps({"set_version": "v1b"}), encoding="utf-8")

    try:
        plan_training_set_info(tmp_path, rules=RULES_A, now=NOW)
    except NornirUserException as error:
        assert "'v1b' must look like v2 or v2.1" in str(error)
    else:
        raise AssertionError("a letter-style set name was accepted")
    assert plan_training_set_info(tmp_path, rules=RULES_A, set_version="v2", now=NOW)["parent"] is None
