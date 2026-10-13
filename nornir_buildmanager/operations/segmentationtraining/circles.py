"""``circle_annotations.json``: one circle per annotation, keyed by crop ``imageKey``.

The SAM2 trainer prompts with circles. This module derives them from data the
exporter already holds and writes one file per volume next to ``manifest.jsonl``.

* ``annotated``: a Viking circle location (OData ``TypeCode == 1``). Center comes
  from its ``CURVEPOLYGON(CIRCULARSTRING(...))`` MosaicShape, radius from the
  stored ``Radius`` property.
* ``inscribed``: a polygon location that has a mask. The circle is the largest
  circle that fits inside the polygon's exterior ring (holes are ignored), found
  with a Euclidean distance transform. The centroid and equal-area radius are
  deliberately not used because a C-shaped polygon's centroid can lie outside it.

Unit of the OData ``Radius`` property
-------------------------------------
Mosaic pixels at downsample 1. Evidence: the SQL migration that built
``MosaicShape`` from the legacy columns writes the circle as
``CIRCULARSTRING(X-Radius Y, X Y+Radius, X+Radius Y, ...)`` where X and Y are the
mosaic-space center, so Radius is expressed in the same space as MosaicShape
(``SqlGeometryUtils.Extensions.ToCircle`` builds the same ring). The web client
multiplies ``Location.Radius`` by an XY scale to reach nanometers, which confirms
it is a pixel quantity. No fixture in this repo carries a real circle, so this
was not checked against live data.

Stored ``radius`` values are the true radius: the annotated circle as drawn, or
the full inscribed circle of a polygon. Shrinking a circle into a prompt is the
consumer's job (the trainer's ``prompt.circle_diameter_scale``), so the scale can
change without a re-export. ``diameter_scale`` stays in the file, fixed at 1.0,
so a consumer can tell these radii from files written by older exporters that
baked a scale in.

Coordinates are crop pixels at the crop's own downsample, x right and y down,
row 0 being the top row of the crop image (mosaic MinY, see
``geometry.MOSAIC_Y_INCREASES_WITH_IMAGE_ROW``). Centers and radii are never
clipped, so a center may lie outside the crop.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from nornir_buildmanager.operations.segmentationtraining import geometry
from nornir_buildmanager.operations.segmentationtraining.product_index import IMAGES_FOLDER
from nornir_buildmanager.operations.segmentationtraining.curves import hydrate_polygons
from nornir_buildmanager.operations.segmentationtraining.geometry import CropWindow
from nornir_buildmanager.operations.segmentationtraining.ingest import (
    load_circle_records,
    load_section_records,
    work_dir,
)
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord, LocationType
from nornir_buildmanager.operations.segmentationtraining.wkt import (
    PolygonRings,
    Ring,
    parse_wkt_polygons,
)

_logger = logging.getLogger(__name__)

CIRCLE_ANNOTATIONS_FILENAME = "circle_annotations.json"
CIRCLE_ANNOTATIONS_VERSION = 1
#: Recorded as ``diameter_scale``: the radii in the file are unscaled.
FAITHFUL_DIAMETER_SCALE = 1.0
SOURCE_ANNOTATED = "annotated"
SOURCE_INSCRIBED = "inscribed"

# Longest raster side for the inscribed-circle search, and the most the raster is
# magnified for small polygons. Exact boundary distances refine the raster answer.
MAX_RASTER_SIDE = 2048
MAX_RASTER_MAGNIFICATION = 8.0
_RASTER_PAD = 2

# Bump to invalidate the per-section inscribed-circle cache after an algorithm change.
_INSCRIBED_CACHE_VERSION = 1

# Origins can be negative: a window that hangs over the mosaic edge is keyed X-512-512 (x0 = -512, x1 = 512).
_IMAGE_KEY_WINDOW = re.compile(r"_D(\d+)_X(-?\d+)-(-?\d+)_Y(-?\d+)-(-?\d+)$")


@dataclass(frozen=True)
class MosaicCircle:
    """One circle in mosaic space at downsample 1, before the diameter scale.

    Immutable and picklable; built once per location and converted into every
    crop window it touches.
    """

    location_id: int
    center_x: float
    center_y: float
    radius: float
    structure_label: str
    has_polygon_mask: bool
    source: str


@dataclass(frozen=True)
class CropRef:
    """A crop image: its ``imageKey``, downsample, and pixel window."""

    image_key: str
    downsample: int
    window: CropWindow


def window_from_image_key(image_key: str) -> tuple[int, CropWindow] | None:
    """Decode ``..._D{ds}_X{x0}-{x1}_Y{y0}-{y1}`` into downsample and pixel window.

    ``GroupWindows`` builds the key from the window's pixel extents at the crop's
    downsample, so the key alone is enough to locate a crop in mosaic space.
    Returns None for a key that does not follow the pattern.
    """
    match = _IMAGE_KEY_WINDOW.search(image_key)
    if match is None:
        return None
    downsample, x0, x1, y0, y1 = (int(group) for group in match.groups())
    if downsample <= 0 or x1 <= x0 or y1 <= y0:
        return None
    return downsample, CropWindow(origin_x=x0, origin_y=y0, width=x1 - x0, height=y1 - y0)


def circle_in_crop_pixels(
    center_x: float,
    center_y: float,
    radius: float,
    *,
    window: CropWindow,
    downsample: float,
) -> tuple[float, float, float]:
    """Convert a mosaic circle to crop pixels: ``(mosaic - origin * D) / D``.

    No clipping. The y flip applies only if ``MOSAIC_Y_INCREASES_WITH_IMAGE_ROW``
    is False, mirroring ``pixel_rings_in_crop``.
    """
    scale = float(downsample)
    origin_x, origin_y = window.mosaic_origin(scale)
    px = (center_x - origin_x) / scale
    py = (center_y - origin_y) / scale
    if not geometry.MOSAIC_Y_INCREASES_WITH_IMAGE_ROW:
        py = window.height - py
    return px, py, radius / scale


def circle_intersects_window(
    px: float, py: float, radius: float, *, width: int, height: int
) -> bool:
    """True when the disc overlaps the ``[0, width] x [0, height]`` crop area.

    A disc that only touches the crop edge at a point does not count.
    """
    nearest_x = min(max(px, 0.0), float(width))
    nearest_y = min(max(py, 0.0), float(height))
    return math.hypot(px - nearest_x, py - nearest_y) < radius


def circle_geometry_from_wkt(wkt: str) -> tuple[float, float, float] | None:
    """Center and radius from a circle MosaicShape, or None when unparseable.

    Viking stores ``CURVEPOLYGON(CIRCULARSTRING(...))`` with points on the
    circle (east, north, west, south, east), each optionally carrying a Z. The
    center is the circumcenter of three consecutive points, which is exact for
    any arc, and the radius is the distance to the first point. ``CIRCLE((x y), r)``
    is accepted too.
    """
    text = wkt.strip()
    if text.upper().startswith("CIRCLE"):
        numbers = [float(token) for token in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", text)]
        if len(numbers) >= 3 and numbers[2] > 0:
            return numbers[0], numbers[1], numbers[2]
        return None
    start = text.find("(", text.upper().find("CIRCULARSTRING"))
    if "CIRCULARSTRING" not in text.upper() or start < 0:
        return None
    points: list[tuple[float, float]] = []
    for chunk in text[start + 1:].replace(")", " ").replace("(", " ").split(","):
        values = [float(token) for token in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", chunk)]
        if len(values) >= 2:
            points.append((values[0], values[1]))
    if len(points) < 3:
        return None
    center = _circumcenter(points[0], points[1], points[2])
    if center is None:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        center = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
    radius = math.hypot(points[0][0] - center[0], points[0][1] - center[1])
    if radius <= 0:
        return None
    return center[0], center[1], radius


def _circumcenter(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> tuple[float, float] | None:
    determinant = 2.0 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(determinant) < 1e-9:
        return None
    a2 = a[0] ** 2 + a[1] ** 2
    b2 = b[0] ** 2 + b[1] ** 2
    c2 = c[0] ** 2 + c[1] ** 2
    ux = (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / determinant
    uy = (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / determinant
    return ux, uy


def annotated_circle(record: LocationRecord) -> MosaicCircle | None:
    """Circle for a ``TypeCode == 1`` record, or None when the shape is unusable.

    Radius is the stored ``Radius`` property (mosaic px at D1) when positive,
    otherwise the radius implied by the WKT.
    """
    if record.type_code is not None and int(record.type_code) != int(LocationType.CIRCLE):
        return None
    shape = circle_geometry_from_wkt(record.wkt)
    if shape is None:
        _logger.info("circle_annotations: skip circle %s, unparseable shape", record.id)
        return None
    center_x, center_y, wkt_radius = shape
    radius = float(record.radius) if record.radius is not None and record.radius > 0 else wkt_radius
    return MosaicCircle(
        location_id=record.id,
        center_x=center_x,
        center_y=center_y,
        radius=radius,
        structure_label=_label(record),
        has_polygon_mask=False,
        source=SOURCE_ANNOTATED,
    )


def _label(record: LocationRecord) -> str:
    """Category the exporter writes to the crop sidecar, or empty when unlabeled."""
    name = record.category_name()
    return "" if name == "unlabeled" else name


def max_inscribed_circle(
    polygons: Sequence[PolygonRings],
    *,
    max_side: int = MAX_RASTER_SIDE,
) -> tuple[float, float, float] | None:
    """Largest circle inside the filled exterior ring(s): ``(center_x, center_y, radius)``.

    *polygons* is the output of ``parse_wkt_polygons`` (a list of
    ``(exterior, *holes)`` tuples). Holes are ignored on purpose: the
    circle is a prompt for the whole object. For several parts the best circle
    over all parts wins.

    The polygon is rasterized in mosaic space (side capped at *max_side*, small
    polygons magnified for sub-pixel precision), the Euclidean distance transform
    gives the clearance of every pixel, and its argmax seeds a pattern search on
    the exact point-to-boundary distance so the result is not limited to the
    raster resolution. Returns None for a degenerate polygon.
    """
    exteriors = _exteriors(polygons)
    if not exteriors:
        return None
    all_x = [pt[0] for ring in exteriors for pt in ring]
    all_y = [pt[1] for ring in exteriors for pt in ring]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    extent = max(max_x - min_x, max_y - min_y)
    if extent <= 0:
        return None
    scale = min(MAX_RASTER_MAGNIFICATION, (max_side - 2 * _RASTER_PAD - 1) / extent)
    width = int(math.ceil((max_x - min_x) * scale)) + 2 * _RASTER_PAD + 1
    height = int(math.ceil((max_y - min_y) * scale)) + 2 * _RASTER_PAD + 1
    image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(image)
    for ring in exteriors:
        draw.polygon(
            [((x - min_x) * scale + _RASTER_PAD, (y - min_y) * scale + _RASTER_PAD) for x, y in ring],
            fill=1,
        )
    mask = np.asarray(image, dtype=bool)
    if not mask.any():
        return None
    clearance = ndimage.distance_transform_edt(mask)
    row, col = np.unravel_index(int(np.argmax(clearance)), clearance.shape)
    seed_x = (col - _RASTER_PAD) / scale + min_x
    seed_y = (row - _RASTER_PAD) / scale + min_y
    edges = _edge_arrays(exteriors)
    center_x, center_y, radius = _refine_center(seed_x, seed_y, 1.0 / scale, edges)
    if radius <= 0:
        return None
    return center_x, center_y, radius


def _exteriors(polygons: Sequence[PolygonRings]) -> list[Ring]:
    """Exterior ring of every part that has at least three vertices."""
    return [rings[0] for rings in polygons if rings and len(rings[0]) >= 3]


def _edge_arrays(rings: Sequence[Ring]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    ax: list[float] = []
    ay: list[float] = []
    bx: list[float] = []
    by: list[float] = []
    for ring in rings:
        points = list(ring)
        if points[0] != points[-1]:
            points.append(points[0])
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            ax.append(x0)
            ay.append(y0)
            bx.append(x1)
            by.append(y1)
    return np.array(ax), np.array(ay), np.array(bx), np.array(by)


def _signed_clearance(
    x: float,
    y: float,
    edges: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
) -> float:
    """Distance to the nearest edge, negative when (x, y) is outside every ring (even-odd)."""
    ax, ay, bx, by = edges
    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(length_sq > 0, ((x - ax) * dx + (y - ay) * dy) / length_sq, 0.0)
    t = np.clip(t, 0.0, 1.0)
    distance = float(np.min(np.hypot(x - (ax + t * dx), y - (ay + t * dy))))
    crossing = (ay > y) != (by > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        x_at_y = ax + (y - ay) * dx / np.where(dy != 0, dy, 1.0)
    inside = int(np.count_nonzero(crossing & (x < x_at_y))) % 2 == 1
    return distance if inside else -distance


def _refine_center(
    x: float,
    y: float,
    step: float,
    edges: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
) -> tuple[float, float, float]:
    """Pattern search for the point of greatest exact clearance near the raster seed."""
    best = _signed_clearance(x, y, edges)
    step = max(step, 1e-6)
    for _ in range(100):
        improved = False
        for dx, dy in (
            (step, 0.0), (-step, 0.0), (0.0, step), (0.0, -step),
            (step, step), (step, -step), (-step, step), (-step, -step),
        ):
            candidate = _signed_clearance(x + dx, y + dy, edges)
            if candidate > best:
                best, x, y, improved = candidate, x + dx, y + dy, True
        if not improved:
            step /= 2.0
            if step < 1e-3:
                break
    return x, y, best


def cells_for_circles(
    crops: Sequence[CropRef],
    circles: Iterable[MosaicCircle],
) -> dict[str, list[dict[str, Any]]]:
    """Per-``imageKey`` entries for every circle that overlaps each crop window.

    Pure: no I/O. Crops that no circle touches are omitted. The radius is the
    true radius, so a crop lists every circle that reaches it at full size; a
    consumer that shrinks circles only ever draws less of them.
    """
    cells: dict[str, list[dict[str, Any]]] = {}
    circle_list = list(circles)
    for crop in crops:
        entries: list[dict[str, Any]] = []
        for circle in circle_list:
            px, py, radius = circle_in_crop_pixels(
                circle.center_x,
                circle.center_y,
                circle.radius,
                window=crop.window,
                downsample=crop.downsample,
            )
            if not circle_intersects_window(
                px, py, radius, width=crop.window.width, height=crop.window.height
            ):
                continue
            entries.append(
                {
                    "location_id": circle.location_id,
                    "center_x": round(px, 3),
                    "center_y": round(py, 3),
                    "radius": round(radius, 3),
                    "structure_label": circle.structure_label,
                    "has_polygon_mask": circle.has_polygon_mask,
                    "source": circle.source,
                }
            )
        if entries:
            entries.sort(key=lambda item: item["location_id"])
            cells[crop.image_key] = entries
    return cells


def build_circle_document(cells: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Wrap *cells* in the version-1 file layout with keys in stable order."""
    return {
        "version": CIRCLE_ANNOTATIONS_VERSION,
        "diameter_scale": FAITHFUL_DIAMETER_SCALE,
        "grid_cells": {key: cells[key] for key in sorted(cells)},
    }


