"""Pad, tile snap, and half-step training windows at a fixed downsample."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from nornir_buildmanager.operations.segmentationtraining.curves import hydrate_polygons
from nornir_buildmanager.operations.segmentationtraining.wkt import (
    PolygonRings,
    parse_wkt_polygons,
)

# Image row 0 is mosaic MinY (tile iY0). Overlay vs Viking is a first real-data check.
MOSAIC_Y_INCREASES_WITH_IMAGE_ROW = True

_logger = logging.getLogger(__name__)
_logged_uneven_texture_span = False


@dataclass(frozen=True)
class TileRect:
    """Half-open tile-index rectangle, exclusive of ix1/iy1."""

    ix0: int
    ix1: int
    iy0: int
    iy1: int

    @property
    def n_x(self) -> int:
        return self.ix1 - self.ix0

    @property
    def n_y(self) -> int:
        return self.iy1 - self.iy0

    def image_key(self, z: int) -> str:
        """Stable name for a shared crop."""
        return f"{z}_X{self.ix0}-{self.ix1}_Y{self.iy0}-{self.iy1}"

    def mosaic_origin(self, tile_w: float, tile_h: float) -> tuple[float, float]:
        """Mosaic bottom-left of this tile rect (minX, minY)."""
        return self.ix0 * tile_w, self.iy0 * tile_h

    def pixel_size(self, tile_x_dim: int, tile_y_dim: int) -> tuple[int, int]:
        return self.n_x * tile_x_dim, self.n_y * tile_y_dim

    def contains(self, other: TileRect) -> bool:
        """True when *other* is fully inside this half-open rect."""
        return (
            other.ix0 >= self.ix0
            and other.ix1 <= self.ix1
            and other.iy0 >= self.iy0
            and other.iy1 <= self.iy1
        )


@dataclass(frozen=True)
class CropWindow:
    """Training crop in pixels at one downsample. Origin is on the half-crop lattice."""

    origin_x: int
    origin_y: int
    width: int
    height: int

    def mosaic_origin(self, downsample: float) -> tuple[float, float]:
        """Mosaic coordinate of the crop's top-left pixel."""
        scale = float(downsample)
        return self.origin_x * scale, self.origin_y * scale

    def covering_tiles(self, tile_x_dim: int, tile_y_dim: int) -> TileRect:
        """Tileset tiles that cover this crop, including a partial tile at a half step."""
        if tile_x_dim <= 0 or tile_y_dim <= 0:
            raise ValueError("tile dimensions must be positive")
        ix0 = math.floor(self.origin_x / tile_x_dim)
        iy0 = math.floor(self.origin_y / tile_y_dim)
        ix1 = math.ceil((self.origin_x + self.width) / tile_x_dim)
        iy1 = math.ceil((self.origin_y + self.height) / tile_y_dim)
        if ix1 <= ix0:
            ix1 = ix0 + 1
        if iy1 <= iy0:
            iy1 = iy0 + 1
        return TileRect(ix0, ix1, iy0, iy1)

    def crop_offset(self, tile_x_dim: int, tile_y_dim: int) -> tuple[int, int]:
        """Pixel offset of this crop inside :meth:`covering_tiles`."""
        tiles = self.covering_tiles(tile_x_dim, tile_y_dim)
        return (
            self.origin_x - tiles.ix0 * tile_x_dim,
            self.origin_y - tiles.iy0 * tile_y_dim,
        )


@dataclass(frozen=True)
class SnapPlan:
    """Finest D, the tight tile snap of the mask, and the MaxTexture output window."""

    downsample: int
    tight: TileRect
    window: TileRect


@dataclass(frozen=True)
class AnnotationGeometry:
    """One location after WKT parse, pad, and snap at a chosen D."""

    location_id: int
    z: int
    downsample: int
    mosaics: tuple[PolygonRings, ...]
    padded_min_x: float
    padded_min_y: float
    padded_max_x: float
    padded_max_y: float
    snap: TileRect


