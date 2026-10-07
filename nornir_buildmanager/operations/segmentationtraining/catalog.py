"""Per-volume AnnotationCrops sqlite catalog and ignore-list mask moves."""

from __future__ import annotations

import errno
import json
import math
import os
import sqlite3
from collections.abc import Collection, Iterator
from itertools import zip_longest
from pathlib import Path
from typing import Any, Iterable

from annotation_crops.ignore import load_ignore_ids as _load_ignore_ids
from annotation_crops.ignore import save_ignore_ids as _save_ignore_ids
from annotation_crops.maskname import MaskName
from nornir_shared import prettyoutput

from nornir_buildmanager.operations.segmentationtraining.ingest import (
    load_export_source,
    load_section_records,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    CropProductIndex,
    get_product_index,
)
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
    expected_key = ("location_id", "image_key")
    key_matches = all(
        got == wanted
        for got, wanted in zip_longest(_primary_key_columns(connection), expected_key)
    )
    if not key_matches:
        _migrate_location_primary_key(connection)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(locations)")}
    _drop_window_origin_columns(connection, columns)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS crop_geometry ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), crop_size INTEGER NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS source ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), "
        "odata TEXT, "
        "kind TEXT, "
        "filter TEXT, "
        "path TEXT, "
        "ingested_at TEXT)"
    )
    source_columns = {row[1] for row in connection.execute("PRAGMA table_info(source)")}
    if "exporter" not in source_columns:
        connection.execute("ALTER TABLE source ADD COLUMN exporter TEXT")
    connection.commit()


def _drop_window_origin_columns(connection: sqlite3.Connection, columns: set[str]) -> None:
    """Drop catalog copies of the window box. The image key already records it."""
    for name in ("origin_x", "origin_y"):
        if name in columns:
            connection.execute(f"ALTER TABLE locations DROP COLUMN {name}")


def _primary_key_columns(connection: sqlite3.Connection) -> Iterator[str]:
    """Yield primary-key column names in key order."""
    rows = connection.execute(
        "SELECT name FROM pragma_table_info('locations') WHERE pk > 0 ORDER BY pk"
    )
    for row in rows:
        yield str(row["name"])


def _migrate_location_primary_key(connection: sqlite3.Connection) -> None:
    """Rebuild locations so one location can have a row per training image."""
    connection.execute("ALTER TABLE locations RENAME TO locations_old")
    connection.execute(_CREATE_SQL)
    old_cols = {str(row[1]) for row in connection.execute("PRAGMA table_info(locations_old)")}
    shared = [
        name
        for name in (
            str(row[1]) for row in connection.execute("PRAGMA table_info(locations)")
        )
        if name in old_cols
    ]
    if shared:
        quoted = ", ".join(shared)
        connection.execute(
            f"INSERT INTO locations ({quoted}) SELECT {quoted} FROM locations_old"
        )
    connection.execute("DROP TABLE locations_old")
    connection.commit()


def load_ignore_ids(output_path: str | os.PathLike[str]) -> set[int]:
    """Load ignored location ids from `ignore.json` (a JSON array)."""
    return _load_ignore_ids(output_path)


def save_ignore_ids(output_path: str | os.PathLike[str], ids: Iterable[int]) -> None:
    """Write `ignore.json` as a sorted JSON array of location ids."""
    _save_ignore_ids(output_path, ids)


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
    products = get_product_index(output)
    ignored_dir = output / "ignored"
    moved = 0
    for location_id in load_ignore_ids(output):
        sources = _find_masks(output, "masks", location_id)
        if not sources:
            continue
        ignored_dir.mkdir(parents=True, exist_ok=True)
        for source in sources:
            _relocate_indexed(products, source, ignored_dir / source.name, "masks", "ignored")
            moved += 1
    return moved


def ignore_location(output_path: str | os.PathLike[str], location_id: int) -> bool:
    """Add *location_id* to ignore.json and move every mask for it. Returns True if listed."""
    ignore_locations(output_path, [int(location_id)])
    return True


def ignore_locations(output_path: str | os.PathLike[str], location_ids: Iterable[int]) -> int:
    """Add *location_ids* to ignore.json and move their masks once.

    Returns how many ids were not already listed.
    """
    incoming = {int(item) for item in location_ids}
    if not incoming:
        return 0
    current = load_ignore_ids(output_path)
    added = incoming - current
    if added:
        save_ignore_ids(output_path, current | incoming)
    apply_ignore_moves(output_path)
    _set_ignored_flags(output_path, incoming)
    return len(added)