def circle_annotations_path(output_path: str | os.PathLike[str]) -> Path:
    """``{AnnotationCrops}/circle_annotations.json``."""
    return Path(output_path) / CIRCLE_ANNOTATIONS_FILENAME


def section_circles(
    output_path: str | os.PathLike[str],
    z: int,
    member_ids: set[int],
) -> list[MosaicCircle]:
    """Annotated and inscribed circles for one section.

    *member_ids* are the locations that appear in a manifest row (they have
    masks). Inscribed circles are cached per section in
    ``_work/circle_inscribed_{z}.json`` keyed by location id and LastModified,
    because the distance transform dominates the cost of a no-op rerun.
    """
    circles: list[MosaicCircle] = []
    for record in load_circle_records(output_path, z):
        circle = annotated_circle(record)
        if circle is not None:
            circles.append(circle)
    if not member_ids:
        return circles
    cache_path = work_dir(output_path) / f"circle_inscribed_{z}.json"
    cache = _load_inscribed_cache(cache_path)
    fresh: dict[str, Any] = {}
    for record in load_section_records(output_path, z):
        if record.id not in member_ids:
            continue
        stamp = record.last_modified.isoformat()
        cached = cache.get(str(record.id))
        if cached is not None and cached.get("lm") == stamp:
            shape = cached
        else:
            found = _inscribed_for_record(record)
            shape = {"lm": stamp, "circle": list(found) if found else None}
        fresh[str(record.id)] = shape
        if shape.get("circle"):
            center_x, center_y, radius = shape["circle"]
            circles.append(
                MosaicCircle(
                    location_id=record.id,
                    center_x=float(center_x),
                    center_y=float(center_y),
                    radius=float(radius),
                    structure_label=_label(record),
                    has_polygon_mask=True,
                    source=SOURCE_INSCRIBED,
                )
            )
    if fresh != cache:
        _save_inscribed_cache(cache_path, fresh)
    return circles


