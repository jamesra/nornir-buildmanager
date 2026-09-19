"""Shared Location records for SegmentationTraining ingest and crop."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any


SCHEMA_VERSION = 3

DEFAULT_ODATA_FILTER = (
    "(TypeCode eq 4 or TypeCode eq 6) and "
    "(Parent/TypeID eq 1 or Parent/Type/ParentID eq 1)"
)


class LocationType(IntEnum):
    """Viking ``Locations.TypeCode``. WKT is control points; only 6 is curve-fit."""

    POINT = 0
    CIRCLE = 1
    ELLIPSE = 2
    POLYLINE = 3
    POLYGON = 4
    OPENCURVE = 5
    CURVEPOLYGON = 6
    CLOSEDCURVE = 7


SKIP_MASK_TYPE_CODES = frozenset({
    LocationType.POINT,
    LocationType.CIRCLE,
    LocationType.ELLIPSE,
    LocationType.POLYLINE,
})


@dataclass(frozen=True)
class LocationRecord:
    """One annotation used for crop/mask, independent of ingest source.

    *type_code* is Viking ``Locations.TypeCode``. Type 6 stores control-point
    POLYGON WKT; the crop path Catmull-Rom-fits those rings before rasterize.
    """

    id: int
    z: int
    wkt: str
    parent_id: int | None
    off_edge: bool
    last_modified: datetime
    type_id: int | None
    type_name: str | None
    structure_label: str | None
    type_code: int | None = None
    radius: float | None = None

    def to_json(self) -> dict[str, Any]:
        """Serialize for section JSONL. Keep this shape stable for a later SQL ingest."""
        payload = asdict(self)
        payload.pop("last_modified")
        payload["lastModified"] = self.last_modified.astimezone(timezone.utc).isoformat()
        payload["parentId"] = payload.pop("parent_id")
        payload["offEdge"] = payload.pop("off_edge")
        payload["typeId"] = payload.pop("type_id")
        payload["typeName"] = payload.pop("type_name")
        payload["structureLabel"] = payload.pop("structure_label")
        payload["typeCode"] = payload.pop("type_code")
        payload["radius"] = payload.pop("radius")
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> LocationRecord:
        """Load a cache JSONL row written by :meth:`to_json`."""
        return cls(
            id=int(payload["id"]),
            z=int(payload["z"]),
            wkt=str(payload["wkt"]),
            parent_id=_optional_int(payload.get("parentId", payload.get("parent_id"))),
            off_edge=bool(payload.get("offEdge", payload.get("off_edge", False))),
            last_modified=parse_datetime(payload.get("lastModified") or payload.get("last_modified")),
            type_id=_optional_int(payload.get("typeId", payload.get("type_id"))),
            type_name=_optional_str(payload.get("typeName", payload.get("type_name"))),
            structure_label=_optional_str(payload.get("structureLabel", payload.get("structure_label"))),
            type_code=_optional_int(payload.get("typeCode", payload.get("type_code"))),
            radius=_optional_float(payload.get("radius")),
        )

    def category_name(self) -> str:
        """COCO category: structure Label, else type name, else unlabeled."""
        if self.structure_label:
            return self.structure_label
        if self.type_name:
            return self.type_name
        return "unlabeled"


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def parse_datetime(value: Any) -> datetime:
    """Parse OData or ISO timestamps to aware UTC."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if text.startswith("/Date(") and text.endswith(")/"):
        millis = int(text[6:-2].split("+")[0].split("-")[0])
        return datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
