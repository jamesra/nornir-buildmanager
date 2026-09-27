"""Section and imageKey watermarks for skip / mask-only / resume."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from nornir_imageregistration.type_info import Shape

from nornir_buildmanager.operations.segmentationtraining.product_index import CropProductIndex
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord

EXPORTER_TILED = "tiled"
EXPORTER_LEGACY = "legacy"
GEOMETRY_TILED = "fixed-d-halfstep-v1"
GEOMETRY_LEGACY = "coarsen-to-fit-v1"

_GEOMETRY_BY_EXPORTER = {
    EXPORTER_TILED: GEOMETRY_TILED,
    EXPORTER_LEGACY: GEOMETRY_LEGACY,
}


def geometry_version_for(exporter: str) -> str:
    """Watermark version for an ExportAnnotationCrops -Exporter value.

    ``tiled`` keeps the existing half-step version so current section watermarks
    still match. ``legacy`` is a different version so those sections rebuild.
    """
    key = (exporter or EXPORTER_TILED).strip().lower()
    try:
        return _GEOMETRY_BY_EXPORTER[key]
    except KeyError:
        raise ValueError(
            f"Exporter must be {EXPORTER_TILED} or {EXPORTER_LEGACY}, got {exporter!r}"
        ) from None


@dataclass(frozen=True)
class ExportParams:
    """Subset of flags that invalidate crops when they change."""

    pad: float
    downsample: int
    max_texture: int
    min_process_pixels: int
    include_off_edge: bool
    channel: str
    filter_name: str
    volume: str
    geometry_version: str = GEOMETRY_TILED
    tile_shape: Shape = Shape(y=0, x=0)
    crop_format: str = "png"

    def hash(self) -> str:
        """Hash the flags that invalidate a crop.

        ``tile_shape`` is written as ``tile_y_dim`` and ``tile_x_dim`` numbers so
        the digest matches the previous two-integer payload.
        """
        payload = {key: value for key, value in self.__dict__.items() if key != "tile_shape"}
        payload["tile_y_dim"] = self.tile_shape.y
        payload["tile_x_dim"] = self.tile_shape.x
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ResolvedTileset:
    """Tile size and level directories after a sampled PNG wins over the XML size.

    ``tile_shape`` is copied onto ``ExportParams`` so the freshness hash changes
    when the tile size changes. ``y`` is height and ``x`` is width. ``save_node``
    is the volume tileset to persist when that size was corrected.
    """

    tile_shape: Shape
    available: list[int]
    level_dirs: dict[int, str]
    prefix: str
    postfix: str
    mtime: float | None
    save_node: Any = None


@dataclass(frozen=True)
class SectionCropRun:
    """Resolved tileset and runtime settings for one section. Not part of the freshness hash."""

    params: ExportParams
    tileset: ResolvedTileset
    exporter: str
    max_tiles_x: int
    max_tiles_y: int
    workers: int
    mask_workers: int
    stage_tiles: str | None
    overlay: bool


@dataclass(frozen=True)
class ImageWatermark:
    """Facts for one shared crop used for resume and mask-only."""

    key: str
    downsample: int
    ix0: int
    ix1: int
    iy0: int
    iy1: int
    member_ids: list[int]
    max_last_modified: str
    origin_x: int = 0
    origin_y: int = 0
    width: int = 0
    height: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "downsample": self.downsample,
            "ix0": self.ix0,
            "ix1": self.ix1,
            "iy0": self.iy0,
            "iy1": self.iy1,
            "originX": self.origin_x,
            "originY": self.origin_y,
            "width": self.width,
            "height": self.height,
            "memberIds": self.member_ids,
            "maxLastModified": self.max_last_modified,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> ImageWatermark:
        return cls(
            key=str(payload["key"]),
            downsample=int(payload.get("downsample", 1)),
            ix0=int(payload.get("ix0", 0)),
            ix1=int(payload.get("ix1", 0)),
            iy0=int(payload.get("iy0", 0)),
            iy1=int(payload.get("iy1", 0)),
            member_ids=[int(i) for i in payload.get("memberIds", [])],
            max_last_modified=str(payload.get("maxLastModified", "")),
            origin_x=int(payload.get("originX", 0)),
            origin_y=int(payload.get("originY", 0)),
            width=int(payload.get("width", 0)),
            height=int(payload.get("height", 0)),
        )

    def same_tiles(self, other: ImageWatermark) -> bool:
        """True when both marks describe the same pixel crop, not only the same tiles."""
        tiles_match = (
            self.downsample == other.downsample
            and self.ix0 == other.ix0
            and self.ix1 == other.ix1
            and self.iy0 == other.iy0
            and self.iy1 == other.iy1
        )
        if not tiles_match:
            return False
        # Legacy watermarks omit pixel extent; tile indices alone defined the crop.
        if self.width == 0 or self.height == 0 or other.width == 0 or other.height == 0:
            return True
        return (
            self.origin_x == other.origin_x
            and self.origin_y == other.origin_y
            and self.width == other.width
            and self.height == other.height
        )

    def same_members(self, other: ImageWatermark) -> bool:
        """True when membership and LastModified (DB-triggered on shape change) match."""
        return (
            set(self.member_ids) == set(other.member_ids)
            and self.max_last_modified == other.max_last_modified
        )


@dataclass
class SectionWatermark:
    """Last successful export facts for one Z."""

    ids: list[int]
    max_last_modified: str
    tileset_mtime: float | None
    params_hash: str
    downsample: int | None = None
    image_keys: list[str] | None = None
    images: list[ImageWatermark] | None = None

    def to_json(self) -> dict[str, Any]:
        images = self.images or []
        return {
            "ids": self.ids,
            "maxLastModified": self.max_last_modified,
            "tilesetMtime": self.tileset_mtime,
            "paramsHash": self.params_hash,
            "downsample": self.downsample,
            "imageKeys": self.image_keys or [item.key for item in images],
            "images": [item.to_json() for item in images],
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> SectionWatermark:
        images = [ImageWatermark.from_json(item) for item in payload.get("images") or []]
        return cls(
            ids=[int(i) for i in payload.get("ids", [])],
            max_last_modified=str(payload.get("maxLastModified", "")),
            tileset_mtime=payload.get("tilesetMtime"),
            params_hash=str(payload.get("paramsHash", "")),
            downsample=payload.get("downsample"),
            image_keys=list(payload.get("imageKeys") or [item.key for item in images]),
            images=images,
        )


def section_meta_path(output_path: str | Path, z: int) -> Path:
    return Path(output_path) / "_work" / f"section_{z}.meta.json"


def load_section_watermark(output_path: str | Path, z: int) -> SectionWatermark | None:
    path = section_meta_path(output_path, z)
    if not path.is_file():
        return None
    return SectionWatermark.from_json(json.loads(path.read_text(encoding="utf-8")))


def save_section_watermark(output_path: str | Path, z: int, watermark: SectionWatermark) -> None:
    path = section_meta_path(output_path, z)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(watermark.to_json(), indent=2), encoding="utf-8")


def id_set(records: Iterable[LocationRecord]) -> set[int]:
    return {record.id for record in records}


def max_last_modified(records: Iterable[LocationRecord]) -> datetime:
    newest = None
    for record in records:
        if newest is None or record.last_modified > newest:
            newest = record.last_modified
    if newest is None:
        return datetime.min
    return newest


def section_is_fresh(
    records: list[LocationRecord],
    *,
    watermark: SectionWatermark | None,
    params: ExportParams,
    tileset_mtime: float | None,
    force: bool,
    refresh_odata: bool = False,
) -> bool:
    """True when ExportSectionCrops may skip this Z entirely.

    ``refresh_odata`` always re-enters so per-crop LastModified checks can
    decide skip vs mask-only without doing a full restitch.
    """
    if force or refresh_odata or watermark is None:
        return False
    if set(watermark.ids) != id_set(records):
        return False
    if watermark.max_last_modified != max_last_modified(records).isoformat():
        return False
    if watermark.params_hash != params.hash():
        return False
    if not _mtime_equal(tileset_mtime, watermark.tileset_mtime):
        return False
    return True


def _mtime_equal(left: float | None, right: float | None) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return abs(float(left) - float(right)) <= 1e-6


def image_output_fresh(
    path: Path,
    watermark_mtime: float | None,
    *,
    products: CropProductIndex | None = None,
    folder: str = "images",
) -> bool:
    """True when an imageKey file exists and is at least as new as the watermark."""
    if products is not None:
        stamped = products.mtime(folder, path.name)
        if stamped is None:
            return False
        if watermark_mtime is None:
            return True
        return stamped + 1e-6 >= watermark_mtime
    if not path.is_file():
        return False
    if watermark_mtime is None:
        return True
    return path.stat().st_mtime + 1e-6 >= watermark_mtime


def image_rebuild_mode(
    current: ImageWatermark,
    previous: ImageWatermark | None,
    *,
    image_path: Path,
    json_path: Path,
    params_hash: str,
    previous_params_hash: str | None,
    tileset_mtime: float | None,
    previous_tileset_mtime: float | None,
    force: bool,
    refresh_odata: bool = False,
    products: CropProductIndex | None = None,
) -> str:
    """Return ``skip``, ``mask_only``, or ``full`` for one shared crop.

    ``section_is_fresh`` returns False for ``refresh_odata``, so this function
    is always invoked per-crop during a refresh. When ``same_members`` is True
    (same ids + unchanged LastModified, which the DB triggers on MosaicShape
    edits), the crop is skipped entirely. When LastModified changed the masks
    are regenerated without restitching the PNG.
    """
    del refresh_odata  # handled at section level; per-crop uses LastModified
    if force or previous is None:
        return "full"
    if params_hash != (previous_params_hash or ""):
        return "full"
    if not _mtime_equal(tileset_mtime, previous_tileset_mtime):
        return "full"
    if not previous.same_tiles(current):
        return "full"
    image_present = (
        products.exists("images", image_path.name)
        if products is not None
        else image_path.is_file()
    )
    json_present = (
        products.exists("images", json_path.name)
        if products is not None
        else json_path.is_file()
    )
    if not image_present:
        return "full"
    if previous.same_members(current) and json_present and image_output_fresh(
        image_path, tileset_mtime, products=products
    ):
        return "skip"
    return "mask_only"
