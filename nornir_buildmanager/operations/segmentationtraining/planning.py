"""Per-section crop plans: closed-form D, whole-tile snap, exact-key groups."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from nornir_buildmanager.operations.segmentationtraining.geometry import (
    SnapPlan,
    TileRect,
    downsample_and_align_snap,
    mosaic_bbox,
    pad_bbox,
)
from nornir_buildmanager.operations.segmentationtraining.grouping import CropGroup, group_snaps
from nornir_buildmanager.operations.segmentationtraining.curves import hydrate_polygons
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord
from nornir_buildmanager.operations.segmentationtraining.wkt import (
    PolygonRings,
    is_skippable_location,
    parse_wkt_polygons,
)

_logger = logging.getLogger(__name__)


@dataclass
class PlannedCrop:
    """One shared output image after snap and same-D reuse grouping."""

    group: CropGroup
    z: int
    image_key: str
    window_of: dict[int, tuple[int, int]] = field(default_factory=dict)

    @property
    def snap(self) -> TileRect:
        return self.group.snap

    @property
    def downsample(self) -> int:
        return self.group.downsample

    @property
    def location_ids(self) -> list[int]:
        return self.group.location_ids


def overlap_tiles_for(max_tiles: int) -> int:
    """~25% of the window, at least one tile."""
    return max(1, max_tiles // 4)


def plan_section_crops(
    records: list[LocationRecord],
    *,
    pad: float,
    finest: int,
    available: list[int],
    max_texture: int,
    min_process_pixels: int,
    tile_x_dim: int,
    tile_y_dim: int,
    max_tiles_x: int,
    max_tiles_y: int,
    mosaic_bounds: tuple[float, float, float, float] | None = None,
    volume: str = "volume",
) -> tuple[list[PlannedCrop], dict[int, list[PolygonRings]]]:
    """Build shared-image groups for one Z. Returns plans and parsed WKT rings."""
    del max_tiles_x, max_tiles_y
    polygons_by_id: dict[int, list[PolygonRings]] = {}
    prepared: list[tuple[LocationRecord, float, float, float, float]] = []
    for record in records:
        if is_skippable_location(record.type_code, record.wkt):
            _logger.info(
                "ExportAnnotationCrops: skip location %s, point/polyline/circle annotation",
                record.id,
            )
            continue
        polygons = hydrate_polygons(parse_wkt_polygons(record.wkt), record.type_code)
        if not polygons:
            _logger.info(
                "ExportAnnotationCrops: skip location %s, no polygon rings",
                record.id,
            )
            continue
        polygons_by_id[record.id] = polygons
        min_x, min_y, max_x, max_y = mosaic_bbox(polygons)
        padded = pad_bbox(min_x, min_y, max_x, max_y, pad, mosaic_bounds)
        prepared.append((record, *padded))
    prepared.sort(key=lambda item: (item[1], item[2], item[0].id))

    per_d: dict[int, list[tuple[int, TileRect, TileRect, int]]] = {}
    for record, min_x, min_y, max_x, max_y in prepared:
        planned = _downsample_and_snap(
            min_x,
            min_y,
            max_x,
            max_y,
            finest=finest,
            available=available,
            max_texture=max_texture,
            min_process_pixels=min_process_pixels,
            tile_x_dim=tile_x_dim,
            tile_y_dim=tile_y_dim,
        )
        if planned is None:
            _logger.info(
                "ExportAnnotationCrops: skip location %s, snapped window exceeds max_texture %s",
                record.id,
                max_texture,
            )
            continue
        per_d.setdefault(planned.downsample, []).append(
            (record.id, planned.tight, planned.window, planned.downsample)
        )

    groups: list[CropGroup] = []
    for downsample in sorted(per_d):
        items = per_d[downsample]
        items.sort(key=lambda item: (item[2].ix0, item[2].iy0, item[0]))
        groups.extend(group_snaps(items))

    z = records[0].z if records else 0
    plans = [
        PlannedCrop(group=group, z=z, image_key=group.image_key_for(z, volume))
        for group in groups
    ]
    plans.sort(key=lambda plan: (plan.downsample, plan.snap.ix0, plan.snap.iy0, plan.image_key))
    _assign_window_indices(plans)
    return plans, polygons_by_id


def _downsample_and_snap(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    *,
    finest: int,
    available: list[int],
    max_texture: int,
    min_process_pixels: int,
    tile_x_dim: int,
    tile_y_dim: int,
) -> SnapPlan | None:
    """Finest D whose MaxTexture window grows from the annotation's min tile."""
    return downsample_and_align_snap(
        min_x,
        min_y,
        max_x,
        max_y,
        finest=finest,
        available=available,
        max_texture=max_texture,
        min_process_pixels=min_process_pixels,
        tile_x_dim=tile_x_dim,
        tile_y_dim=tile_y_dim,
    )


def _assign_window_indices(plans: list[PlannedCrop]) -> None:
    by_id: dict[int, list[PlannedCrop]] = {}
    for plan in plans:
        for location_id in plan.location_ids:
            by_id.setdefault(location_id, []).append(plan)
    for location_id, hosts in by_id.items():
        hosts.sort(key=lambda plan: (plan.snap.ix0, plan.snap.iy0, plan.image_key))
        count = len(hosts)
        for index, plan in enumerate(hosts):
            plan.window_of[location_id] = (index, count)
