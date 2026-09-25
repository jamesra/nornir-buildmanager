"""One overlay per location that was split across crop tiles.

The PNG keeps the aspect ratio of those tiles: three 1024-pixel tiles stacked
vertically are 1024x3072, not a square. The JSON is one card whose
``gridColumns`` and ``gridRows`` say how many squares it occupies.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from nornir_buildmanager.operations.segmentationtraining.freshness import ImageWatermark

_PART_COLORS = (
    (255, 0, 0),
    (0, 255, 0),
    (0, 128, 255),
    (255, 255, 0),
    (255, 0, 255),
    (0, 255, 255),
)


def write_split_mask_overviews(
    output: Path,
    marks: list[ImageWatermark],
    location_ids: set[int],
    *,
    max_edge: int = 1024,
) -> int:
    """Write ``overlays/parts/{locationId}.png`` and ``.json`` for split masks.

    Locations in *location_ids* that occupy one crop have any previous part
    files removed. Returns the number of overviews written.
    """
    folder = output / "overlays" / "parts"
    hosts: dict[int, list[ImageWatermark]] = {}
    for mark in marks:
        if mark.width <= 0 or mark.height <= 0:
            continue
        for location_id in mark.member_ids:
            if location_id in location_ids:
                hosts.setdefault(location_id, []).append(mark)
    written = 0
    split_ids: set[int] = set()
    for location_id, group in hosts.items():
        unique = {mark.key: mark for mark in group}
        if len(unique) < 2:
            continue
        split_ids.add(location_id)
        _write_one(folder, location_id, list(unique.values()), max_edge=max_edge)
        written += 1
    if folder.is_dir():
        for location_id in location_ids - split_ids:
            for suffix in (".png", ".json"):
                (folder / f"{location_id}{suffix}").unlink(missing_ok=True)
    return written


def _write_one(
    folder: Path,
    location_id: int,
    marks: list[ImageWatermark],
    *,
    max_edge: int,
) -> None:
    ordered = sorted(marks, key=lambda mark: (mark.origin_y, mark.origin_x, mark.key))
    min_x = min(mark.origin_x for mark in ordered)
    min_y = min(mark.origin_y for mark in ordered)
    max_x = max(mark.origin_x + mark.width for mark in ordered)
    max_y = max(mark.origin_y + mark.height for mark in ordered)
    width = max(1, max_x - min_x)
    height = max(1, max_y - min_y)
    cell = _grid_cell(ordered, max_edge)
    grid_columns = max(1, round(width / cell))
    grid_rows = max(1, round(height / cell))
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    parts: list[dict[str, int | str]] = []
    for index, mark in enumerate(ordered):
        x = mark.origin_x - min_x
        y = mark.origin_y - min_y
        _paste_mask(canvas, folder.parents[1], mark, location_id, x, y, mark.width, mark.height, index)
        parts.append({
            "windowIndex": index,
            "windowCount": len(ordered),
            "imageKey": mark.key,
            "x": x,
            "y": y,
            "width": mark.width,
            "height": mark.height,
            "gridX": round(x / cell),
            "gridY": round(y / cell),
        })
    folder.mkdir(parents=True, exist_ok=True)
    canvas.save(folder / f"{location_id}.png")
    payload = {
        "locationId": location_id,
        "width": width,
        "height": height,
        "originX": min_x,
        "originY": min_y,
        "gridColumns": grid_columns,
        "gridRows": grid_rows,
        "parts": parts,
    }
    (folder / f"{location_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def _grid_cell(marks: list[ImageWatermark], max_edge: int) -> int:
    """Tile size that one grid square represents. Crops share one edge length."""
    edges = {mark.width for mark in marks} | {mark.height for mark in marks}
    edges.discard(0)
    if len(edges) == 1:
        return edges.pop()
    return max(1, int(max_edge))


def _paste_mask(
    canvas: Image.Image,
    output: Path,
    mark: ImageWatermark,
    location_id: int,
    x: int,
    y: int,
    width: int,
    height: int,
    index: int,
) -> None:
    mask_path = output / "masks" / f"{mark.key}_{location_id}.png"
    if not mask_path.is_file():
        mask_path = output / "ignored" / f"{mark.key}_{location_id}.png"
    if not mask_path.is_file():
        return
    with Image.open(mask_path) as handle:
        mask = handle.convert("L").resize((width, height), Image.Resampling.NEAREST)
    color = Image.new("RGB", (width, height), _PART_COLORS[index % len(_PART_COLORS)])
    canvas.paste(color, (x, y), mask)
