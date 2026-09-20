"""Band-gated tile stitch into lossless PNG crops with optional read-only shared memory."""

from __future__ import annotations

import logging
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from numpy.typing import NDArray
from PIL import Image

import nornir_imageregistration
import nornir_pools
from nornir_buildmanager.operations.segmentationtraining.geometry import (
    TileRect,
    next_power_of_two,
)

from nornir_buildmanager.operations.segmentationtraining.poolutil import submit_bounded
from nornir_buildmanager.templates import Current as Templates

_logger = logging.getLogger(__name__)

LoadTile = Callable[[int, int], NDArray | None]

CROP_IMAGE_EXT = ".png"
LEGACY_CROP_IMAGE_EXT = ".jpg"


def crop_image_filename(image_key: str) -> str:
    """Trainer crop file name (lossless PNG)."""
    return f"{image_key}{CROP_IMAGE_EXT}"


def resolve_crop_image(images_dir: str | Path, image_key: str) -> Path:
    """Return the PNG crop path, or a leftover JPEG if PNG is not on disk yet."""
    root = Path(images_dir)
    png = root / crop_image_filename(image_key)
    if png.is_file():
        return png
    jpeg = root / f"{image_key}{LEGACY_CROP_IMAGE_EXT}"
    if jpeg.is_file():
        return jpeg
    return png


@dataclass(frozen=True)
class StitchJob:
    """One output image covering a snapped tile rect."""

    image_key: str
    snap: TileRect
    downsample: int
    tile_x_dim: int
    tile_y_dim: int
    image_path: str


def tile_filename(prefix: str, postfix: str, ix: int, iy: int) -> str:
    """Tileset file name using the buildmanager grid template."""
    return Templates.GridTileNameTemplate % {
        "prefix": prefix,
        "postfix": postfix,
        "X": ix,
        "Y": iy,
    }


def probe_tile_pixel_size(
    level_dir: str,
    *,
    postfix: str = ".png",
    samples: int = 32,
) -> tuple[int, int] | None:
    """Return (width, height) of tileset PNGs.

    Uses the most common sampled size, then snaps each axis to a power of two.
    Edge leftovers (e.g. 511) do not become TileXDim.
    """
    if not os.path.isdir(level_dir):
        return None
    widths: list[int] = []
    heights: list[int] = []
    with os.scandir(level_dir) as entries:
        for entry in entries:
            if not entry.is_file() or not entry.name.endswith(postfix):
                continue
            with Image.open(entry.path) as image:
                file_w, file_h = image.size
            widths.append(int(file_w))
            heights.append(int(file_h))
            if len(widths) >= samples:
                break
    if not widths:
        return None
    return next_power_of_two(_typical_size(widths)), next_power_of_two(_typical_size(heights))


def _typical_size(values: list[int]) -> int:
    """Most common value; ties prefer the larger size (full tiles over edge cuts)."""
    counts = Counter(values)
    best = max(counts.values())
    return max(size for size, count in counts.items() if count == best)


def stitch_from_loader(
    job: StitchJob,
    loader: LoadTile,
) -> NDArray:
    """Paste whole tiles into an 8-bit host array. No contrast remap.

    Pillow encode is a host boundary, so each loaded tile is converted once with
    :func:`nornir_imageregistration.EnsureNumpyArray`.
    """
    width, height = job.snap.pixel_size(job.tile_x_dim, job.tile_y_dim)
    canvas = np.zeros((height, width), dtype=np.uint8)
    for iy in range(job.snap.iy0, job.snap.iy1):
        for ix in range(job.snap.ix0, job.snap.ix1):
            try:
                tile = loader(ix, iy)
            except (IOError, OSError) as exc:
                _logger.warning(
                    "Skipping unreadable tile (%d,%d) in job %s: %s", ix, iy, job.image_key, exc
                )
                continue
            if tile is None:
                continue
            tile = nornir_imageregistration.EnsureNumpyArray(tile)
            if tile.ndim > 2:
                tile = np.asarray(tile[..., 0])
            if tile.dtype != np.uint8:
                if np.issubdtype(tile.dtype, np.floating):
                    tile = np.clip(tile * 255.0, 0, 255).astype(np.uint8)
                else:
                    info_max = np.iinfo(tile.dtype).max if np.issubdtype(tile.dtype, np.integer) else 255
                    tile = (tile.astype(np.float32) * (255.0 / max(int(info_max), 1))).astype(np.uint8)
            px = (ix - job.snap.ix0) * job.tile_x_dim
            py = (iy - job.snap.iy0) * job.tile_y_dim
            th, tw = tile.shape[:2]
            if th > job.tile_y_dim or tw > job.tile_x_dim:
                raise ValueError(
                    f"Tile ({ix},{iy}) is {tw}x{th} but TileXDim/TileYDim is "
                    f"{job.tile_x_dim}x{job.tile_y_dim}"
                )
            rh = min(th, max(0, height - py))
            rw = min(tw, max(0, width - px))
            if rh <= 0 or rw <= 0:
                continue
            canvas[py:py + rh, px:px + rw] = tile[:rh, :rw]
    return canvas


