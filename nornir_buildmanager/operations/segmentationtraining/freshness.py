"""Section and imageKey watermarks for skip / mask-only / resume."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord


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
    geometry_version: str = "viking-catmull-v1"
    tile_x_dim: int = 0
    tile_y_dim: int = 0
    crop_format: str = "png"

    def hash(self) -> str:
        payload = json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "downsample": self.downsample,
            "ix0": self.ix0,
            "ix1": self.ix1,
            "iy0": self.iy0,
            "iy1": self.iy1,
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
        )

    def same_tiles(self, other: ImageWatermark) -> bool:
        return (
            self.downsample == other.downsample
            and self.ix0 == other.ix0
            and self.ix1 == other.ix1
            and self.iy0 == other.iy0
            and self.iy1 == other.iy1
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
) -> bool:
    """True when ExportSectionCrops may skip this Z entirely."""
    if force or watermark is None:
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


def image_output_fresh(path: Path, watermark_mtime: float | None) -> bool:
    """True when an imageKey file exists and is at least as new as the watermark."""
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
) -> str:
    """Return ``skip``, ``mask_only``, or ``full`` for one shared crop."""
    if force or previous is None:
        return "full"
    if params_hash != (previous_params_hash or ""):
        return "full"
    if not _mtime_equal(tileset_mtime, previous_tileset_mtime):
        return "full"
    if not previous.same_tiles(current):
        return "full"
    if not image_path.is_file():
        return "full"
    members_match = (
        set(previous.member_ids) == set(current.member_ids)
        and previous.max_last_modified == current.max_last_modified
    )
    if members_match and json_path.is_file() and image_output_fresh(image_path, tileset_mtime):
        return "skip"
    return "mask_only"
