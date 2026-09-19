"""Collapse same-D snaps onto reusable MaxTexture windows without extra coarsening."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect

_VOLUME_TOKEN = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_volume_token(name: str | None) -> str:
    """Filename-safe volume prefix so mixed-volume training sets do not collide."""
    cleaned = _VOLUME_TOKEN.sub("_", (name or "").strip()).strip("._")
    return cleaned or "volume"


@dataclass
class CropGroup:
    """One output image and the location ids that share it."""

    snap: TileRect
    location_ids: list[int] = field(default_factory=list)
    downsample: int = 1

    def image_key_for(self, z: int, volume: str) -> str:
        """Stable shared-image id; *z* is the section number, not a location id."""
        token = sanitize_volume_token(volume)
        return (
            f"{token}_{z}_D{self.downsample}_X{self.snap.ix0}-{self.snap.ix1}"
            f"_Y{self.snap.iy0}-{self.snap.iy1}"
        )


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
        hosts = [rect for rect in candidates if rect.contains(tight)]
        host = min(hosts, key=lambda rect: (rect.ix0, rect.iy0, rect.ix1, rect.iy1)) if hosts else window
        assigned.setdefault(host, []).append(location_id)
    return [
        CropGroup(snap=rect, location_ids=ids, downsample=downsample)
        for rect, ids in assigned.items()
    ]


def agglomerative_merge(
    groups: list[CropGroup],
    *,
    max_tiles_x: int,
    max_tiles_y: int,
) -> list[CropGroup]:
    """Merge groups while union AABB stays within MaxTiles; least area increase."""
    remaining = [CropGroup(snap=g.snap, location_ids=list(g.location_ids), downsample=g.downsample)
                 for g in groups]
    while True:
        best: tuple[float, int, int, TileRect] | None = None
        for i, j in _candidate_pairs(remaining, max_tiles_x, max_tiles_y):
            left = remaining[i]
            right = remaining[j]
            union = _union(left.snap, right.snap)
            if union.n_x > max_tiles_x or union.n_y > max_tiles_y:
                continue
            increase = _area(union) - max(_area(left.snap), _area(right.snap))
            if best is None or increase < best[0]:
                best = (increase, i, j, union)
        if best is None:
            break
        _increase, i, j, union = best
        merged_ids = remaining[i].location_ids + remaining[j].location_ids
        downsample = remaining[i].downsample
        for index in sorted((i, j), reverse=True):
            remaining.pop(index)
        remaining.append(CropGroup(snap=union, location_ids=merged_ids, downsample=downsample))
    return remaining


def _candidate_pairs(
    remaining: list[CropGroup],
    max_tiles_x: int,
    max_tiles_y: int,
) -> list[tuple[int, int]]:
    """Neighbor pairs whose union might fit MaxTiles. Uses rtree when installed."""
    try:
        from rtree import index as rtree_index
    except ImportError:
        pairs: list[tuple[int, int]] = []
        for i, left in enumerate(remaining):
            for j in range(i + 1, len(remaining)):
                if _maybe_neighbors(left.snap, remaining[j].snap, max_tiles_x, max_tiles_y):
                    pairs.append((i, j))
        return pairs
    idx = rtree_index.Index()
    for i, group in enumerate(remaining):
        snap = group.snap
        idx.insert(i, (snap.ix0, snap.iy0, snap.ix1, snap.iy1))
    pairs = []
    seen: set[tuple[int, int]] = set()
    for i, group in enumerate(remaining):
        snap = group.snap
        bounds = (
            snap.ix0 - max_tiles_x,
            snap.iy0 - max_tiles_y,
            snap.ix1 + max_tiles_x,
            snap.iy1 + max_tiles_y,
        )
        for j in idx.intersection(bounds):
            if j <= i:
                continue
            pair = (i, int(j))
            if pair in seen:
                continue
            seen.add(pair)
            pairs.append(pair)
    return pairs


def _maybe_neighbors(a: TileRect, b: TileRect, max_tiles_x: int, max_tiles_y: int) -> bool:
    """Cheap reject: union cannot fit if centers are farther than MaxTiles."""
    if a.ix0 > b.ix1 + max_tiles_x or b.ix0 > a.ix1 + max_tiles_x:
        return False
    if a.iy0 > b.iy1 + max_tiles_y or b.iy0 > a.iy1 + max_tiles_y:
        return False
    return True


def _union(a: TileRect, b: TileRect) -> TileRect:
    return TileRect(
        min(a.ix0, b.ix0),
        max(a.ix1, b.ix1),
        min(a.iy0, b.iy0),
        max(a.iy1, b.iy1),
    )


def _area(rect: TileRect) -> int:
    return rect.n_x * rect.n_y
