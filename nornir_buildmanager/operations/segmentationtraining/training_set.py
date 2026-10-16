"""``training_set.json``: the metadata sidecar of an exported training set.

A set is named ``v<SET>`` or ``v<SET>.<REVISION>`` (``v2``, ``v2.1``). A revision is a repopulated or tweaked set made with
the same creation rules; new rules are a new set number. Models have their own version numbers, unrelated to the set's
(see the ``Training-set-naming`` rule). ``ExportTrainingData`` writes one sidecar into its output folder, next to
``manifest.jsonl``; the folder is later copied into the set's ``<set>/<VOLUME>/`` folder, so the sidecar travels with the
volume's data.

The sidecar records the exporter parameters that define a sample (``creation_rules``) and their fingerprint. The
fingerprint is what makes "a revision has the same rules" checkable: a set number keeps one fingerprint, and the exporter
refuses to export under a set number whose rules have changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    geometry_version_for,
)

SIDECAR_FILENAME = "training_set.json"
SCHEMA_VERSION = 1
SET_VERSION_RE = re.compile(r"^v(\d+)(?:\.([1-9]\d*))?$")
"""``v2`` or ``v2.1``. A revision starts at 1, so ``v2.0`` is just ``v2``. The old letter style (``v1b``) is not accepted
for a new set."""

DEFAULT_MAX_TEXTURE = 1024


def parse_set_version(text: str, *, what: str = "set version") -> tuple[int, int]:
    """``(set number, revision)`` of ``v2`` / ``v2.1``.

    Raises:
        NornirUserException: ``text`` is not of that form.
    """
    match = SET_VERSION_RE.match(text or "")
    if match is None:
        raise NornirUserException(
            f"{what} {text!r} must look like v2 or v2.1 (v<SET> or v<SET>.<REVISION>, the revision starting at 1)"
        )
    return int(match.group(1)), int(match.group(2) or 0)


def creation_rules(
    *,
    pad: float | None = None,
    downsample: int | None = None,
    exporter: str | None = None,
    max_texture: int | None = None,
    min_process_pixels: int | None = None,
    include_off_edge: bool = False,
    channels: str | None = None,
    filters: str | None = None,
    structure_type_ids: list[int] | None = None,
    odata_filter: str | None = None,
) -> dict[str, Any]:
    """The exporter parameters that decide which samples exist and what each looks like.

    Run-time settings (worker counts, scratch folders, QA overlays, force/update modes) and the annotation source are
    not rules: changing them does not change what a sample is.
    """
    exporter_name = (exporter or "tiled").strip().lower()
    return {
        "pad": 1.0 if pad is None else float(pad),
        "downsample": 1 if downsample is None else int(downsample),
        "exporter": exporter_name,
        "geometry_version": geometry_version_for(exporter_name),
        "max_texture": int(max_texture) if max_texture else DEFAULT_MAX_TEXTURE,
        "min_process_pixels": 16 if min_process_pixels is None else int(min_process_pixels),
        "include_off_edge": bool(include_off_edge),
        "channels": channels or "TEM",
        "filters": filters or "Leveled",
        "structure_type_ids": None if structure_type_ids is None else sorted(int(v) for v in structure_type_ids),
        "odata_filter": odata_filter or None,
    }


def rules_fingerprint(rules: Mapping[str, Any]) -> str:
    """12 hex digits of the digest of ``rules``; equal rules give equal fingerprints whatever their key order."""
    encoded = json.dumps(dict(rules), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


def read_training_set_info(output_path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """The sidecar in ``output_path``, or None when there is none or it cannot be read."""
    try:
        record = json.loads((Path(output_path) / SIDECAR_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _generator() -> dict[str, str]:
    import importlib.metadata

    for name in ("nornir-buildmanager", "nornir_buildmanager"):
        try:
            return {"name": "nornir-buildmanager", "version": importlib.metadata.version(name)}
        except importlib.metadata.PackageNotFoundError:
            continue
    return {"name": "nornir-buildmanager", "version": "unknown"}


def plan_training_set_info(
    output_path: str | os.PathLike[str],
    *,
    rules: Mapping[str, Any],
    source: Mapping[str, Any] | None = None,
    set_version: str | None = None,
    parent: str | None = None,
    description: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The sidecar this export should leave in ``output_path``. Writes nothing.

    ``set_version`` names the set the export belongs to; omitted, the set already named in the folder is kept (None
    for a folder that was never versioned). ``parent`` is the set it revises or derives from; a revision (``v2.1``)
    defaults to its set (``v2``). ``description`` is free text for an operator; omitted, the existing one is kept.

    Raises:
        NornirUserException: A name is malformed, the set number would go backwards, a parent is not an earlier set,
            or the creation rules changed since the sidecar's set number was started (a set keeps its rules; a change
            is a new set number).
    """
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
        "generator": _generator(),
        "creation_rules": dict(rules),
        "creation_rules_fingerprint": fingerprint,
        "source": dict(source or {}),
        "description": text,
    }


def write_training_set_info(output_path: str | os.PathLike[str], record: Mapping[str, Any]) -> Path:
    """Write ``record`` as ``training_set.json`` in ``output_path``, atomically (a temp file, then a replace)."""
    folder = Path(output_path)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / SIDECAR_FILENAME
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(dict(record), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return path


def _plan_from_arguments(**kwargs: Any) -> dict[str, Any] | None:
    """The record the pipeline arguments describe, or None for a run that exports nothing (``-Cleanup``, ``-Repair``)."""
    if kwargs.get("Cleanup") or kwargs.get("Repair"):
        return None
    output_path = kwargs.get("OutputPath")
    if not output_path:
        raise NornirUserException("ExportTrainingData needs an output folder (-Output)")
    rules = creation_rules(
        pad=kwargs.get("Pad"),
        downsample=kwargs.get("Downsample"),
        exporter=kwargs.get("Exporter"),
        max_texture=kwargs.get("MaxTexture"),
        min_process_pixels=kwargs.get("MinProcessPixels"),
        include_off_edge=bool(kwargs.get("IncludeOffEdge")),
        channels=kwargs.get("Channels"),
        filters=kwargs.get("Filters"),
        structure_type_ids=kwargs.get("StructureTypeId"),
        odata_filter=kwargs.get("ODataFilter"),
    )
    source = {
        key: kwargs[arg]
        for key, arg in (("odata", "OData"), ("geometries", "Geometries"), ("viking_xml", "VikingXML"))
        if kwargs.get(arg)
    }
    return plan_training_set_info(
        output_path,
        rules=rules,
        source=source,
        set_version=kwargs.get("SetVersion"),
        parent=kwargs.get("ParentSet"),
        description=kwargs.get("SetDescription"),
    )


def CheckTrainingSet(**kwargs: Any) -> None:
    """Pipeline entry, first step of ExportTrainingData: refuse a bad ``-SetVersion`` or changed rules before any export.

    Writes nothing. Returns None.
    """
    _plan_from_arguments(**kwargs)


def WriteTrainingSetInfo(**kwargs: Any) -> None:
    """Pipeline entry, last step of ExportTrainingData: write ``training_set.json`` next to the data. Returns None."""
    record = _plan_from_arguments(**kwargs)
    if record is not None:
        write_training_set_info(kwargs["OutputPath"], record)
