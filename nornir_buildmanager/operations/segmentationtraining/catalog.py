"""Per-volume AnnotationCrops sqlite catalog and ignore-list mask moves."""

from __future__ import annotations

import errno
import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from nornir_buildmanager.operations.segmentationtraining.ingest import load_section_records
from nornir_buildmanager.operations.segmentationtraining.progress import (
    GALLERY_LABEL,
    GALLERY_TRACK_ID,
    IterateProgressReporter,
)
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord
from nornir_buildmanager.operations.segmentationtraining.stitch import (
    crop_image_filename,
    resolve_crop_image,
)

SAM2_COLUMNS = (
    "sam2PredIou",
    "sam2ObjectScore",
    "sam2Stability",
    "sam2GtIou",
    "sam2Checkpoint",
    "sam2ScoredAt",
)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS locations (
    location_id INTEGER NOT NULL,
    z INTEGER NOT NULL,
    structure_id INTEGER,
    structure_label TEXT,
    type_id INTEGER,
    type_name TEXT,
    radius REAL,
    image_key TEXT NOT NULL,
    image_relpath TEXT,
    mask_relpath TEXT,
    ignored INTEGER NOT NULL DEFAULT 0,
    sam2PredIou REAL,
    sam2ObjectScore REAL,
    sam2Stability REAL,
    sam2GtIou REAL,
    sam2Checkpoint TEXT,
    sam2ScoredAt TEXT,
    PRIMARY KEY (location_id, image_key)
)
"""


def sqlite_path(output_path: str | os.PathLike[str]) -> Path:
    """Return `{Output}/annotation_crops.sqlite`."""
    return Path(output_path) / "annotation_crops.sqlite"


def ignore_path(output_path: str | os.PathLike[str]) -> Path:
    """Return `{Output}/ignore.json`."""
    return Path(output_path) / "ignore.json"


def connect(output_path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open (and create) the per-volume catalog database."""
    path = sqlite_path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    _ensure_locations_schema(connection)
    return connection


def _ensure_locations_schema(connection: sqlite3.Connection) -> None:
    """Create locations, rename JPEG-era columns, and key rows by image as well as location."""
    connection.execute(_CREATE_SQL)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(locations)")}
    if "jpeg_relpath" in columns and "image_relpath" not in columns:
        connection.execute("ALTER TABLE locations RENAME COLUMN jpeg_relpath TO image_relpath")
        connection.commit()
    if _primary_key_columns(connection) != ["location_id", "image_key"]:
        _migrate_location_primary_key(connection)


def _primary_key_columns(connection: sqlite3.Connection) -> list[str]:
    """Primary-key column names in key order."""
    rows = list(connection.execute("PRAGMA table_info(locations)"))
    keyed = sorted((int(row[5]), str(row[1])) for row in rows if int(row[5]) > 0)
    return [name for _order, name in keyed]


def _migrate_location_primary_key(connection: sqlite3.Connection) -> None:
    """Rebuild locations so one location can have a row per training image."""
    connection.execute("ALTER TABLE locations RENAME TO locations_old")
    connection.execute(_CREATE_SQL)
    old_cols = {str(row[1]) for row in connection.execute("PRAGMA table_info(locations_old)")}
    new_cols = [str(row[1]) for row in connection.execute("PRAGMA table_info(locations)")]
    shared = [name for name in new_cols if name in old_cols]
    if shared:
        quoted = ", ".join(shared)
        connection.execute(
            f"INSERT INTO locations ({quoted}) SELECT {quoted} FROM locations_old"
        )
    connection.execute("DROP TABLE locations_old")
    connection.commit()