def ignore_blank_crops(
    output_path: str | os.PathLike[str],
    z: int,
    blank: Iterable[tuple[str, float]],
    members_by_key: dict[str, Iterable[int]],
) -> int:
    """Ignore every location on crops whose stitched image is mostly saturated.

    One ``ignore.json`` write and one mask move cover the whole batch.
    Returns how many location ids were newly listed.
    """
    ids: list[int] = []
    seen: set[int] = set()
    for key, fraction in blank:
        members = [int(item) for item in members_by_key.get(key, ())]
        prettyoutput.Log(
            f"ExportAnnotationCrops: section {z} image {key} is "
            f"{fraction:.0%} saturated; ignoring locations {members}"
        )
        for member in members:
            if member not in seen:
                seen.add(member)
                ids.append(member)
    return ignore_locations(output_path, ids)


def restore_location(output_path: str | os.PathLike[str], location_id: int) -> bool:
    """Remove *location_id* from ignore.json and move every mask back to `masks/`."""
    location_id = int(location_id)
    ids = load_ignore_ids(output_path)
    ids.discard(location_id)
    save_ignore_ids(output_path, ids)
    output = Path(output_path)
    products = get_product_index(output)
    sources = _find_masks(output, "ignored", location_id)
    if sources:
        masks = output / "masks"
        masks.mkdir(parents=True, exist_ok=True)
        for source in sources:
            _relocate_indexed(products, source, masks / source.name, "ignored", "masks")
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
    apply_ignore: bool = True,
) -> int:
    """Insert or update catalog rows without wiping SAM2 score columns.

    When *location_ids* is set, only those ids are written. When *z* and/or
    *image_keys* are set, only matching SA-1B JSON / mask files are read so a
    long volume export does not reopen every crop on every section.
    Existing ``sam2*`` values stay on conflict; new rows get NULL scores.

    Set *apply_ignore* to ``False`` when masks were not moved during this
    run (e.g. inside ``-Update`` with only additions) to avoid a whole-tree
    glob for every ignored id.
    """
    output = Path(output_path)
    if apply_ignore:
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
    sync_source_catalog(output)
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


_catalog_dirty = False


def mark_catalog_dirty() -> None:
    """Remember that this process changed export products the gallery must reread."""
    global _catalog_dirty
    _catalog_dirty = True


def take_catalog_dirty() -> bool:
    """Return whether the catalog needs a full rebuild, then clear the flag."""
    global _catalog_dirty
    dirty = _catalog_dirty
    _catalog_dirty = False
    return dirty


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
    sync_source_catalog(output)
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
    """Insert one locations row, including empty SAM2 columns."""
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


def _set_ignored_flags(
    output_path: str | os.PathLike[str],
    location_ids: Iterable[int],
) -> None:
    """Set ``ignored`` on every catalog row for *location_ids* in one connection."""
    ids = sorted({int(item) for item in location_ids})
    if not ids or not sqlite_path(output_path).is_file():
        return
    connection = connect(output_path)
    try:
        connection.executemany(
            "UPDATE locations SET ignored = 1 WHERE location_id = ?",
            [(location_id,) for location_id in ids],
        )
        connection.commit()
    finally:
        connection.close()


def _set_ignored_flag(
    output_path: str | os.PathLike[str],
    location_id: int,
    *,
    ignored: bool,
) -> None:
    """Set ``ignored`` on every catalog row for one location id."""
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


def _relocate_indexed(
    products: CropProductIndex,
    source: Path,
    destination: Path,
    source_folder: str,
    dest_folder: str,
) -> None:
    """Move *source* onto *destination* and keep the product index in step."""
    if destination.is_file():
        destination.unlink()
        products.forget(dest_folder, destination.name)
    source.replace(destination)
    products.forget(source_folder, source.name)
    products.note(dest_folder, destination.name)


def _find_masks(output: Path, folder: str, location_id: int) -> list[Path]:
    """Every mask file for *location_id* in the cached *folder*."""
    products = get_product_index(output)
    suffix = f"_{int(location_id)}.png"
    root = output.joinpath(*folder.split("/"))
    names = sorted(name for name in products.iter_names(folder) if name.endswith(suffix))
    return [root / name for name in names]


def _find_mask(output: Path, folder: str, location_id: int) -> Path | None:
    """First cached mask for a location id."""
    matches = _find_masks(output, folder, location_id)
    if not matches:
        return None
    return matches[0]


def _records_by_id(
    output: Path,
    *,
    z: int | None = None,
) -> dict[int, LocationRecord]:
    """Location records from one section JSONL, or every section when *z* is omitted."""
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
    output: Path,
    products: CropProductIndex,
    *,
    z: int | None,
    image_keys: set[str] | None,
) -> Iterator[Path]:
    """Yield JSON sidecars from the product index. Prefer an explicit key list."""
    images = output / "images"
    if image_keys is not None:
        for key in image_keys:
            name = f"{key}.json"
            if products.exists("images", name):
                yield images / name
        return
    for name in products.iter_names("images"):
        if not name.endswith(".json"):
            continue
        if z is not None and not _image_key_matches_z(name[:-5], z):
            continue
        yield images / name


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
) -> Collection[dict[str, Any]]:
    """Catalog rows from crop JSON and indexed masks. *image_keys* limits the scan to one section."""
    rows: dict[tuple[int, str], dict[str, Any]] = {}
    products = get_product_index(output)
    for json_path in _iter_image_json_paths(output, products, z=z, image_keys=image_keys):
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
    for folder, ignored_flag in (("masks", False), ("ignored", True)):
        for location_id, image_key in _iter_indexed_masks(
            products, folder, z=z, image_keys=image_keys
        ):
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
    return rows.values()


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
    """One locations-table row for a location on one crop."""
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