def write_png(array: NDArray, path: str) -> None:
    """Write 8-bit L PNG and drop a leftover JPEG of the same stem if present."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array, mode="L").save(path, format="PNG")
    leftover = Path(path).with_suffix(LEGACY_CROP_IMAGE_EXT)
    if leftover != Path(path) and leftover.is_file():
        leftover.unlink()


def disk_tile_loader(level_dir: str, prefix: str, postfix: str) -> LoadTile:
    """Load a tileset PNG from *level_dir*.

    Returns None for missing tiles and also for tiles that are present but
    unreadable (corrupt / truncated). A warning is emitted so the operator
    can repair or remove the bad file.
    """

    def load(ix: int, iy: int) -> NDArray | None:
        name = tile_filename(prefix, postfix, ix, iy)
        path = os.path.join(level_dir, name)
        if not os.path.isfile(path):
            return None
        try:
            return nornir_imageregistration.LoadImage(path, backend="numpy")
        except (IOError, OSError) as exc:
            _logger.warning("Skipping unreadable tile %s: %s", path, exc)
            return None

    return load


def run_stitch_jobs(
    jobs: list[StitchJob],
    *,
    loader: LoadTile,
    workers: int,
    use_shared_memory: bool = True,
) -> list[str]:
    """Stitch jobs. Band gating is the caller's responsibility.

    When *workers* > 1 and *use_shared_memory*, unique tiles in the job set are
    published once via :func:`npArrayToSharedArray` (read-only).
    """
    if not jobs:
        return []
    if workers <= 1 or not use_shared_memory:
        paths: list[str] = []
        for job in jobs:
            array = stitch_from_loader(job, loader)
            write_png(array, job.image_path)
            paths.append(job.image_path)
        return paths
    return _stitch_with_shared_tiles(jobs, loader=loader, workers=workers)


def _stitch_with_shared_tiles(
    jobs: list[StitchJob],
    *,
    loader: LoadTile,
    workers: int,
) -> list[str]:
    tiles: dict[tuple[int, int], Any] = {}
    handles: list[Any] = []
    try:
        needed: set[tuple[int, int]] = set()
        for job in jobs:
            for iy in range(job.snap.iy0, job.snap.iy1):
                for ix in range(job.snap.ix0, job.snap.ix1):
                    needed.add((ix, iy))
        for ix, iy in needed:
            try:
                array = loader(ix, iy)
            except (IOError, OSError) as exc:
                _logger.warning(
                    "Skipping unreadable tile (%d,%d) during shared prefetch: %s", ix, iy, exc
                )
                continue
            if array is None:
                continue
            # Shared-memory publish is host-only; LoadImage may return CuPy.
            array = nornir_imageregistration.EnsureNumpyArray(array)
            meta, _view = nornir_imageregistration.npArrayToSharedArray(
                np.ascontiguousarray(array), read_only=True
            )
            tiles[(ix, iy)] = meta
            handles.append(meta)
        pool = nornir_pools.GetMultithreadingPool("segtrain-stitch", num_threads=workers)
        results: list[str] = []

        def submit(job: StitchJob):
            return pool.add_task(
                f"stitch-{job.image_key}",
                stitch_job_from_shared,
                job,
                tiles,
            )

        for task in submit_bounded(submit, jobs, max_in_flight=workers):
            results.append(task.wait_return())
        return results
    finally:
        for meta in handles:
            nornir_imageregistration.unlink_shared_memory(meta)


def stitch_job_from_shared(job: StitchJob, tiles: dict[tuple[int, int], Any]) -> str:
    """Worker: attach read-only shared tiles and write PNG."""

    def loader(ix: int, iy: int) -> NDArray | None:
        meta = tiles.get((ix, iy))
        if meta is None:
            return None
        return nornir_imageregistration.ImageParamToNumpyImageArray(meta)

    array = stitch_from_loader(job, loader)
    write_png(array, job.image_path)
    return job.image_path


def jobs_in_column_band(jobs: list[StitchJob], ix_min: int, ix_max: int) -> list[StitchJob]:
    """Jobs whose tile X range intersects [ix_min, ix_max) inclusive-exclusive."""
    return [job for job in jobs if job.snap.ix0 < ix_max and job.snap.ix1 > ix_min]


def remaining_min_ix(jobs: list[StitchJob]) -> int | None:
    if not jobs:
        return None
    return min(job.snap.ix0 for job in jobs)


COLUMN_BAND = 2


def tiles_for_jobs(jobs: list[StitchJob]) -> set[tuple[int, int]]:
    """Unique tile indices referenced by *jobs*."""
    needed: set[tuple[int, int]] = set()
    for job in jobs:
        for iy in range(job.snap.iy0, job.snap.iy1):
            for ix in range(job.snap.ix0, job.snap.ix1):
                needed.add((ix, iy))
    return needed


def stage_tile_file(
    source_dir: str,
    stage_dir: str | None,
    *,
    prefix: str,
    postfix: str,
    ix: int,
    iy: int,
) -> str | None:
    """Copy one tileset file to *stage_dir* when staging is enabled. Return the path to load."""
    name = tile_filename(prefix, postfix, ix, iy)
    source = os.path.join(source_dir, name)
    if stage_dir:
        dest_dir = stage_dir
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, name)
        if os.path.isfile(source) and not os.path.isfile(dest):
            import shutil
            shutil.copy2(source, dest)
        if os.path.isfile(dest):
            return dest
    if os.path.isfile(source):
        return source
    return None


def sweep_stitch_jobs(
    jobs: list[StitchJob],
    *,
    source_dir: str,
    prefix: str,
    postfix: str,
    workers: int,
    stage_dir: str | None = None,
    use_shared_memory: bool = True,
    column_band: int = COLUMN_BAND,
) -> list[str]:
    """Stitch *jobs* one K-column band at a time with bounded in-flight workers.

    Prefetches the next column's tiles onto *stage_dir* while the current band
    encodes. Evicts finished columns using remaining min ix (rtree when present
    only for neighbor queries in grouping). Calls ``ReleaseStagePools`` after
    each band.
    """
    if not jobs:
        return []
    remaining = sorted(jobs, key=lambda job: (job.snap.ix0, job.snap.iy0, job.image_key))
    written: list[str] = []
    column_band = max(1, column_band)

    while remaining:
        ix_min = remaining[0].snap.ix0
        ix_max = ix_min + column_band
        band = jobs_in_column_band(remaining, ix_min, ix_max)
        prefetch = jobs_in_column_band(remaining, ix_min, ix_max + 1)
        for ix, iy in tiles_for_jobs(prefetch):
            stage_tile_file(
                source_dir,
                stage_dir,
                prefix=prefix,
                postfix=postfix,
                ix=ix,
                iy=iy,
            )
        load_root = stage_dir if stage_dir else source_dir
        
        loader = disk_tile_loader(load_root, prefix, postfix)
        written.extend(
            run_stitch_jobs(
                band,
                loader=loader,
                workers=workers,
                use_shared_memory=use_shared_memory,
            )
        )
        done = {job.image_key for job in band}
        remaining = [job for job in remaining if job.image_key not in done]
        nornir_pools.ReleaseStagePools()
    return written
