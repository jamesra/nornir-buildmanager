"""OData and Geometries-dump ingest into per-section JSONL."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.segmentationtraining.progress import (
    INGEST_LABEL,
    INGEST_TRACK_ID,
    IterateProgressReporter,
)
from nornir_buildmanager.operations.segmentationtraining.records import (
    SCHEMA_VERSION,
    LocationRecord,
    parse_datetime,
    resolve_odata_filter,
)
from nornir_buildmanager.operations.segmentationtraining.wkt import (
    is_skippable_location,
    mosaic_wkt_from_odata_shape,
)

_logger = logging.getLogger(__name__)

ODATA_PAGE_SIZE = 16384
# ASP.NET EnableQueryAttribute default; $filter AST nodes above this return 400.
ODATA_MAX_FILTER_NODES = 100
ODATA_FILTER_NODE_MARGIN = 10
_ODATA_FILTER_TOKEN = re.compile(
    r"'(?:''|[^'])*'|[A-Za-z_][A-Za-z0-9_]*|[+-]?\d+(?:\.\d+)?"
)
_ODATA_COMPARISON = frozenset({"eq", "ne", "gt", "ge", "lt", "le"})
HttpGet = Callable[[str], dict[str, Any]]


def work_dir(output_path: str | os.PathLike[str]) -> Path:
    """Return `{Output}/_work`."""
    path = Path(output_path) / "_work"
    path.mkdir(parents=True, exist_ok=True)
    return path


def section_jsonl_path(output_path: str | os.PathLike[str], z: int) -> Path:
    """Per-section spill file used by ExportSectionCrops."""
    return work_dir(output_path) / f"section_{z}.jsonl"


def cache_meta_path(output_path: str | os.PathLike[str]) -> Path:
    """Sidecar describing the ingest snapshot."""
    return work_dir(output_path) / "cache.meta.json"


def export_source_path(output_path: str | os.PathLike[str]) -> Path:
    """Durable provenance file next to crops, not under `_work`."""
    return Path(output_path) / "source.json"


def load_cache_meta(output_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Return the ingest cache sidecar, or `{}` when it is missing or invalid."""
    path = cache_meta_path(output_path)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_cache_meta_sections(output_path: str | os.PathLike[str]) -> list[int]:
    """Section numbers recorded by the last ingest, in cache order."""
    sections: list[int] = []
    for item in load_cache_meta(output_path).get("sections") or []:
        try:
            sections.append(int(item))
        except (TypeError, ValueError):
            continue
    return sections


_SOURCE_EXPORTERS = frozenset({"tiled", "legacy"})
_SOURCE_SECRET_KEYS = frozenset({
    "connection",
    "connectionstring",
    "connection_string",
    "server",
    "password",
    "user",
    "uid",
    "pwd",
    "credentials",
})


def _exporter_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if key in _SOURCE_EXPORTERS:
        return key
    return None