def load_ignore_ids(output_path: str | os.PathLike[str]) -> set[int]:
    """Load ignored location ids from `ignore.json` (a JSON array)."""
    path = ignore_path(output_path)
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return set()
    ids: set[int] = set()
    for item in payload:
        try:
            ids.add(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def save_ignore_ids(output_path: str | os.PathLike[str], ids: Iterable[int]) -> None:
    """Write `ignore.json` as a sorted JSON array of location ids."""
    path = ignore_path(output_path)
    ordered = sorted({int(item) for item in ids})
    path.write_text(json.dumps(ordered), encoding="utf-8")


def equivalent_radius(area: float | None, stored: float | None) -> float | None:
    """Prefer stored Viking Radius; else `sqrt(area / pi)` from the mask."""
    if stored is not None:
        return float(stored)
    if area is None or area <= 0:
        return None
    return math.sqrt(float(area) / math.pi)


def apply_ignore_moves(output_path: str | os.PathLike[str]) -> int:
    """Move ignored masks into `ignored/`, replacing any file already there.

    Returns the number of files moved.
    """
    output = Path(output_path)
    ignored_dir = output / "ignored"
    moved = 0
    for location_id in load_ignore_ids(output):
        sources = _find_masks(output / "masks", location_id)
        if not sources:
            continue
        ignored_dir.mkdir(parents=True, exist_ok=True)
        for source in sources:
            destination = ignored_dir / source.name
            if destination.is_file():
                destination.unlink()
            source.replace(destination)
            moved += 1
    return moved


def ignore_location(output_path: str | os.PathLike[str], location_id: int) -> bool:
    """Add *location_id* to ignore.json and move every mask for it. Returns True if listed."""
    ids = load_ignore_ids(output_path)
    ids.add(int(location_id))
    save_ignore_ids(output_path, ids)
    apply_ignore_moves(output_path)
    _set_ignored_flag(output_path, int(location_id), ignored=True)
    return True


def restore_location(output_path: str | os.PathLike[str], location_id: int) -> bool:
    """Remove *location_id* from ignore.json and move every mask back to `masks/`."""
    location_id = int(location_id)
    ids = load_ignore_ids(output_path)
    ids.discard(location_id)
    save_ignore_ids(output_path, ids)
    sources = _find_masks(Path(output_path) / "ignored", location_id)
    if sources:
        masks = Path(output_path) / "masks"
        masks.mkdir(parents=True, exist_ok=True)
        for source in sources:
            destination = masks / source.name
            if destination.is_file():
                destination.unlink()
            source.replace(destination)
    _set_ignored_flag(output_path, location_id, ignored=False)
    return True


def load_sam2_by_id(output_path: str | os.PathLike[str]) -> dict[tuple[int, str], dict[str, Any]]:
    """Return existing SAM2 score columns keyed by location id and image key."""
    path = sqlite_path(output_path)
    if not path.is_file():
        return {}
    connection = connect(output_path)
    try:
        rows = connection.execute(
            "SELECT location_id, image_key, " + ", ".join(SAM2_COLUMNS) + " FROM locations"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        connection.close()
    preserved: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        payload = {name: row[name] for name in SAM2_COLUMNS}
        if any(value is not None for value in payload.values()):
            preserved[(int(row["location_id"]), str(row["image_key"]))] = payload
    return preserved


def upsert_catalog(
    output_path: str | os.PathLike[str],
    location_ids: Iterable[int] | None = None,
    *,
    z: int | None = None,
    image_keys: Iterable[str] | None = None,
) -> int:
    """Insert or update catalog rows without wiping SAM2 score columns.

    When *location_ids* is set, only those ids are written. When *z* and/or
    *image_keys* are set, only matching SA-1B JSON / mask files are read so a
    long volume export does not reopen every crop on every section.
    Existing ``sam2*`` values stay on conflict; new rows get NULL scores.
    """
    output = Path(output_path)
    apply_ignore_moves(output)
    ignored_ids = load_ignore_ids(output)
    records_by_id = _records_by_id(output, z=z)
    keys = None if image_keys is None else {str(key) for key in image_keys}
    rows = _collect_location_rows(
        output,
        records_by_id,
        ignored_ids,
        z=z,
        image_keys=keys,
    )
    if location_ids is not None:
        wanted = {int(item) for item in location_ids}
        rows = [row for row in rows if int(row["location_id"]) in wanted]
    connection = connect(output)
    try:
        for row in rows:
            _upsert_row(connection, row)
        connection.commit()
    finally:
        connection.close()
    return len(rows)


def prune_catalog_section(
    output_path: str | os.PathLike[str],
    z: int,
    keep_ids: set[int],
) -> int:
    """Delete catalog rows for section *z* whose location id is not in *keep_ids*."""
    if not sqlite_path(output_path).is_file():
        return 0
    connection = connect(output_path)
    try:
        if keep_ids:
            placeholders = ", ".join("?" for _ in keep_ids)
            cursor = connection.execute(
                f"DELETE FROM locations WHERE z = ? AND location_id NOT IN ({placeholders})",
                [int(z), *sorted(keep_ids)],
            )
        else:
            cursor = connection.execute("DELETE FROM locations WHERE z = ?", [int(z)])
        connection.commit()
        deleted = cursor.rowcount
    finally:
        connection.close()
    return int(deleted)


def rebuild_catalog(output_path: str | os.PathLike[str]) -> int:
    """Rebuild sqlite from images JSON, section JSONL, masks, and ignore.json.

    Existing SAM2 score columns are copied onto matching location ids.
    Returns the number of location rows written.
    """
    output = Path(output_path)
    apply_ignore_moves(output)
    ignored_ids = load_ignore_ids(output)
    preserved = load_sam2_by_id(output)
    records_by_id = _records_by_id(output)
    rows = _collect_location_rows(output, records_by_id, ignored_ids)
    reporter = IterateProgressReporter(
        GALLERY_TRACK_ID, len(rows), label=GALLERY_LABEL, depth=0
    )
    reporter.start()
    try:
        connection = connect(output)
        try:
            connection.execute("DELETE FROM locations")
            for index, row in enumerate(rows, start=1):
                sam2 = preserved.get((int(row["location_id"]), str(row["image_key"])), {})
                row.update(sam2)
                _insert_row(connection, row)
                reporter.update(index)
            connection.commit()
        finally:
            connection.close()
    finally:
        reporter.complete()
    return len(rows)


def upsert_sam2_scores(
    output_path: str | os.PathLike[str],
    location_id: int,
    scores: dict[str, Any],
    image_key: str | None = None,
) -> None:
    """Update SAM2 score columns for one location, or one image when *image_key* is set."""
    connection = connect(output_path)
    try:
        assignments = ", ".join(f"{name} = ?" for name in SAM2_COLUMNS)
        values = [scores.get(name) for name in SAM2_COLUMNS]
        if image_key is None:
            connection.execute(
                f"UPDATE locations SET {assignments} WHERE location_id = ?",
                [*values, int(location_id)],
            )
        else:
            connection.execute(
                f"UPDATE locations SET {assignments} WHERE location_id = ? AND image_key = ?",
                [*values, int(location_id), image_key],
            )
        connection.commit()
    finally:
        connection.close()


def list_catalog_rows(output_path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Return all catalog rows as plain dicts."""
    path = sqlite_path(output_path)
    if not path.is_file():
        return []
    connection = connect(output_path)
    try:
        rows = connection.execute(
            "SELECT * FROM locations ORDER BY z, location_id, image_key"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


_NON_SAM2_COLUMNS = (
    "location_id",
    "z",
    "structure_id",
    "structure_label",
    "type_id",
    "type_name",
    "radius",
    "image_key",
    "image_relpath",
    "mask_relpath",
    "ignored",
)


def _insert_row(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    columns = [*_NON_SAM2_COLUMNS, *SAM2_COLUMNS]
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO locations ({', '.join(columns)}) VALUES ({placeholders})",
        [row.get(name) for name in columns],
    )


def _upsert_row(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Insert a location row; on conflict refresh metadata but not SAM2 scores."""
    columns = [*_NON_SAM2_COLUMNS, *SAM2_COLUMNS]
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(
        f"{name} = excluded.{name}"
        for name in _NON_SAM2_COLUMNS
        if name != "location_id"
    )
    connection.execute(
        f"INSERT INTO locations ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(location_id, image_key) DO UPDATE SET {updates}",
        [row.get(name) for name in columns],
    )


def _set_ignored_flag(
    output_path: str | os.PathLike[str],
    location_id: int,
    *,
    ignored: bool,
) -> None:
    path = sqlite_path(output_path)
    if not path.is_file():
        return
    connection = connect(output_path)
    try:
        connection.execute(
            "UPDATE locations SET ignored = ? WHERE location_id = ?",
            [1 if ignored else 0, location_id],
        )
        connection.commit()
    finally:
        connection.close()


def _find_masks(folder: Path, location_id: int) -> list[Path]:
    """Every mask file for *location_id*, including each split-window fragment."""
    if not folder.is_dir():
        return []
    suffix = f"_{int(location_id)}.png"
    return sorted(path for path in folder.glob(f"*{suffix}") if path.is_file())


def _find_mask(folder: Path, location_id: int) -> Path | None:
    matches = _find_masks(folder, location_id)
    if not matches:
        return None
    return matches[0]


def _records_by_id(
    output: Path,
    *,
    z: int | None = None,
) -> dict[int, LocationRecord]:
    work = output / "_work"
    if not work.is_dir():
        return {}
    by_id: dict[int, LocationRecord] = {}
    if z is not None:
        for record in load_section_records(output, int(z)):
            by_id[record.id] = record
        return by_id
    for path in work.glob("section_*.jsonl"):
        token = path.name[len("section_") : -len(".jsonl")]
        if not token.isdigit():
            continue
        for record in load_section_records(output, int(token)):
            by_id[record.id] = record
    return by_id


def _image_key_matches_z(image_key: str, z: int) -> bool:
    """True when *image_key* looks like ``{volume}_{z}_D...``."""
    return f"_{int(z)}_D" in image_key


def _iter_image_json_paths(
    images: Path,
    *,
    z: int | None,
    image_keys: set[str] | None,
) -> list[Path]:
    """JSON sidecars to read for a catalog pass. Prefer an explicit key list."""
    if image_keys is not None:
        paths = [images / f"{key}.json" for key in sorted(image_keys)]
        return [path for path in paths if path.is_file()]
    found: list[Path] = []
    with os.scandir(images) as entries:
        for entry in entries:
            if not entry.is_file() or not entry.name.endswith(".json"):
                continue
            if z is not None and not _image_key_matches_z(entry.name[:-5], z):
                continue
            found.append(Path(entry.path))
    found.sort()
    return found


def _read_json_object(path: Path) -> dict[str, Any] | None:
    """Load one JSON object; skip corrupt or non-dict files.

    Propagates ``EMFILE`` / ``ENFILE`` so callers see FD exhaustion instead of
    silently omitting crops from the catalog.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        if exc.errno in (errno.EMFILE, errno.ENFILE):
            raise
        return None
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _collect_location_rows(
    output: Path,
    records_by_id: dict[int, LocationRecord],
    ignored_ids: set[int],
    *,
    z: int | None = None,
    image_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    rows: dict[tuple[int, str], dict[str, Any]] = {}
    images = output / "images"
    if images.is_dir():
        for json_path in _iter_image_json_paths(images, z=z, image_keys=image_keys):
            image_key = json_path.stem
            payload = _read_json_object(json_path)
            if payload is None:
                continue
            annotations = payload.get("annotations") or []
            for item in annotations:
                location_id = int(item["id"])
                record = records_by_id.get(location_id)
                area = item.get("area")
                stored_radius = record.radius if record is not None else None
                rows[(location_id, image_key)] = _row(
                    output,
                    location_id=location_id,
                    z=_row_z(record, image_key, item),
                    record=record,
                    annotation=item,
                    image_key=image_key,
                    area=None if area is None else float(area),
                    stored_radius=stored_radius,
                    ignored_ids=ignored_ids,
                )
    for folder, ignored_flag in ((output / "masks", False), (output / "ignored", True)):
        if not folder.is_dir():
            continue
        with os.scandir(folder) as entries:
            for entry in entries:
                if not entry.is_file() or not entry.name.endswith(".png"):
                    continue
                path = Path(entry.path)
                location_id = _location_id_from_mask_name(path)
                if location_id is None:
                    continue
                image_key = path.stem[: -len(f"_{location_id}")]
                if image_keys is not None and image_key not in image_keys:
                    continue
                if z is not None and not _image_key_matches_z(image_key, z):
                    continue
                if (location_id, image_key) in rows:
                    continue
                record = records_by_id.get(location_id)
                rows[(location_id, image_key)] = _row(
                    output,
                    location_id=location_id,
                    z=_row_z(record, image_key, {}),
                    record=record,
                    annotation={},
                    image_key=image_key,
                    area=None,
                    stored_radius=record.radius if record is not None else None,
                    ignored_ids=ignored_ids if not ignored_flag else ignored_ids | {location_id},
                )
    return list(rows.values())


def _row(
    output: Path,
    *,
    location_id: int,
    z: int,
    record: LocationRecord | None,
    annotation: dict[str, Any],
    image_key: str,
    area: float | None,
    stored_radius: float | None,
    ignored_ids: set[int],
) -> dict[str, Any]:
    ignored = location_id in ignored_ids
    mask_rel = _mask_relpath(output, image_key, location_id, ignored=ignored)
    structure_id = None
    structure_label = annotation.get("structure_label")
    type_id = annotation.get("category_id")
    type_name = annotation.get("type_name")
    if record is not None:
        structure_id = record.parent_id
        structure_label = record.structure_label if structure_label is None else structure_label
        type_id = record.type_id if type_id is None else type_id
        type_name = record.type_name if type_name is None else type_name
    return {
        "location_id": location_id,
        "z": int(z),
        "structure_id": structure_id,
        "structure_label": structure_label,
        "type_id": None if type_id is None else int(type_id),
        "type_name": type_name,
        "radius": equivalent_radius(area, stored_radius),
        "image_key": image_key,
        "image_relpath": _image_relpath(output, image_key),
        "mask_relpath": mask_rel,
        "ignored": 1 if ignored else 0,
    }


def _image_relpath(output: Path, image_key: str) -> str:
    path = resolve_crop_image(output / "images", image_key)
    if path.is_file():
        return str(path.relative_to(output)).replace("\\", "/")
    return f"images/{crop_image_filename(image_key)}"


def _mask_relpath(output: Path, image_key: str, location_id: int, *, ignored: bool) -> str:
    name = f"{image_key}_{location_id}.png"
    folder = "ignored" if ignored else "masks"
    relative = f"{folder}/{name}"
    if (output / relative).is_file():
        return relative
    found = _find_masks(output / folder, location_id)
    if len(found) == 1:
        return str(found[0].relative_to(output)).replace("\\", "/")
    return relative


def _location_id_from_mask_name(path: Path) -> int | None:
    if "_" not in path.stem:
        return None
    token = path.stem.rsplit("_", 1)[-1]
    if not token.isdigit():
        return None
    return int(token)


def _row_z(record: LocationRecord | None, image_key: str, annotation: dict[str, Any]) -> int:
    if record is not None:
        return int(record.z)
    if "z" in annotation:
        return int(annotation["z"])
    parts = image_key.split("_")
    for index, part in enumerate(parts):
        if part.startswith("D") and index > 0 and parts[index - 1].isdigit():
            return int(parts[index - 1])
    return 0