def _inscribed_for_record(record: LocationRecord) -> tuple[float, float, float] | None:
    try:
        polygons = hydrate_polygons(parse_wkt_polygons(record.wkt), record.type_code)
    except ValueError:
        _logger.info("circle_annotations: skip location %s, unparseable polygon", record.id)
        return None
    return max_inscribed_circle(polygons)


def _load_inscribed_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("version") != _INSCRIBED_CACHE_VERSION:
        return {}
    items = payload.get("items")
    return items if isinstance(items, dict) else {}


def _save_inscribed_cache(path: Path, items: dict[str, Any]) -> None:
    payload = {"version": _INSCRIBED_CACHE_VERSION, "items": items}
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


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


def write_circle_annotations(
    output_path: str | os.PathLike[str],
    *,
    image_exists: Callable[[str], bool] | None = None,
) -> Path | None:
    """Regenerate ``circle_annotations.json`` from the manifest and circle spills.

    The whole file is rebuilt from current data on every call, so it cannot go
    stale after an export, ``-Update``, ``-Repair`` or ``-Cleanup``: only
    manifest rows whose crop image still exists contribute, and sections or crops
    that vanished simply produce no entries. Call this once per volume run (it
    reads ``manifest.jsonl`` once); it is not thread safe against concurrent
    manifest writers.

    Radii are written unscaled (``diameter_scale`` 1.0). *image_exists* receives the crop PNG basename;
    the default consults the shared product index.

    Returns the path written, or None when there is nothing to write (no
    manifest and no existing file). An unchanged file is not rewritten.
    """
    output = Path(output_path)
    path = circle_annotations_path(output)
    rows = _read_manifest_rows(output)
    if not rows and not path.is_file():
        return None
    exists = image_exists if image_exists is not None else _default_image_exists(output)

    from nornir_buildmanager.operations.segmentationtraining.stitch import crop_image_filename

    crops_by_z: dict[int, list[CropRef]] = {}
    members_by_z: dict[int, set[int]] = {}
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("imageKey", ""))
        if not key or key in seen or not exists(crop_image_filename(key)):
            continue
        decoded = window_from_image_key(key)
        if decoded is None:
            _logger.warning("circle_annotations: cannot decode window from imageKey %s", key)
            continue
        seen.add(key)
        z = int(row["z"])
        downsample = int(row.get("downsample") or decoded[0])
        crops_by_z.setdefault(z, []).append(CropRef(key, downsample, decoded[1]))
        members_by_z.setdefault(z, set()).update(int(i) for i in row.get("locationIds", []))

    cells: dict[str, list[dict[str, Any]]] = {}
    for z in sorted(crops_by_z):
        circles = section_circles(output, z, members_by_z[z])
        cells.update(cells_for_circles(crops_by_z[z], circles))

    document = build_circle_document(cells)
    text = json.dumps(document, indent=1)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return path
    temp = path.with_suffix(".json.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)
    return path


def _default_image_exists(output: Path) -> Callable[[str], bool]:
    from nornir_buildmanager.operations.segmentationtraining.product_index import get_product_index

    products = get_product_index(output)
    return lambda name: products.exists(IMAGES_FOLDER, name)


def WriteCircleAnnotations(
    OutputPath: str | None = None,
    **kwargs: Any,
) -> None:
    """Pipeline entry for ExportAnnotationCrops: regenerate the volume's circle file. Returns None."""
    del kwargs
    from nornir_buildmanager.exceptions import NornirUserException

    if not OutputPath:
        raise NornirUserException("ExportAnnotationCrops requires -Output")
    write_circle_annotations(OutputPath)
    return None