def _source_from_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Public source.json fields. A SQL export keeps kind only, never a connection."""
    source = meta.get("source") if isinstance(meta.get("source"), dict) else {}
    kind = source.get("kind")
    payload: dict[str, Any] = {
        "kind": kind,
        "filter": meta.get("filter") or source.get("filter"),
        "includeOffEdge": bool(meta.get("includeOffEdge")),
        "ingestedAt": meta.get("ingestedAt"),
    }
    exporter = _exporter_name(meta.get("exporter") or source.get("exporter"))
    if exporter:
        payload["exporter"] = exporter
    if kind == "sql":
        return payload
    url = source.get("url")
    if isinstance(url, str) and url.strip():
        payload["odata"] = url.strip()
    dump_path = source.get("path")
    if dump_path:
        payload["path"] = str(dump_path)
    return payload


def _write_source_document(output_path: str | os.PathLike[str], payload: dict[str, Any]) -> None:
    """Write source.json after dropping connection secrets."""
    cleaned = {
        key: value
        for key, value in payload.items()
        if str(key).lower() not in _SOURCE_SECRET_KEYS
    }
    export_source_path(output_path).parent.mkdir(parents=True, exist_ok=True)
    export_source_path(output_path).write_text(
        json.dumps(cleaned, indent=2), encoding="utf-8"
    )


def write_export_source(
    output_path: str | os.PathLike[str],
    meta: dict[str, Any],
) -> None:
    """Write `{Output}/source.json` from an ingest meta document.

    ``odata`` stores the service root. ``geometries`` stores the dump path and
    does not invent a URL. ``sql`` stores ``kind`` only: no connection string,
    server, or credentials. An OData URL already saved on a SQL export is kept.
    """
    payload = _source_from_meta(meta)
    path = export_source_path(output_path)
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            existing = loaded
    if "exporter" not in payload:
        prior_exporter = _exporter_name(existing.get("exporter"))
        if prior_exporter:
            payload["exporter"] = prior_exporter
    if payload.get("kind") == "sql" and "odata" not in payload:
        prior_url = existing.get("odata")
        if isinstance(prior_url, str) and prior_url.strip():
            payload["odata"] = prior_url.strip()
    _write_source_document(output_path, payload)


def record_export_exporter(output_path: str | os.PathLike[str], exporter: str) -> None:
    """Remember the -Exporter value this volume actually used."""
    key = _exporter_name(exporter)
    if key is None:
        raise ValueError("exporter must be tiled or legacy")
    current = load_export_source(output_path)
    current["exporter"] = key
    _write_source_document(output_path, current)


def load_export_source(output_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load `source.json`, or reconstruct it from `_work/cache.meta.json`."""
    path = export_source_path(output_path)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload:
            return payload
    meta = load_cache_meta(output_path)
    source = meta.get("source") if isinstance(meta.get("source"), dict) else {}
    if not meta and not source:
        return {}
    reconstructed: dict[str, Any] = {
        "kind": source.get("kind"),
        "filter": meta.get("filter") or source.get("filter"),
        "includeOffEdge": bool(meta.get("includeOffEdge")),
        "ingestedAt": meta.get("ingestedAt"),
    }
    url = source.get("url")
    if isinstance(url, str) and url.strip() and source.get("kind") != "sql":
        reconstructed["odata"] = url.strip()
    dump_path = source.get("path")
    if dump_path and source.get("kind") != "sql":
        reconstructed["path"] = str(dump_path)
    exporter = _exporter_name(meta.get("exporter") or source.get("exporter"))
    if exporter:
        reconstructed["exporter"] = exporter
    return {key: value for key, value in reconstructed.items() if value is not None}


def odata_url_from_output(output_path: str | os.PathLike[str]) -> str | None:
    """OData service root recorded for this export, or None."""
    url = load_export_source(output_path).get("odata")
    if isinstance(url, str):
        text = url.strip()
        if text:
            return text
    return None


def odata_url_for_export(
    output_path: str | os.PathLike[str],
    image_meta: dict[str, Any] | None = None,
) -> str | None:
    """Prefer the volume `source.json` URL, else one already on a crop JSON."""
    url = odata_url_from_output(output_path)
    if url:
        return url
    if image_meta is None:
        return None
    raw = image_meta.get("odata")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def ensure_export_source(output_path: str | os.PathLike[str]) -> None:
    """Create `source.json` from cache.meta when the durable file is missing."""
    if export_source_path(output_path).is_file():
        return
    meta = load_cache_meta(output_path)
    if meta:
        write_export_source(output_path, meta)