def mosaic_bbox(polygons: list[PolygonRings]) -> tuple[float, float, float, float]:
    """Axis-aligned mosaic bounds of all exterior rings."""
    xs: list[float] = []
    ys: list[float] = []
    for rings in polygons:
        for x, y in rings[0]:
            xs.append(x)
            ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def pad_bbox(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    pad: float,
    mosaic_bounds: tuple[float, float, float, float] | None = None,
) -> tuple[float, float, float, float]:
    """Expand a bbox by *pad* mosaic pixels on each side and optional mosaic clamp."""
    extra = float(pad)
    out = (min_x - extra, min_y - extra, max_x + extra, max_y + extra)
    if mosaic_bounds is None:
        return out
    return (
        max(out[0], mosaic_bounds[0]),
        max(out[1], mosaic_bounds[1]),
        min(out[2], mosaic_bounds[2]),
        min(out[3], mosaic_bounds[3]),
    )


def snap_to_tiles(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    *,
    tile_w: float,
    tile_h: float,
) -> TileRect:
    """Snap a padded mosaic AABB to whole tiles."""
    ix0 = int(math.floor(min_x / tile_w))
    iy0 = int(math.floor(min_y / tile_h))
    ix1 = int(math.ceil(max_x / tile_w))
    iy1 = int(math.ceil(max_y / tile_h))
    if ix1 <= ix0:
        ix1 = ix0 + 1
    if iy1 <= iy0:
        iy1 = iy0 + 1
    return TileRect(ix0, ix1, iy0, iy1)


def max_texture_tile_span(tile_dim: int, max_texture: int) -> int | None:
    """Tiles per axis so `span * tile_dim == max_texture`, or None if it does not divide."""
    if tile_dim <= 0 or max_texture <= 0:
        return None
    if max_texture % tile_dim != 0:
        return None
    span = max_texture // tile_dim
    if span < 1:
        return None
    return span


def expand_snap_from_min_tile(
    snap: TileRect,
    span_x: int,
    span_y: int,
) -> TileRect | None:
    """Grow *snap* to a ``span_x`` × ``span_y`` window from its min tile.

    Origin is the tileset column/row of the annotation, not a quantized block
    grid. None means the tight snap is larger than MaxTexture; coarsen D.
    """
    if span_x < 1 or span_y < 1:
        return None
    if snap.n_x > span_x or snap.n_y > span_y:
        return None
    return TileRect(snap.ix0, snap.ix0 + span_x, snap.iy0, snap.iy0 + span_y)


def downsample_and_align_snap(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    *,
    finest: int,
    available: list[int],
    max_texture: int,
    min_process_pixels: int = 16,
    tile_x_dim: int,
    tile_y_dim: int,
) -> SnapPlan | None:
    """Legacy coarsen-to-fit snap. ``plan_section_crops`` splits at a fixed D instead.

    The output window is always ``span`` × ``span`` tiles starting at the
    annotation's min tileset column/row (512-px tiles → 2×2). If the tight snap
    is larger than that window, coarsen D. Never split one annotation across crops.
    """
    downsample = choose_downsample(
        width_mosaic=max_x - min_x,
        height_mosaic=max_y - min_y,
        finest=finest,
        available=available,
        max_texture=max_texture,
        min_process_pixels=min_process_pixels,
    )
    span_x = max_texture_tile_span(tile_x_dim, max_texture)
    span_y = max_texture_tile_span(tile_y_dim, max_texture)
    if span_x is None or span_y is None:
        _log_uneven_texture_span(tile_x_dim, tile_y_dim, max_texture)
    seen: set[int] = set()
    current = downsample
    while current not in seen:
        seen.add(current)
        snap = snap_to_tiles(
            min_x,
            min_y,
            max_x,
            max_y,
            tile_w=float(tile_x_dim * current),
            tile_h=float(tile_y_dim * current),
        )
        if not snap_fits_max_texture(snap, tile_x_dim, tile_y_dim, max_texture):
            bumped = next_available_downsample(available, current)
            if bumped is None:
                return None
            current = bumped
            continue
        if span_x is None or span_y is None:
            return SnapPlan(current, snap, snap)
        grown = expand_snap_from_min_tile(snap, span_x, span_y)
        if grown is not None:
            return SnapPlan(current, snap, grown)
        bumped = next_available_downsample(available, current)
        if bumped is None:
            return None
        current = bumped
    return None