def record_crop_size(output_path: str | os.PathLike[str], crop_size: int) -> None:
    """Remember the volume crop size. Does not stitch or move files."""
    connection = connect(output_path)
    try:
        connection.execute(
            "INSERT INTO crop_geometry (id, crop_size) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET crop_size = excluded.crop_size",
            [int(crop_size)],
        )
        connection.commit()
    finally:
        connection.close()


def read_crop_size(output_path: str | os.PathLike[str]) -> int | None:
    """Return the volume crop size, or None when the catalog has not recorded one."""
    if not sqlite_path(output_path).is_file():
        return None
    connection = connect(output_path)
    try:
        row = connection.execute("SELECT crop_size FROM crop_geometry WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        connection.close()
    if row is None:
        return None
    return int(row[0])


def sync_source_catalog(output_path: str | os.PathLike[str]) -> None:
    """Copy `source.json` (or cache.meta) into the sqlite `source` row."""
    source = load_export_source(output_path)
    if not source:
        return
    connection = connect(output_path)
    try:
        connection.execute(
            "INSERT INTO source (id, odata, kind, filter, path, ingested_at, exporter) "
            "VALUES (1, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "odata = excluded.odata, "
            "kind = excluded.kind, "
            "filter = excluded.filter, "
            "path = excluded.path, "
            "ingested_at = excluded.ingested_at, "
            "exporter = excluded.exporter",
            [
                source.get("odata"),
                source.get("kind"),
                source.get("filter"),
                source.get("path"),
                source.get("ingestedAt"),
                source.get("exporter"),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def read_source_odata(output_path: str | os.PathLike[str]) -> str | None:
    """OData service root stored on the catalog, or None."""
    if not sqlite_path(output_path).is_file():
        return None
    connection = connect(output_path)
    try:
        row = connection.execute("SELECT odata FROM source WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        connection.close()
    if row is None:
        return None
    value = row["odata"]
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _image_relpath(output: Path, image_key: str) -> str:
    """Relative crop path, PNG when that file is present."""
    path = resolve_crop_image(output / "images", image_key)
    if path.is_file():
        return str(path.relative_to(output)).replace("\\", "/")
    return f"images/{crop_image_filename(image_key)}"


def _iter_indexed_masks(
    products: CropProductIndex,
    folder: str,
    *,
    z: int | None,
    image_keys: set[str] | None,
) -> Iterator[tuple[int, str]]:
    """Yield ``(location_id, image_key)`` for cached PNG masks in *folder*."""
    if image_keys is not None:
        for image_key in image_keys:
            if z is not None and not _image_key_matches_z(image_key, z):
                continue
            for location_id, name in products.members(folder, image_key):
                if name.endswith(".png"):
                    yield location_id, image_key
        return
    for name in products.iter_names(folder):
        if not name.endswith(".png"):
            continue
        location_id = _location_id_from_mask_name(Path(name))
        if location_id is None:
            continue
        image_key = Path(name).stem[: -len(f"_{location_id}")]
        if z is not None and not _image_key_matches_z(image_key, z):
            continue
        yield location_id, image_key


def _mask_relpath(output: Path, image_key: str, location_id: int, *, ignored: bool) -> str:
    """Relative mask path. A lone fragment name is used when the expected file is absent."""
    products = get_product_index(output)
    name = f"{image_key}_{location_id}.png"
    folder = "ignored" if ignored else "masks"
    relative = f"{folder}/{name}"
    if products.exists(folder, name):
        return relative
    found = _find_masks(output, folder, location_id)
    if len(found) == 1:
        return str(found[0].relative_to(output)).replace("\\", "/")
    return relative


def _location_id_from_mask_name(path: Path) -> int | None:
    """Location id from a ``{image_key}_{id}.png`` mask filename."""
    parsed = MaskName.parse(path.name)
    if parsed is None:
        return None
    return parsed.location_id


def _row_z(record: LocationRecord | None, image_key: str, annotation: dict[str, Any]) -> int:
    """Section number from the record, the annotation, or the ``{volume}_{z}_D`` key."""
    if record is not None:
        return int(record.z)
    if "z" in annotation:
        return int(annotation["z"])
    parts = image_key.split("_")
    for index, part in enumerate(parts):
        if part.startswith("D") and index > 0 and parts[index - 1].isdigit():
            return int(parts[index - 1])
    return 0