def ingest_to_section_files(
    *,
    output_path: str | os.PathLike[str],
    odata: str | None,
    geometries: str | os.PathLike[str] | None,
    odata_filter: str | None = None,
    structure_type_ids: list[int] | None = None,
    sections: list[int] | None = None,
    include_off_edge: bool = False,
    http_get: HttpGet | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Write per-section JSONL from exactly one of OData or a Geometries dump."""
    if bool(odata) == bool(geometries):
        raise NornirUserException(
            "ExportAnnotationCrops requires exactly one of -OData or -Geometries"
        )
    try:
        filter_text = resolve_odata_filter(odata_filter, structure_type_ids)
    except ValueError as exc:
        raise NornirUserException(str(exc)) from exc
    prior_odata = odata_url_from_output(output_path)
    if odata:
        records: Iterable[LocationRecord] = iter_odata_locations(
            odata,
            filter_text=filter_text,
            include_off_edge=include_off_edge,
            sections=sections,
            http_get=http_get,
            output_path=output_path,
            force=force,
        )
        source = {"kind": "odata", "url": odata, "filter": filter_text}
    else:
        records = iter_geometries_dump(
            Path(geometries),  # type: ignore[arg-type]
            include_off_edge=include_off_edge,
            sections=sections,
        )
        source = {"kind": "geometries", "path": str(geometries), "filter": filter_text}
        if prior_odata:
            source["url"] = prior_odata

    written = write_section_jsonl(
        output_path,
        records,
        prune_missing=sections is None,
    )
    meta = {
        "schemaVersion": SCHEMA_VERSION,
        "source": source,
        "filter": filter_text,
        "includeOffEdge": include_off_edge,
        "ingestedAt": datetime.now(timezone.utc).isoformat(),
        "sections": sorted(written.keys()),
        "counts": {str(z): len(rows) for z, rows in written.items()},
    }
    cache_meta_path(output_path).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    write_export_source(output_path, meta)
    return meta


def write_section_jsonl(
    output_path: str | os.PathLike[str],
    records: Iterable[LocationRecord],
    *,
    prune_missing: bool = True,
) -> dict[int, list[LocationRecord]]:
    """Flush records grouped by Z into section JSONL files. Returns the groups."""
    by_z: dict[int, list[LocationRecord]] = {}
    for record in records:
        by_z.setdefault(record.z, []).append(record)
    work = work_dir(output_path)
    existing = {path for path in work.glob("section_*.jsonl")}
    kept: set[Path] = set()
    for z, group in by_z.items():
        group.sort(key=lambda item: item.id)
        path = section_jsonl_path(output_path, z)
        with path.open("w", encoding="utf-8") as handle:
            for item in group:
                handle.write(json.dumps(item.to_json(), separators=(",", ":")) + "\n")
        kept.add(path)
    if prune_missing:
        for stale in existing - kept:
            stale.unlink(missing_ok=True)
    return by_z


def load_section_records(output_path: str | os.PathLike[str], z: int) -> list[LocationRecord]:
    """Read the ingest spill for one section."""
    path = section_jsonl_path(output_path, z)
    if not path.is_file():
        return []
    records: list[LocationRecord] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(LocationRecord.from_json(json.loads(line)))
    return records


def iter_geometries_dump(
    path: Path,
    *,
    include_off_edge: bool,
    sections: list[int] | None,
) -> Iterator[LocationRecord]:
    """Yield LocationRecords from an OData dump (JSONL or {value:[]})."""
    text = path.read_text(encoding="utf-8")
    entities = list(_parse_odata_dump(text))
    section_set = set(sections) if sections else None
    reporter = IterateProgressReporter(
        INGEST_TRACK_ID, len(entities), label=INGEST_LABEL, depth=0
    )
    reporter.start()
    try:
        for index, entity in enumerate(entities, start=1):
            record = location_from_odata_entity(
                entity, include_off_edge=include_off_edge
            )
            if record is not None and (
                section_set is None or record.z in section_set
            ):
                yield record
            reporter.update(index)
    finally:
        reporter.complete()


def _parse_odata_dump(text: str) -> Iterator[dict[str, Any]]:
    """Yield entities from concatenated JSON documents, or from JSONL if that fails."""
    stripped = text.strip()
    if not stripped:
        return
    if stripped[0] == "{":
        try:
            decoder = json.JSONDecoder()
            offset = 0
            while offset < len(stripped):
                while offset < len(stripped) and stripped[offset].isspace():
                    offset += 1
                if offset >= len(stripped):
                    break
                doc, offset = decoder.raw_decode(stripped, offset)
                yield from _entities_from_odata_document(doc)
            return
        except json.JSONDecodeError:
            pass
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        yield json.loads(line)


def _entities_from_odata_document(doc: Any) -> list[dict[str, Any]]:
    """Entities from an OData ``value`` array, a bare list, or one object with ``ID``."""
    if isinstance(doc, list):
        return [item for item in doc if isinstance(item, dict)]
    if isinstance(doc, dict) and "value" in doc:
        value = doc["value"]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if isinstance(doc, dict) and "ID" in doc:
        return [doc]
    return []


def iter_odata_locations(
    base_url: str,
    *,
    filter_text: str,
    include_off_edge: bool,
    sections: list[int] | None,
    http_get: HttpGet | None = None,
    output_path: str | os.PathLike[str] | None = None,
    force: bool = False,
) -> Iterator[LocationRecord]:
    """Two-phase OData ingest: IDs first, MosaicShape only for dirty Z."""
    getter = http_get or http_get_json
    section_set = set(sections) if sections else None
    pass_a = _fetch_odata_entities(
        _odata_locations_url(
            base_url,
            filter_text=filter_text,
            select="ID,Z,LastModified,ParentID,OffEdge",
            expand=None,
        ),
        getter,
    )
    by_z: dict[int, list[dict[str, Any]]] = {}
    for entity in pass_a:
        z = int(entity["Z"])
        if section_set is not None and z not in section_set:
            continue
        by_z.setdefault(z, []).append(entity)
    if not by_z:
        return

    dirty: list[int] = []
    for z, entities in by_z.items():
        if force or output_path is None or _section_needs_geometry(
            output_path, z, entities, include_off_edge=include_off_edge
        ):
            dirty.append(z)
        else:
            yield from load_section_records(output_path, z)

    dirty_total = sum(len(by_z[z]) for z in dirty)
    reporter = IterateProgressReporter(
        INGEST_TRACK_ID, dirty_total, label=INGEST_LABEL, depth=0
    )
    reporter.start()
    seen = 0
    try:
        for chunk in _chunk_z_for_odata_filter(dirty, filter_text):
            pass_b_filter = _combined_z_filter(filter_text, chunk)
            for entity in _iter_odata_pages(
                _odata_locations_url(
                    base_url,
                    filter_text=pass_b_filter,
                    select="ID,ParentID,TypeCode,Z,OffEdge,MosaicShape,LastModified,Radius",
                    expand="Parent($select=ID,TypeID,Label;$expand=Type($select=ID,Name,ParentID))",
                ),
                getter,
            ):
                seen += 1
                reporter.update(seen)
                record = location_from_odata_entity(
                    entity, include_off_edge=include_off_edge
                )
                if record is None:
                    continue
                if section_set is not None and record.z not in section_set:
                    continue
                yield record
    finally:
        reporter.complete()


def location_from_odata_entity(
    entity: dict[str, Any],
    *,
    include_off_edge: bool,
) -> LocationRecord | None:
    """Map one OData Location JSON object to a LocationRecord, or skip it."""
    off_edge = bool(entity.get("OffEdge", False))
    if off_edge and not include_off_edge:
        return None
    wkt = mosaic_wkt_from_odata_shape(entity.get("MosaicShape"))
    raw_type_code = entity.get("TypeCode")
    type_code = None if raw_type_code is None or raw_type_code == "" else int(raw_type_code)
    if not wkt or is_skippable_location(type_code, wkt):
        return None
    parent = entity.get("Parent") or {}
    parent_type = parent.get("Type") or {}
    parent_id = entity.get("ParentID", parent.get("ID"))
    type_id = parent.get("TypeID", parent_type.get("ID"))
    raw_radius = entity.get("Radius")
    radius = None if raw_radius is None or raw_radius == "" else float(raw_radius)
    return LocationRecord(
        id=int(entity["ID"]),
        z=int(entity["Z"]),
        wkt=wkt,
        parent_id=None if parent_id is None else int(parent_id),
        off_edge=off_edge,
        last_modified=parse_datetime(entity.get("LastModified")),
        type_id=None if type_id is None else int(type_id),
        type_name=_optional_text(parent_type.get("Name")),
        structure_label=_optional_text(parent.get("Label")),
        type_code=type_code,
        radius=radius,
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _section_needs_geometry(
    output_path: str | os.PathLike[str],
    z: int,
    pass_a: list[dict[str, Any]],
    *,
    include_off_edge: bool,
) -> bool:
    """True when pass B must download MosaicShape for *z*."""
    incoming = [
        entity
        for entity in pass_a
        if include_off_edge or not bool(entity.get("OffEdge", False))
    ]
    existing = load_section_records(output_path, z)
    if not incoming and not existing:
        return False
    if not existing:
        return True
    if any(record.type_code is None for record in existing):
        return True
    existing_ids = {record.id for record in existing}
    incoming_ids = {int(entity["ID"]) for entity in incoming}
    if existing_ids != incoming_ids:
        return True
    existing_lm = {record.id: record.last_modified for record in existing}
    for entity in incoming:
        loc_id = int(entity["ID"])
        incoming_lm = parse_datetime(entity.get("LastModified"))
        if existing_lm[loc_id] != incoming_lm:
            return True
    return False


def _consecutive_ranges(values: list[int]) -> list[tuple[int, int]]:
    """Collapse sorted unique integers into inclusive (start, end) runs."""
    ordered = sorted(set(values))
    if not ordered:
        return []
    ranges: list[tuple[int, int]] = []
    start = previous = ordered[0]
    for value in ordered[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append((start, previous))
        start = previous = value
    ranges.append((start, previous))
    return ranges


def _odata_z_filter(values: list[int]) -> str:
    """OData predicate for Z membership using ge/le runs instead of eq/or chains."""
    predicates: list[str] = []
    for start, end in _consecutive_ranges(values):
        if start == end:
            predicates.append(f"Z eq {start}")
        else:
            predicates.append(f"(Z ge {start} and Z le {end})")
    return " or ".join(predicates)


def _combined_z_filter(base_filter: str, values: list[int]) -> str:
    """AND a Z-membership predicate onto the caller's ``$filter``."""
    z_filter = _odata_z_filter(values)
    if not z_filter:
        return base_filter
    return f"({base_filter}) and ({z_filter})"


def estimate_odata_filter_nodes(filter_text: str) -> int:
    """Conservative count of ASP.NET OData $filter AST nodes.

    Each identifier, literal, and operator is one node. Comparison operators
    add one more for the implicit Convert node ODataLib often inserts.
    """
    count = 0
    comparisons = 0
    for match in _ODATA_FILTER_TOKEN.finditer(filter_text):
        token = match.group(0)
        count += 1
        if token.lower() in _ODATA_COMPARISON:
            comparisons += 1
    return count + comparisons


def _chunk_z_for_odata_filter(values: list[int], base_filter: str) -> Iterator[list[int]]:
    """Yield Z lists whose combined $filter stays under MaxNodeCount."""
    budget = ODATA_MAX_FILTER_NODES - ODATA_FILTER_NODE_MARGIN
    chunk: list[int] = []
    for start, end in _consecutive_ranges(values):
        addition = list(range(start, end + 1))
        trial = chunk + addition
        if chunk and estimate_odata_filter_nodes(_combined_z_filter(base_filter, trial)) > budget:
            yield chunk
            chunk = addition
        else:
            chunk = trial
        nodes = estimate_odata_filter_nodes(_combined_z_filter(base_filter, chunk))
        if nodes > budget:
            raise NornirUserException(
                "OData $filter exceeds the server MaxNodeCount of "
                f"{ODATA_MAX_FILTER_NODES}; shorten -ODataFilter."
            )
    if chunk:
        yield chunk


def _odata_locations_url(
    base_url: str,
    *,
    filter_text: str,
    select: str,
    expand: str | None,
) -> str:
    """Locations query with paging, count, and a stable ``Z,ID`` order."""
    root = base_url.rstrip("/")
    query = (
        f"$filter={quote(filter_text, safe='()/')}"
        f"&$orderby=Z,ID"
        f"&$select={quote(select, safe=',')}"
        f"&$count=true"
        f"&$top={ODATA_PAGE_SIZE}"
    )
    if expand:
        query += f"&$expand={quote(expand, safe='();$,')}"
    url = f"{root}/Locations?{query}"
    return url


def _odata_count(document: dict[str, Any]) -> int | None:
    """Return ``@odata.count`` when it is a positive integer."""
    raw = document.get("@odata.count")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _iter_odata_documents(url: str, getter: HttpGet) -> Iterator[dict[str, Any]]:
    """Follow ``@odata.nextLink`` until a page omits it."""
    next_url: str | None = url
    while next_url:
        document = getter(next_url)
        yield document
        link = document.get("@odata.nextLink") if isinstance(document, dict) else None
        if not link:
            break
        next_url = _absolute_next_link(next_url, str(link))


def _fetch_odata_entities(url: str, getter: HttpGet) -> list[dict[str, Any]]:
    """Download every OData page and publish ingest progress when a total is known."""
    collected: list[dict[str, Any]] = []
    reporter: IterateProgressReporter | None = None
    try:
        for document in _iter_odata_documents(url, getter):
            page = _entities_from_odata_document(document)
            if reporter is None:
                total = _odata_count(document) if isinstance(document, dict) else None
                has_next = bool(
                    isinstance(document, dict) and document.get("@odata.nextLink")
                )
                if total is None and not has_next:
                    total = len(page)
                if total:
                    created = IterateProgressReporter(
                        INGEST_TRACK_ID, total, label=INGEST_LABEL, depth=0
                    )
                    created.start()
                    reporter = created
            collected.extend(page)
            if reporter is not None:
                reporter.update(len(collected))
        if reporter is None and collected:
            created = IterateProgressReporter(
                INGEST_TRACK_ID, len(collected), label=INGEST_LABEL, depth=0
            )
            created.start()
            created.update(len(collected))
            reporter = created
    finally:
        if reporter is not None:
            reporter.complete()
    return collected


def _iter_odata_pages(url: str, getter: HttpGet) -> Iterator[dict[str, Any]]:
    """Yield entities from every page of an OData query."""
    for document in _iter_odata_documents(url, getter):
        yield from _entities_from_odata_document(document)


def _absolute_next_link(current: str, link: str) -> str:
    """Use an absolute nextLink as-is; resolve a relative one against the current page."""
    if urlparse(link).scheme:
        return link
    return urljoin(current, link)


def http_get_json(url: str) -> dict[str, Any]:
    """GET JSON from *url* with an OData Accept header."""
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, ValueError) as exc:
        if isinstance(exc, ValueError) and not isinstance(exc, (HTTPError, URLError)):
            raise
        detail = str(exc)
        if isinstance(exc, HTTPError):
            try:
                body = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                body = ""
            if body:
                detail = f"{exc}\n{body[:2000]}"
        raise NornirUserException(f"OData request failed: {url}\n{detail}") from exc
    return json.loads(payload)