def _log_uneven_texture_span(tile_x_dim: int, tile_y_dim: int, max_texture: int) -> None:
    global _logged_uneven_texture_span
    if _logged_uneven_texture_span:
        return
    _logged_uneven_texture_span = True
    _logger.info(
        "ExportAnnotationCrops: MaxTexture %s is not an integer tile count "
        "(%sx%s); keeping tight snaps",
        max_texture,
        tile_x_dim,
        tile_y_dim,
    )


def available_downsamples(levels: list[float | int]) -> list[int]:
    """Sorted integer downsample factors present on a tileset."""
    values = sorted({int(level) for level in levels if int(level) >= 1})
    return values or [1]


def next_power_of_two(value: int) -> int:
    """Smallest power of two that is >= *value*, minimum 1."""
    if value <= 1:
        return 1
    return 1 << (int(value) - 1).bit_length()


def choose_downsample(
    *,
    width_mosaic: float,
    height_mosaic: float,
    finest: int,
    available: list[int],
    max_texture: int,
    min_process_pixels: int = 16,
) -> int:
    """Finest power-of-two D so both bbox axes fit in *max_texture*.

    *min_process_pixels* is unused; kept so callers need not change.
    """
    del min_process_pixels
    if max_texture <= 0:
        raise ValueError("max_texture must be positive")
    need = max(
        1,
        math.ceil(max(width_mosaic, 0.0) / max_texture),
        math.ceil(max(height_mosaic, 0.0) / max_texture),
    )
    requested = max(int(finest), next_power_of_two(need))
    return clamp_downsample_to_available(requested, available, finest)


def clamp_downsample_to_available(
    requested: int,
    available: list[int],
    finest: int,
) -> int:
    """Smallest available D at or coarser than *requested* and *finest*."""
    floor = max(int(finest), 1)
    candidates = [item for item in sorted(set(available)) if item >= floor]
    if not candidates:
        return max(available) if available else floor
    for item in candidates:
        if item >= requested:
            return item
    return candidates[-1]


def next_available_downsample(available: list[int], current: int) -> int | None:
    """Next coarser available D strictly greater than *current*, or None."""
    for item in sorted(set(available)):
        if item > current:
            return item
    return None


def snap_fits_max_texture(
    snap: TileRect,
    tile_x_dim: int,
    tile_y_dim: int,
    max_texture: int,
) -> bool:
    """True when the snapped whole-tile window is within *max_texture* on both axes."""
    width, height = snap.pixel_size(tile_x_dim, tile_y_dim)
    return width <= max_texture and height <= max_texture


def window_tile_rect(
    rect: TileRect,
    *,
    max_tiles_x: int,
    max_tiles_y: int,
    overlap_tiles: int = 1,
) -> list[TileRect]:
    """Split a snapped rect into overlapping windows that fit MaxTiles."""
    max_tiles_x = max(1, max_tiles_x)
    max_tiles_y = max(1, max_tiles_y)
    overlap_tiles = max(0, overlap_tiles)
    if rect.n_x <= max_tiles_x and rect.n_y <= max_tiles_y:
        return [rect]
    windows: list[TileRect] = []
    if rect.n_x > max_tiles_x and rect.n_y > max_tiles_y:
        step_x = max(1, max_tiles_x - overlap_tiles)
        step_y = max(1, max_tiles_y - overlap_tiles)
        x0 = rect.ix0
        while True:
            x1 = min(x0 + max_tiles_x, rect.ix1)
            y0 = rect.iy0
            while True:
                y1 = min(y0 + max_tiles_y, rect.iy1)
                windows.append(TileRect(x0, x1, y0, y1))
                if y1 >= rect.iy1:
                    break
                y0 += step_y
                if y0 >= rect.iy1:
                    break
            if x1 >= rect.ix1:
                break
            x0 += step_x
            if x0 >= rect.ix1:
                break
        return windows
    if rect.n_x >= rect.n_y:
        step = max(1, max_tiles_x - overlap_tiles)
        x0 = rect.ix0
        while True:
            x1 = min(x0 + max_tiles_x, rect.ix1)
            windows.append(TileRect(x0, x1, rect.iy0, rect.iy1))
            if x1 >= rect.ix1:
                break
            x0 += step
            if x0 >= rect.ix1:
                break
        return windows
    step = max(1, max_tiles_y - overlap_tiles)
    y0 = rect.iy0
    while True:
        y1 = min(y0 + max_tiles_y, rect.iy1)
        windows.append(TileRect(rect.ix0, rect.ix1, y0, y1))
        if y1 >= rect.iy1:
            break
        y0 += step
        if y0 >= rect.iy1:
            break
    return windows


