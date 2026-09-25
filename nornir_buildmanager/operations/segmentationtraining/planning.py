"""Per-section crop plans at a fixed downsample, grouped by identical windows."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from nornir_buildmanager.operations.segmentationtraining.curves import hydrate_polygons
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    EXPORTER_LEGACY,
    EXPORTER_TILED,
    ExportParams,
    ResolvedTileset,
)
from nornir_buildmanager.operations.segmentationtraining.geometry import (
    CropWindow,
    TileRect,
    downsample_and_align_snap,
    mosaic_bbox,
    pad_bbox,
    place_mask_windows,
)
from nornir_buildmanager.operations.segmentationtraining.grouping import (
    CropGroup,
    group_snaps,
    group_windows,
)
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord
from nornir_buildmanager.operations.segmentationtraining.wkt import (
    PolygonRings,
    is_skippable_location,
    parse_wkt_polygons,
)

_logger = logging.getLogger(__name__)


@dataclass
class PlannedCrop:
    """One shared output image after half-step placement and identical-window grouping."""

    group: CropGroup
    z: int
    image_key: str
    window_of: dict[int, tuple[int, int]] = field(default_factory=dict)

    @property
    def snap(self) -> TileRect:
        return self.group.snap

    @property
    def window(self) -> CropWindow:
        if self.group.window is None:
            raise RuntimeError(f"planned crop {self.image_key} has no pixel window")
        return self.group.window

    @property
    def downsample(self) -> int:
        return self.group.downsample

    @property
    def location_ids(self) -> list[int]:
        return self.group.location_ids

    def output_size(self) -> tuple[int, int]:
        """Pixel size of the training crop, not the covering tile span."""
        window = self.window
        return window.width, window.height

    def mosaic_origin(self) -> tuple[float, float]:
        """Mosaic coordinate of the crop's top-left pixel."""
        return self.window.mosaic_origin(self.downsample)


def overlap_tiles_for(max_tiles: int) -> int:
    """~25% of the window, at least one tile."""
    return max(1, max_tiles // 4)


def plan_section_crops(
    records: list[LocationRecord],
    *,
    params: ExportParams,
    tileset: ResolvedTileset,
    mosaic_bounds: tuple[float, float, float, float] | None = None,
    exporter: str = EXPORTER_TILED,
) -> tuple[list[PlannedCrop], dict[int, list[PolygonRings]]]:
    """Build shared-image groups for one Z.

    ``tiled`` stays at *downsample* and splits a mask that does not fit one crop.
    ``legacy`` coarsens D until the mask fits one crop and never splits.
    """
    pad = params.pad
    downsample = params.downsample
    available = tileset.available
    max_texture = params.max_texture
    min_process_pixels = params.min_process_pixels
    tile_x_dim = tileset.tile_x_dim
    tile_y_dim = tileset.tile_y_dim
    volume = params.volume
    level = max(int(downsample), 1)
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

    mode = (exporter or EXPORTER_TILED).strip().lower()
    if mode not in (EXPORTER_TILED, EXPORTER_LEGACY):
        raise ValueError(
            f"Exporter must be {EXPORTER_TILED} or {EXPORTER_LEGACY}, got {exporter!r}"
        )
    if mode == EXPORTER_LEGACY:
        groups = _legacy_groups(
            prepared,
            finest=level,
            available=available,
            max_texture=int(max_texture),
            min_process_pixels=int(min_process_pixels),
            tile_x_dim=tile_x_dim,
            tile_y_dim=tile_y_dim,
        )
    else:
        del available, min_process_pixels
        chosen: list[tuple[int, CropWindow]] = []
        for record, min_x, min_y, max_x, max_y in prepared:
            windows = place_mask_windows(
                polygons_by_id[record.id],
                (min_x, min_y, max_x, max_y),
                downsample=level,
                crop_width=int(max_texture),
                crop_height=int(max_texture),
            )
            if not windows:
                _logger.info(
                    "ExportAnnotationCrops: skip location %s, mask has no area at downsample %s",
                    record.id,
                    level,
                )
                continue
            for window in windows:
                chosen.append((record.id, window))
        groups = group_windows(
            chosen,
            downsample=level,
            tile_x_dim=tile_x_dim,
            tile_y_dim=tile_y_dim,
        )
    z = records[0].z if records else 0
    plans = [
        PlannedCrop(group=group, z=z, image_key=group.image_key_for(z, volume))
        for group in groups
    ]
    plans.sort(
        key=lambda plan: (
            plan.downsample,
            plan.window.origin_x,
            plan.window.origin_y,
            plan.image_key,
        )
    )
    _assign_window_indices(plans)
    return plans, polygons_by_id


def _legacy_groups(
    prepared: list[tuple[LocationRecord, float, float, float, float]],
    *,
    finest: int,
    available: list[int],
    max_texture: int,
    min_process_pixels: int,
    tile_x_dim: int,
    tile_y_dim: int,
) -> list[CropGroup]:
    """Coarsen each mask until it fits one crop, then share windows per D."""
    by_downsample: dict[int, list[tuple[int, TileRect, TileRect, int]]] = {}
    for record, min_x, min_y, max_x, max_y in prepared:
        snap = downsample_and_align_snap(
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
        if snap is None:
            _logger.info(
                "ExportAnnotationCrops: skip location %s, mask does not fit any available downsample",
                record.id,
            )
            continue
        by_downsample.setdefault(snap.downsample, []).append(
            (record.id, snap.tight, snap.window, snap.downsample)
        )
    groups: list[CropGroup] = []
    for downsample in sorted(by_downsample):
        for group in group_snaps(by_downsample[downsample]):
            groups.append(
                CropGroup(
                    snap=group.snap,
                    location_ids=sorted(group.location_ids),
                    downsample=downsample,
                    window=_window_covering_tiles(group.snap, tile_x_dim, tile_y_dim),
                )
            )
    return groups


def _window_covering_tiles(snap: TileRect, tile_x_dim: int, tile_y_dim: int) -> CropWindow:
    """Pixel window of a whole-tile span. Origin sits on a tile boundary."""
    return CropWindow(
        origin_x=snap.ix0 * tile_x_dim,
        origin_y=snap.iy0 * tile_y_dim,
        width=snap.n_x * tile_x_dim,
        height=snap.n_y * tile_y_dim,
    )


def _assign_window_indices(plans: list[PlannedCrop]) -> None:
    by_id: dict[int, list[PlannedCrop]] = {}
    for plan in plans:
        for location_id in plan.location_ids:
            by_id.setdefault(location_id, []).append(plan)
    for location_id, hosts in by_id.items():
        hosts.sort(key=lambda plan: (plan.window.origin_x, plan.window.origin_y, plan.image_key))
        count = len(hosts)
        for index, plan in enumerate(hosts):
            plan.window_of[location_id] = (index, count)
