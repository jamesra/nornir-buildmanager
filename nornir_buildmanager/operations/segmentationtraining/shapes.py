"""``shapes.jsonl``: every location's outline and its links to adjacent sections.

The SAM2 trainer builds an *adjacent-section seed* the way annotators do in Viking: take the
polygon of the same structure on section z +/- 1-2, centre it on the target and scale it until it
fits inside the cell. That needs, per location, the structure it belongs to, the locations it is
linked to in nearby sections, and its outline. The crop PNGs hold none of that (a polygon is cut
into tile-sized masks), so this module writes it once per volume:

* ``{Output}/shapes.jsonl``: one line per polygon location that has a mask or is linked to one::

      {"location_id": 8, "z": 3, "structure_id": 77, "structure_label": "BC",
       "has_mask": true, "centroid": [x, y], "area": 1234.5,
       "linked": [[7, 2], [9, 4]],
       "polygons": [[[[dx, dy], ...exterior...], [[dx, dy], ...hole...]], ...]}

  ``centroid`` and ``area`` are in mosaic pixels at downsample 1; ``polygons`` is a list of
  polygons, each a list of rings (exterior first, then holes) with every point relative to the
  centroid, so the trainer can place the outline anywhere. ``linked`` lists ``[location_id, z]``
  of locations Viking links to this one within :data:`LINK_MAX_DZ` sections and the same structure.

* ``structureIds`` and ``linkedIds`` on every ``manifest.jsonl`` row, parallel to ``locationIds``.

Both are rebuilt from the ingest spills in the work folder on every run (including ``-Update``,
``-Repair`` and ``-Cleanup``), so an export made before this module existed is brought up to
date by running the pipeline again; no crop is restitched. The only network use is one
``LocationLinks`` pull, cached in the work folder's ``location_links.json`` and repeated when the cache is
missing, when the set of locations changed, or with ``-Update`` / ``-RefreshOData`` / ``-Force``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import numpy as np

from nornir_buildmanager.operations.segmentationtraining.curves import hydrate_polygons
from nornir_buildmanager.operations.segmentationtraining.ingest import (
    _iter_odata_pages,
    _odata_locations_url,
    http_get_json,
    load_section_records,
    odata_filter_from_output,
    odata_url_from_output,
    work_dir,
)
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord
from nornir_buildmanager.operations.segmentationtraining.workdir import work_root
from nornir_buildmanager.operations.segmentationtraining.wkt import (
    PolygonRings,
    Ring,
    parse_wkt_polygons,
)

_logger = logging.getLogger(__name__)

SHAPES_FILENAME = "shapes.jsonl"
LINKS_CACHE_FILENAME = "location_links.json"
LINKS_CACHE_VERSION = 2
#: Viking links a location to the same structure on neighbouring sections; ignore anything farther.
LINK_MAX_DZ = 2
_PRECISION = 2
#: Outlines are simplified to within this many pixels. A fitted curve polygon has hundreds of points;
#: a seed that is scaled and shifted to fit inside a cell does not need them.
SIMPLIFY_TOLERANCE_PX = 0.75

HttpGet = Callable[[str], dict[str, Any]]


# --------------------------------------------------------------------------- geometry


def ring_area_and_centroid(ring: Ring) -> tuple[float, float, float]:
    """Signed shoelace area and centroid ``(area, cx, cy)`` of a ring; a degenerate ring has area 0."""
    points = list(ring)
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        if not points:
            return 0.0, 0.0, 0.0
        return 0.0, sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)
    twice_area = 0.0
    cx = cy = 0.0
    for index, (x0, y0) in enumerate(points):
        x1, y1 = points[(index + 1) % len(points)]
        cross = x0 * y1 - x1 * y0
        twice_area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if twice_area == 0.0:
        return 0.0, sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)
    return twice_area / 2.0, cx / (3.0 * twice_area), cy / (3.0 * twice_area)


def polygons_area_and_centroid(polygons: Iterable[PolygonRings]) -> tuple[float, float, float]:
    """Area and area-weighted centroid of polygons: exterior rings add, holes subtract."""
    total = 0.0
    sx = sy = 0.0
    fallback: list[tuple[float, float]] = []
    for rings in polygons:
        for index, ring in enumerate(rings):
            area, cx, cy = ring_area_and_centroid(ring)
            if index == 0:
                fallback.append((cx, cy))
            weight = abs(area) if index == 0 else -abs(area)
            total += weight
            sx += cx * weight
            sy += cy * weight
    if total > 0.0:
        return total, sx / total, sy / total
    if fallback:
        return 0.0, sum(p[0] for p in fallback) / len(fallback), sum(p[1] for p in fallback) / len(fallback)
    return 0.0, 0.0, 0.0


def _simplify_open(points: np.ndarray, tolerance: float) -> np.ndarray:
    """Ramer-Douglas-Peucker on an open polyline; the end points are kept."""
    count = len(points)
    if count <= 2:
        return points
    keep = np.zeros(count, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, count - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        start, end = points[first], points[last]
        chord = end - start
        length = float(np.hypot(chord[0], chord[1]))
        inner = points[first + 1 : last] - start
        if length == 0.0:
            distances = np.hypot(inner[:, 0], inner[:, 1])
        else:
            distances = np.abs(chord[0] * inner[:, 1] - chord[1] * inner[:, 0]) / length
        farthest = int(np.argmax(distances))
        if distances[farthest] > tolerance:
            middle = first + 1 + farthest
            keep[middle] = True
            stack.append((first, middle))
            stack.append((middle, last))
    return points[keep]


def simplify_ring(ring: Ring, tolerance: float = SIMPLIFY_TOLERANCE_PX) -> Ring:
    """A closed ring reduced to within *tolerance* pixels of the original; never fewer than three points."""
    points = np.asarray(ring, dtype=float)
    if len(points) > 1 and np.array_equal(points[0], points[-1]):
        points = points[:-1]
    if len(points) <= 4 or tolerance <= 0:
        return ring
    # Split the loop at the point farthest from its first point so both halves are open polylines.
    split = int(np.argmax(np.hypot(*(points - points[0]).T)))
    first = _simplify_open(points[: split + 1], tolerance)
    second = _simplify_open(np.vstack([points[split:], points[:1]]), tolerance)
    merged = np.vstack([first[:-1], second[:-1]])
    if len(merged) < 3:
        return ring
    closed = np.vstack([merged, merged[:1]])
    return tuple((float(x), float(y)) for x, y in closed)


def location_shape(record: LocationRecord) -> dict[str, Any] | None:
    """Outline of one location relative to its centroid, or None when its WKT has no polygon."""
    try:
        polygons = hydrate_polygons(parse_wkt_polygons(record.wkt), record.type_code)
    except ValueError:
        _logger.info("shapes: skip location %s, unparseable polygon", record.id)
        return None
    if not polygons:
        return None
    area, cx, cy = polygons_area_and_centroid(polygons)
    relative = [
        [[[round(x - cx, _PRECISION), round(y - cy, _PRECISION)] for x, y in simplify_ring(ring)] for ring in rings]
        for rings in polygons
    ]
    return {
        "centroid": [round(cx, _PRECISION), round(cy, _PRECISION)],
        "area": round(area, _PRECISION),
        "polygons": relative,
    }


# --------------------------------------------------------------------------- links


def _ids_digest(ids: Iterable[int]) -> str:
    return hashlib.sha1(",".join(str(i) for i in sorted(ids)).encode("ascii")).hexdigest()


def _links_cache_path(output: Path) -> Path:
    return work_dir(output) / LINKS_CACHE_FILENAME


def _load_links_cache(path: Path, digest: str, root: str) -> list[tuple[int, int]] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != LINKS_CACHE_VERSION
        or payload.get("ids") != digest
        or payload.get("root") != root
    ):
        return None
    pairs = payload.get("links")
    if not isinstance(pairs, list):
        return None
    return [(int(a), int(b)) for a, b in pairs]


LINKS_EXPAND = "LocationLinksA($select=A,B),LocationLinksB($select=A,B)"


def fetch_links(
    root: str,
    known: dict[int, tuple[int, int | None]],
    *,
    filter_text: str | None = None,
    http_get: HttpGet | None = None,
) -> list[tuple[int, int]]:
    """``LocationLinks`` between two known locations of the same structure within LINK_MAX_DZ sections.

    *known* maps location id to ``(z, structure id)``. The links come expanded on the export's own
    locations (*filter_text* is the ``$filter`` they were ingested with) rather than from the
    volume's whole link table, which is mostly links between locations that are not exported.
    Every page is streamed and filtered, so memory stays at the size of the result.
    """
    url = _odata_locations_url(
        root.rstrip("/"), filter_text=filter_text or "ID gt 0", select="ID", expand=LINKS_EXPAND
    )
    pairs: set[tuple[int, int]] = set()
    seen = 0
    for entity in _iter_odata_pages(url, http_get or http_get_json):
        for key in ("LocationLinksA", "LocationLinksB"):
            for link in entity.get(key) or ():
                seen += 1
                try:
                    a, b = int(link["A"]), int(link["B"])
                except (KeyError, TypeError, ValueError):
                    continue
                left, right = known.get(a), known.get(b)
                if left is None or right is None or left[1] is None or left[1] != right[1]:
                    continue
                if a != b and 1 <= abs(left[0] - right[0]) <= LINK_MAX_DZ:
                    pairs.add((min(a, b), max(a, b)))
    _logger.info("shapes: read %d link end(s), kept %d link(s)", seen, len(pairs))
    return sorted(pairs)


def collect_links(
    output: Path,
    known: dict[int, tuple[int, int | None]],
    *,
    refresh: bool,
    http_get: HttpGet | None = None,
) -> dict[int, list[int]]:
    """Adjacent-section links per location id, from the cache or one OData pull.

    Without an OData root recorded for this export (a ``-Geometries`` run) there is nothing to
    ask, and an existing cache is used as it is.
    """
    root = odata_url_from_output(output)
    digest = _ids_digest(known)
    cache_path = _links_cache_path(output)
    pairs: list[tuple[int, int]] | None = None
    if root is None:
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            pairs = [(int(a), int(b)) for a, b in payload.get("links", [])]
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pairs = None
        if pairs is None:
            _logger.info("shapes: no OData root recorded and no cached links; shapes carry no links")
            pairs = []
    else:
        if not refresh:
            pairs = _load_links_cache(cache_path, digest, root)
        if pairs is None:
            pairs = fetch_links(root, known, filter_text=odata_filter_from_output(output), http_get=http_get)
            payload = {"version": LINKS_CACHE_VERSION, "root": root, "ids": digest, "links": [list(p) for p in pairs]}
            temp = cache_path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            os.replace(temp, cache_path)
    linked: dict[int, set[int]] = {}
    for a, b in pairs:
        linked.setdefault(a, set()).add(b)
        linked.setdefault(b, set()).add(a)
    return {
        location: sorted(others, key=lambda other: (known[other][0], other))
        for location, others in linked.items()
        if location in known
    }


# --------------------------------------------------------------------------- writing


def _read_manifest_rows(output: Path) -> list[dict[str, Any]]:
    path = output / "manifest.jsonl"
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _spilled_sections(output: Path) -> list[int]:
    """Section numbers that have an ingest spill in the work folder."""
    found: list[int] = []
    work = work_root(output)
    for path in work.glob("section_*.jsonl") if work.is_dir() else ():
        try:
            found.append(int(path.stem.split("_", 1)[1]))
        except (IndexError, ValueError):
            continue
    return sorted(found)


def _write_if_changed(path: Path, text: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)
    return True


def write_location_shapes(
    output_path: str | os.PathLike[str],
    *,
    refresh: bool = False,
    http_get: HttpGet | None = None,
) -> Path | None:
    """Rebuild ``shapes.jsonl`` and the manifest's ``structureIds`` / ``linkedIds`` from current data.

    Returns the path of ``shapes.jsonl``, or None when there is nothing to describe (no manifest
    and no ingested sections). An unchanged file is not rewritten.
    """
    output = Path(output_path)
    rows = _read_manifest_rows(output)
    masked = {int(i) for row in rows for i in row.get("locationIds", [])}
    records: list[LocationRecord] = []
    for z in _spilled_sections(output):
        records.extend(load_section_records(output, z))
    if not rows and not records:
        return None
    known = {record.id: (record.z, record.parent_id) for record in records}
    links = collect_links(output, known, refresh=refresh, http_get=http_get)

    # Targets, and the locations a target can borrow its seed from. Every other location is skipped:
    # on a real volume that is three quarters of them.
    stale = masked - set(known)
    if stale:
        _logger.warning(
            "shapes: %d location(s) in the manifest have no current ingest record (deleted or changed in "
            "OData since the crops were made); they get no shape. -Update removes their crops.",
            len(stale),
        )
    wanted = set(masked)
    for location in masked:
        wanted.update(links.get(location, ()))
    lines: list[str] = []
    for record in sorted(records, key=lambda item: (item.z, item.id)):
        if record.id not in wanted:
            continue
        shape = location_shape(record)
        if shape is None:
            continue
        entry = {
            "location_id": record.id,
            "z": record.z,
            "structure_id": record.parent_id,
            "structure_label": "" if record.category_name() == "unlabeled" else record.category_name(),
            "has_mask": record.id in masked,
            "linked": [[other, known[other][0]] for other in links.get(record.id, [])],
            **shape,
        }
        lines.append(json.dumps(entry, separators=(",", ":")))
    path = output / SHAPES_FILENAME
    _write_if_changed(path, "\n".join(lines) + ("\n" if lines else ""))

    if rows:
        decorated: list[str] = []
        for row in rows:
            ids = [int(i) for i in row.get("locationIds", [])]
            row["structureIds"] = [known[i][1] if i in known else None for i in ids]
            row["linkedIds"] = [links.get(i, []) for i in ids]
            decorated.append(json.dumps(row, separators=(",", ":")))
        _write_if_changed(output / "manifest.jsonl", "\n".join(decorated) + "\n")
    return path


def WriteLocationShapes(
    OutputPath: str | None = None,
    RefreshOData: bool = False,
    Update: bool = False,
    Force: bool = False,
    http_get: HttpGet | None = None,
    **kwargs: Any,
) -> None:
    """Pipeline entry for ExportTrainingData: regenerate the volume's shapes file and manifest fields."""
    del kwargs
    from nornir_buildmanager.exceptions import NornirUserException

    if not OutputPath:
        raise NornirUserException("ExportTrainingData needs an output folder (-Output)")
    write_location_shapes(OutputPath, refresh=bool(RefreshOData or Update or Force), http_get=http_get)
