"""Parse OGC POLYGON / MULTIPOLYGON WKT into mosaic rings."""

from __future__ import annotations

import re
from typing import Any, Sequence

from nornir_buildmanager.operations.segmentationtraining.records import (
    SKIP_MASK_TYPE_CODES,
    LocationRecord,
    parse_datetime,
)

Ring = tuple[tuple[float, float], ...]
PolygonRings = tuple[Ring, ...]  # exterior first, then holes

_COORD = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_SKIP_WKT_TYPES = frozenset({
    "POINT",
    "MULTIPOINT",
    "LINESTRING",
    "MULTILINESTRING",
    "POLYLINE",
    "MULTIPOLYLINE",
    "CIRCLE",
})


def is_curve_polygon(wkt: str) -> bool:
    """True for SQL CURVEPOLYGON/CIRCULARSTRING (Viking circles), not TypeCode 6."""
    return "CURVEPOLYGON" in wkt.upper() or "CIRCULARSTRING" in wkt.upper()


def wkt_type_token(wkt: str) -> str:
    """Leading WKT type word, e.g. POINT or POLYGON."""
    index = 0
    stripped = wkt.strip()
    while index < len(stripped) and (stripped[index].isalpha() or stripped[index] == "_"):
        index += 1
    return stripped[:index].upper()


def is_skippable_wkt(wkt: str) -> bool:
    """True for point, polyline, or circle WKT that cannot form a filled mask."""
    if is_curve_polygon(wkt):
        return True
    return wkt_type_token(wkt) in _SKIP_WKT_TYPES


def is_skippable_location(type_code: int | None, wkt: str) -> bool:
    """True when TypeCode or WKT is a point, polyline, or circle."""
    if type_code is not None and int(type_code) in SKIP_MASK_TYPE_CODES:
        return True
    return is_skippable_wkt(wkt)


def parse_wkt_polygons(wkt: str) -> list[PolygonRings]:
    """Return one (exterior, *holes) tuple per POLYGON in a WKT string.

    Point, polyline, and circle WKT return an empty list so a mis-coded
    annotation does not abort the section.
    """
    stripped = wkt.strip()
    if is_skippable_wkt(stripped):
        return []
    upper = stripped.upper()
    if upper.startswith("MULTIPOLYGON"):
        body = _paren_body(stripped, "MULTIPOLYGON")
        return [_parse_polygon_body(part) for part in _split_top_level(body)]
    if upper.startswith("POLYGON"):
        body = _paren_body(stripped, "POLYGON")
        return [_parse_polygon_body(body)]
    raise ValueError(f"Unsupported WKT type: {wkt[:40]}")


def mosaic_wkt_from_odata_shape(shape: object) -> str | None:
    """Extract WellKnownText from an OData MosaicShape payload."""
    if shape is None:
        return None
    if isinstance(shape, str):
        return shape
    if not isinstance(shape, dict):
        return None
    geometry = shape.get("Geometry") or shape.get("geometry") or shape
    if isinstance(geometry, str):
        return geometry
    if isinstance(geometry, dict):
        wkt = geometry.get("WellKnownText") or geometry.get("wellKnownText")
        if isinstance(wkt, str) and wkt:
            return wkt
        if geometry.get("type") == "Polygon" and "coordinates" in geometry:
            return _geojson_polygon_to_wkt(geometry["coordinates"])
        if geometry.get("type") == "MultiPolygon" and "coordinates" in geometry:
            parts = [_geojson_polygon_to_wkt(poly) for poly in geometry["coordinates"]]
            interiors = ",".join(p[len("POLYGON"):] for p in parts)
            return f"MULTIPOLYGON({interiors})"
    return None


def _paren_body(wkt: str, keyword: str) -> str:
    start = wkt.upper().find(keyword)
    open_paren = wkt.find("(", start)
    if open_paren < 0:
        raise ValueError("WKT missing opening parenthesis")
    depth = 0
    for index, char in enumerate(wkt[open_paren:], start=open_paren):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return wkt[open_paren + 1:index]
    raise ValueError("WKT is not balanced")


def _split_top_level(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(body):
        if char == "(":
            if depth == 0:
                start = index
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                parts.append(body[start:index + 1])
    return parts


def _parse_polygon_body(body: str) -> PolygonRings:
    rings_src = _split_top_level(body)
    if not rings_src:
        raise ValueError("POLYGON has no rings")
    rings = tuple(_parse_ring(ring) for ring in rings_src)
    return rings


def _parse_ring(ring: str) -> Ring:
    inner = ring.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    numbers = [float(match.group(0)) for match in _COORD.finditer(inner)]
    if len(numbers) < 6 or len(numbers) % 2:
        raise ValueError("Ring does not contain coordinate pairs")
    pairs = [(numbers[i], numbers[i + 1]) for i in range(0, len(numbers), 2)]
    return tuple(pairs)


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


def _geojson_polygon_to_wkt(coordinates: Sequence) -> str:
    ring_text = []
    for ring in coordinates:
        coords = ",".join(f"{pt[0]} {pt[1]}" for pt in ring)
        ring_text.append(f"({coords})")
    return "POLYGON(" + ",".join(ring_text) + ")"