def pixel_rings_in_crop(
    polygons: list[PolygonRings],
    *,
    origin_x: float,
    origin_y: float,
    downsample: float,
    width: int,
    height: int,
) -> list[PolygonRings]:
    """Translate mosaic rings into integer crop pixels; clip empty polygons out."""
    converted: list[PolygonRings] = []
    for rings in polygons:
        pixel_rings: list[tuple[tuple[int, int], ...]] = []
        for ring in rings:
            pts: list[tuple[int, int]] = []
            for mx, my in ring:
                px = int(round((mx - origin_x) / downsample))
                py = int(round((my - origin_y) / downsample))
                if not MOSAIC_Y_INCREASES_WITH_IMAGE_ROW:
                    py = height - 1 - py
                pts.append((px, py))
            pixel_rings.append(_simplify_pixel_ring(tuple(pts)))
        if _ring_touches_image(pixel_rings[0], width, height):
            clipped = [_clip_ring_to_image(ring, width, height) for ring in pixel_rings]
            if len(clipped[0]) >= 3:
                converted.append(tuple(clipped))
    return converted


def _simplify_pixel_ring(ring: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    """Drop consecutive duplicate pixels (one vertex per pixel)."""
    if not ring:
        return ring
    simplified: list[tuple[int, int]] = [ring[0]]
    for point in ring[1:]:
        if point != simplified[-1]:
            simplified.append(point)
    if len(simplified) > 1 and simplified[0] == simplified[-1]:
        simplified.pop()
    return tuple(simplified)


def _clip_ring_to_image(ring: tuple[tuple[int, int], ...], width: int, height: int) -> tuple[tuple[int, int], ...]:
    """Clip a pixel ring to the crop rectangle (polygon ∩ window)."""
    clipped = list(ring)
    edges = (
        (lambda p: p[0] >= 0, lambda a, b: _intersect_x(a, b, 0)),
        (lambda p: p[0] <= width, lambda a, b: _intersect_x(a, b, width)),
        (lambda p: p[1] >= 0, lambda a, b: _intersect_y(a, b, 0)),
        (lambda p: p[1] <= height, lambda a, b: _intersect_y(a, b, height)),
    )
    for inside, intersect in edges:
        if not clipped:
            break
        output: list[tuple[int, int]] = []
        prev = clipped[-1]
        for current in clipped:
            curr_in = inside(current)
            prev_in = inside(prev)
            if curr_in:
                if not prev_in:
                    output.append(intersect(prev, current))
                output.append(current)
            elif prev_in:
                output.append(intersect(prev, current))
            prev = current
        clipped = output
    return _simplify_pixel_ring(tuple(clipped))


def _intersect_x(a: tuple[int, int], b: tuple[int, int], x: int) -> tuple[int, int]:
    if a[0] == b[0]:
        return x, a[1]
    t = (x - a[0]) / (b[0] - a[0])
    return x, int(round(a[1] + t * (b[1] - a[1])))


def _intersect_y(a: tuple[int, int], b: tuple[int, int], y: int) -> tuple[int, int]:
    if a[1] == b[1]:
        return a[0], y
    t = (y - a[1]) / (b[1] - a[1])
    return int(round(a[0] + t * (b[0] - a[0]))), y


def _ring_touches_image(ring: tuple[tuple[int, int], ...], width: int, height: int) -> bool:
    if len(ring) < 3:
        return False
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return not (max(xs) < 0 or min(xs) >= width or max(ys) < 0 or min(ys) >= height)


def _ring_abs_area_centroid(
    ring: tuple[tuple[float, float], ...] | tuple[tuple[int, int], ...],
) -> tuple[float, float, float]:
    """Return absolute shoelace area and centroid. Empty rings return zeros."""
    if len(ring) < 3:
        return 0.0, 0.0, 0.0
    cross = 0.0
    cx = 0.0
    cy = 0.0
    count = len(ring)
    for index in range(count):
        x, y = ring[index]
        nx, ny = ring[(index + 1) % count]
        partial = x * ny - nx * y
        cross += partial
        cx += (x + nx) * partial
        cy += (y + ny) * partial
    signed = cross / 2.0
    if abs(signed) <= 1e-12:
        return 0.0, 0.0, 0.0
    return abs(signed), cx / (6.0 * signed), cy / (6.0 * signed)


def _polygons_area_centroid(polygons: list[PolygonRings]) -> tuple[float, float, float]:
    """Area-weighted centroid of exteriors minus holes, in the rings' coordinate space."""
    total_area = 0.0
    sum_x = 0.0
    sum_y = 0.0
    for rings in polygons:
        if not rings:
            continue
        area, cx, cy = _ring_abs_area_centroid(rings[0])
        total_area += area
        sum_x += cx * area
        sum_y += cy * area
        for hole in rings[1:]:
            hole_area, hx, hy = _ring_abs_area_centroid(hole)
            total_area -= hole_area
            sum_x -= hx * hole_area
            sum_y -= hy * hole_area
    return total_area, sum_x, sum_y


def _phase_offsets(crop: int) -> list[int]:
    """Non-overlapping partition offsets: 0 and half the crop when that is a real shift."""
    step = crop // 2
    if step <= 0 or step >= crop:
        return [0]
    return [0, step]


def _lattice_step(crop: int) -> int:
    """Half-crop step, or the crop itself when it is a single pixel."""
    step = crop // 2
    if step <= 0:
        return crop
    return step


def _containing_origins(edge_min: float, edge_max: float, crop: int, step: int) -> list[int]:
    """Lattice origins whose ``crop``-long window contains ``[edge_min, edge_max]``."""
    if step <= 0 or edge_max - edge_min > crop + 1e-9:
        return []
    k_max = math.floor(edge_min / step + 1e-9)
    k_min = math.ceil((edge_max - crop) / step - 1e-9)
    if k_min > k_max:
        return []
    return [k * step for k in range(k_min, k_max + 1)]


def _intersecting_origins(edge_min: float, edge_max: float, crop: int, phase: int) -> list[int]:
    """Origins ``phase + k * crop`` whose window meets ``[edge_min, edge_max]``."""
    if crop <= 0:
        return []
    k_min = math.floor((edge_min - crop - phase) / crop) - 1
    k_max = math.ceil((edge_max - phase) / crop) + 1
    origins: list[int] = []
    for k in range(k_min, k_max + 1):
        origin = phase + k * crop
        if origin < edge_max and origin + crop > edge_min:
            origins.append(origin)
    return origins


def _mask_centroid_px(
    polygons: list[PolygonRings],
    downsample: float,
    fallback: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Mask centroid in downsample pixels. Falls back to the padded bbox center."""
    area, sum_x, sum_y = _polygons_area_centroid(polygons)
    scale = float(downsample) if downsample else 1.0
    if area > 1e-6:
        return sum_x / area / scale, sum_y / area / scale
    return (fallback[0] + fallback[2]) / (2.0 * scale), (fallback[1] + fallback[3]) / (2.0 * scale)


def _window_mask_area(
    polygons: list[PolygonRings],
    window: CropWindow,
    downsample: int,
) -> float:
    """Clipped mask area inside *window*, using the same clip as training masks."""
    origin_x, origin_y = window.mosaic_origin(downsample)
    clipped = pixel_rings_in_crop(
        polygons,
        origin_x=origin_x,
        origin_y=origin_y,
        downsample=float(downsample),
        width=window.width,
        height=window.height,
    )
    total = 0.0
    for rings in clipped:
        if not rings:
            continue
        area, _cx, _cy = _ring_abs_area_centroid(rings[0])
        total += area
        for hole in rings[1:]:
            hole_area, _hx, _hy = _ring_abs_area_centroid(hole)
            total -= hole_area
    return max(0.0, total)


def place_mask_windows(
    polygons: list[PolygonRings],
    padded: tuple[float, float, float, float],
    *,
    downsample: int,
    crop_width: int,
    crop_height: int,
) -> list[CropWindow]:
    """Windows for one mask at a fixed downsample.

    When the padded bbox fits in a half-step window, return the single window
    whose center is closest to the mask centroid. Otherwise try the four
    non-overlapping phases and keep the phase whose smallest positive mask
    area is largest. Ties prefer fewer windows, then the smaller phase origin.
    """
    if crop_width < 1 or crop_height < 1:
        raise ValueError("crop size must be positive")
    scale = max(int(downsample), 1)
    min_x, min_y, max_x, max_y = padded
    px0 = min_x / scale
    py0 = min_y / scale
    px1 = max_x / scale
    py1 = max_y / scale
    step_x = _lattice_step(crop_width)
    step_y = _lattice_step(crop_height)
    contained = _contained_windows(px0, py0, px1, py1, crop_width, crop_height, step_x, step_y)
    if contained:
        centroid = _mask_centroid_px(polygons, scale, padded)
        return [_nearest_center_window(contained, centroid)]
    return _maximin_phase_windows(
        polygons,
        px0,
        py0,
        px1,
        py1,
        downsample=scale,
        crop_width=crop_width,
        crop_height=crop_height,
    )


def _contained_windows(
    px0: float,
    py0: float,
    px1: float,
    py1: float,
    crop_width: int,
    crop_height: int,
    step_x: int,
    step_y: int,
) -> list[CropWindow]:
    origins_x = _containing_origins(px0, px1, crop_width, step_x)
    origins_y = _containing_origins(py0, py1, crop_height, step_y)
    return [
        CropWindow(origin_x, origin_y, crop_width, crop_height)
        for origin_x in origins_x
        for origin_y in origins_y
    ]


def _nearest_center_window(
    windows: list[CropWindow],
    centroid: tuple[float, float],
) -> CropWindow:
    """Window whose center is closest to *centroid*. Ties break toward the smaller origin."""
    cx, cy = centroid

    def rank(window: CropWindow) -> tuple[float, int, int]:
        dx = cx - (window.origin_x + window.width / 2.0)
        dy = cy - (window.origin_y + window.height / 2.0)
        return (dx * dx + dy * dy, window.origin_x, window.origin_y)

    return min(windows, key=rank)


def _maximin_phase_windows(
    polygons: list[PolygonRings],
    px0: float,
    py0: float,
    px1: float,
    py1: float,
    *,
    downsample: int,
    crop_width: int,
    crop_height: int,
) -> list[CropWindow]:
    """Phase whose smallest positive clipped mask area is as large as possible."""
    best: tuple[tuple[float, int, int, int], list[CropWindow]] | None = None
    for phase_x in _phase_offsets(crop_width):
        origins_x = _intersecting_origins(px0, px1, crop_width, phase_x)
        for phase_y in _phase_offsets(crop_height):
            origins_y = _intersecting_origins(py0, py1, crop_height, phase_y)
            windows: list[CropWindow] = []
            areas: list[float] = []
            for origin_x in origins_x:
                for origin_y in origins_y:
                    window = CropWindow(origin_x, origin_y, crop_width, crop_height)
                    area = _window_mask_area(polygons, window, downsample)
                    if area <= 0.0:
                        continue
                    windows.append(window)
                    areas.append(area)
            if not windows:
                continue
            windows.sort(key=lambda item: (item.origin_x, item.origin_y))
            rank = (-min(areas), len(windows), phase_x, phase_y)
            if best is None or rank < best[0]:
                best = (rank, windows)
    if best is None:
        return []
    return best[1]


def parse_and_snap(
    wkt: str,
    *,
    pad: float,
    downsample: int,
    tile_x_dim: int,
    tile_y_dim: int,
    mosaic_bounds: tuple[float, float, float, float] | None = None,
    type_code: int | None = None,
) -> tuple[list[PolygonRings], TileRect, tuple[float, float, float, float]]:
    """Parse WKT, hydrate TypeCode 6 curves, pad, and snap at *downsample*."""
    polygons = hydrate_polygons(parse_wkt_polygons(wkt), type_code)
    min_x, min_y, max_x, max_y = mosaic_bbox(polygons)
    padded = pad_bbox(min_x, min_y, max_x, max_y, pad, mosaic_bounds)
    tile_w = tile_x_dim * downsample
    tile_h = tile_y_dim * downsample
    snap = snap_to_tiles(*padded, tile_w=float(tile_w), tile_h=float(tile_h))
    return polygons, snap, padded
