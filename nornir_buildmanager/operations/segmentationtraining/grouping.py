"""Group masks that independently selected the same training window."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from nornir_buildmanager.operations.segmentationtraining.geometry import (
    CropWindow,
    TileRect,
)

_VOLUME_TOKEN = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_volume_token(name: str | None) -> str:
    """Filename-safe volume prefix so mixed-volume training sets do not collide."""
    cleaned = _VOLUME_TOKEN.sub("_", (name or "").strip()).strip("._")
    return cleaned or "volume"


# Annotation servers here are named for their volume with deployment decoration around it, e.g.
# ``NeitzTemporalMonkeyTokenTest`` for TemporalMonkey. The decoration is not part of the volume's name.
_SERVER_NAME_PREFIXES = ("Neitz",)
_SERVER_NAME_SUFFIXES = ("TokenTest", "Test")


def volume_name_from_odata_url(odata_url: str | None) -> str | None:
    """Volume name taken from the annotation server's name, or None when the URL does not carry one.

    The server name is the path segment before ``OData`` (``…/NeitzCPED/OData`` -> ``CPED``), minus the
    deployment prefix and suffix. A name that is nothing but decoration yields None.
    """
    if not odata_url:
        return None
    segments = [part for part in urlparse(odata_url.strip()).path.split("/") if part]
    for index, part in enumerate(segments):
        if part.lower() == "odata" and index > 0:
            server = segments[index - 1]
            break
    else:
        return None
    name = server
    for prefix in _SERVER_NAME_PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            name = name[len(prefix):]
            break
    for suffix in _SERVER_NAME_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
            break
    cleaned = _VOLUME_TOKEN.sub("_", name.strip()).strip("._")
    return cleaned or None


@dataclass
class CropGroup:
    """One output image and the location ids that share it."""

    snap: TileRect
    location_ids: list[int] = field(default_factory=list)
    downsample: int = 1
    window: CropWindow | None = None

    def image_key_for(self, z: int, volume: str) -> str:
        """Stable shared-image id; *z* is the section number, not a location id.

        Pixel extents are used when a window is set, so a half-step crop does not
        collide with the tileset span that covers the same tiles.
        """
        token = sanitize_volume_token(volume)
        if self.window is None:
            return (
                f"{token}_{z}_D{self.downsample}_X{self.snap.ix0}-{self.snap.ix1}"
                f"_Y{self.snap.iy0}-{self.snap.iy1}"
            )
        end_x = self.window.origin_x + self.window.width
        end_y = self.window.origin_y + self.window.height
        return (
            f"{token}_{z}_D{self.downsample}_X{self.window.origin_x}-{end_x}"
            f"_Y{self.window.origin_y}-{end_y}"
        )


def group_windows(
    items: list[tuple[int, CropWindow]],
    *,
    downsample: int,
    tile_x_dim: int,
    tile_y_dim: int,
) -> list[CropGroup]:
    """Group location ids that selected the same window.

    A mask is not moved onto a neighbor window. Two masks share an image only
    when they independently choose the same origin and size.
    """
    assigned: dict[CropWindow, list[int]] = {}
    for location_id, window in items:
        assigned.setdefault(window, []).append(location_id)
    groups: list[CropGroup] = []
    order = sorted(assigned, key=lambda item: (item.origin_x, item.origin_y, item.width, item.height))
    for window in order:
        groups.append(
            CropGroup(
                snap=window.covering_tiles(tile_x_dim, tile_y_dim),
                location_ids=sorted(assigned[window]),
                downsample=downsample,
                window=window,
            )
        )
    return groups


def group_snaps(
    items: list[tuple[int, TileRect, TileRect, int]],
    *,
    max_tiles_x: int = 0,
    max_tiles_y: int = 0,
) -> list[CropGroup]:
    """Assign each cell to a same-D window that already covers its tight snap.

    Each item is ``(location_id, tight, window, downsample)``. *window* is that
    cell's MaxTexture crop from its min tile. A neighbor may reuse it when
    *tight* fits inside, so a cell that only needs tile (5,7) can share the
    2×2 that starts at (4,7). D is never coarsened to share. *max_tiles_x* /
    *max_tiles_y* are unused; kept for callers.
    """
    del max_tiles_x, max_tiles_y
    if not items:
        return []
    downsample = items[0][3]
    candidates = sorted(
        {item[2] for item in items},
        key=lambda rect: (rect.ix0, rect.iy0, rect.ix1, rect.iy1),
    )
    assigned: dict[TileRect, list[int]] = {}
    for location_id, tight, window, item_d in items:
        if item_d != downsample:
            raise ValueError("group_snaps requires a single downsample")
        # *candidates* is sorted by the host-preference key, so the first containing rect is the minimum.
        host = next((rect for rect in candidates if rect.contains(tight)), window)
        assigned.setdefault(host, []).append(location_id)
    return [
        CropGroup(snap=rect, location_ids=ids, downsample=downsample)
        for rect, ids in assigned.items()
    ]
