"""Band-gated tile stitch into lossless PNG crops with optional read-only shared memory."""

from __future__ import annotations

import logging
import os
import shutil
from collections import Counter
from collections.abc import Iterable
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


def finer_dirs_for_downsample(downsample: int, level_dirs: dict[int, str]) -> list[str]:
    """Return level directories for successive halves of *downsample*, next-finer first.

    Reconstruction assumes a 2× grid at each step, so the list stops at the first
    missing half rather than jumping a coarser stride.
    """
    dirs: list[str] = []
    current = int(downsample)
    while current > 1 and current % 2 == 0:
        current //= 2
        path = level_dirs.get(current)
        if path is None:
            break
        dirs.append(path)
    return dirs


def _as_uint8_gray(tile: NDArray) -> NDArray:
    """Return a 2-D uint8 host array for PNG paste and encode."""
    tile = nornir_imageregistration.EnsureNumpyArray(tile)
    if tile.ndim > 2:
        tile = np.asarray(tile[..., 0])
    if tile.dtype != np.uint8:
        if np.issubdtype(tile.dtype, np.floating):
            tile = np.clip(tile * 255.0, 0, 255).astype(np.uint8)
        else:
            info_max = np.iinfo(tile.dtype).max if np.issubdtype(tile.dtype, np.integer) else 255
            tile = (tile.astype(np.float32) * (255.0 / max(int(info_max), 1))).astype(np.uint8)
    return tile


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
            tile = _as_uint8_gray(tile)
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


def rebuild_tile_from_finer(
    ix: int,
    iy: int,
    finer_dirs: list[str],
    *,
    prefix: str,
    postfix: str,
    tile_x_dim: int,
    tile_y_dim: int,
) -> NDArray | None:
    """Recursively reconstruct a tile from progressively finer levels.

    Each of the four sub-tiles at ``finer_dirs[0]`` must load or recover;
    a single missing or unrecoverable sub-tile aborts the whole tile.
    """
    if not finer_dirs or tile_x_dim <= 0 or tile_y_dim <= 0:
        return None
    canvas = np.zeros((tile_y_dim * 2, tile_x_dim * 2), dtype=np.uint8)
    for sub_iy in range(iy * 2, iy * 2 + 2):
        for sub_ix in range(ix * 2, ix * 2 + 2):
            sub = _load_or_recover(
                sub_ix,
                sub_iy,
                level_dir=finer_dirs[0],
                finer_dirs=finer_dirs[1:],
                prefix=prefix,
                postfix=postfix,
                tile_x_dim=tile_x_dim,
                tile_y_dim=tile_y_dim,
            )
            if sub is None:
                return None
            px = (sub_ix - ix * 2) * tile_x_dim
            py = (sub_iy - iy * 2) * tile_y_dim
            th, tw = sub.shape[:2]
            rh = min(th, tile_y_dim)
            rw = min(tw, tile_x_dim)
            if rh <= 0 or rw <= 0:
                return None
            canvas[py:py + rh, px:px + rw] = sub[:rh, :rw]
    with Image.fromarray(canvas, mode="L") as im:
        result = im.resize((tile_x_dim, tile_y_dim), resample=Image.Resampling.BILINEAR)
    return np.array(result, dtype=np.uint8)


def _load_or_recover(
    ix: int,
    iy: int,
    *,
    level_dir: str,
    finer_dirs: list[str],
    prefix: str,
    postfix: str,
    tile_x_dim: int,
    tile_y_dim: int,
    repair_dir: str | None = None,
) -> NDArray | None:
    """Load one tile; on IOError try recursive reconstruction from *finer_dirs*."""
    name = tile_filename(prefix, postfix, ix, iy)
    path = os.path.join(level_dir, name)
    if not os.path.isfile(path):
        return None
    try:
        tile = nornir_imageregistration.LoadImage(path, backend="numpy")
        return _as_uint8_gray(tile)
    except (IOError, OSError) as exc:
        can_recover = bool(finer_dirs) and tile_x_dim > 0 and tile_y_dim > 0
        if can_recover:
            _logger.warning(
                "Tile %s unreadable (%s); attempting recursive recovery",
                path,
                exc,
            )
            recovered = rebuild_tile_from_finer(
                ix,
                iy,
                finer_dirs,
                prefix=prefix,
                postfix=postfix,
                tile_x_dim=tile_x_dim,
                tile_y_dim=tile_y_dim,
            )
            if recovered is not None:
                _logger.warning("Recovered tile %s; overwriting corrupt file", path)
                write_png(recovered, path)
                if repair_dir:
                    repair_path = os.path.join(repair_dir, name)
                    if os.path.normpath(repair_path) != os.path.normpath(path):
                        write_png(recovered, repair_path)
                return recovered
        _logger.warning("Skipping unreadable tile %s: %s", path, exc)
        return None


def disk_tile_loader(
    level_dir: str,
    prefix: str,
    postfix: str,
    *,
    finer_dirs: list[str] | None = None,
    tile_x_dim: int = 0,
    tile_y_dim: int = 0,
    repair_dir: str | None = None,
) -> LoadTile:
    """Load a tileset PNG from *level_dir*.

    Returns None for missing tiles and also for tiles that are present but
    unreadable (corrupt / truncated) after any finer-level recovery fails.
    A warning is emitted so the operator can repair or remove the bad file.
    """
    recovery_dirs = list(finer_dirs) if finer_dirs else []

    def load(ix: int, iy: int) -> NDArray | None:
        return _load_or_recover(
            ix,
            iy,
            level_dir=level_dir,
            finer_dirs=recovery_dirs,
            prefix=prefix,
            postfix=postfix,
            tile_x_dim=tile_x_dim,
            tile_y_dim=tile_y_dim,
            repair_dir=repair_dir,
        )

    return load


_STITCH_TILE_BUDGET_BYTES = 2 * 1024 ** 3
_IO_WORKERS_CAP = 32
_IO_WORKERS_FLOOR = 4


def _available_ram_bytes() -> int:
    """Host RAM available for sizing a decoded-tile window. Falls back to 2 GiB."""
    try:
        import psutil
    except ImportError:
        return _STITCH_TILE_BUDGET_BYTES
    return max(1, int(psutil.virtual_memory().available))


def default_io_workers(cpu_count: int | None = None) -> int:
    """Thread-pool width for PNG/CIFS tile reads. Sized from CPU count, capped at 32."""
    cpus = max(1, int(cpu_count if cpu_count is not None else (os.cpu_count() or 1)))
    return max(_IO_WORKERS_FLOOR, min(cpus * 2, _IO_WORKERS_CAP))


def default_column_band(
    workers: int,
    jobs: list[StitchJob],
    *,
    tile_x_dim: int = 0,
    tile_y_dim: int = 0,
    cpu_count: int | None = None,
    available_bytes: int | None = None,
) -> int:
    """Tile-column window from RAM and this section's crops.

    When unique tiles fit ``min(available/4, 2 GiB)``, the window is the full X
    span so the encode pool is not starved by a two-column slice. Otherwise the
    width is the largest span that stays inside that decoded-tile budget.
    CPU count sizes the I/O thread pool via :func:`default_io_workers`, not this
    window, except as the empty-job fallback.
    """
    cpus = max(1, int(cpu_count if cpu_count is not None else (os.cpu_count() or 1)))
    workers = max(1, int(workers) if workers else cpus)
    if not jobs:
        return max(workers, cpus)
    tx = tile_x_dim if tile_x_dim > 0 else 512
    ty = tile_y_dim if tile_y_dim > 0 else 512
    tile_bytes = max(1, int(tx) * int(ty))
    if available_bytes is None:
        available_bytes = _available_ram_bytes()
    budget = max(tile_bytes, min(int(available_bytes) // 4, _STITCH_TILE_BUDGET_BYTES))
    ix0 = min(job.snap.ix0 for job in jobs)
    ix1 = max(job.snap.ix1 for job in jobs)
    full_span = max(1, ix1 - ix0)
    unique = tiles_for_jobs(jobs)
    if len(unique) * tile_bytes <= budget:
        return full_span
    iy0 = min(job.snap.iy0 for job in jobs)
    iy1 = max(job.snap.iy1 for job in jobs)
    height = max(1, iy1 - iy0)
    columns_for_budget = max(1, budget // (tile_bytes * height))
    return max(1, min(full_span, columns_for_budget))


def _load_tile_coord(
    loader: LoadTile, ix: int, iy: int
) -> tuple[tuple[int, int], NDArray | None]:
    """Load one tile for the I/O thread pool. Missing or unreadable → None."""
    try:
        array = loader(ix, iy)
    except (IOError, OSError) as exc:
        _logger.warning(
            "Skipping unreadable tile (%d,%d) during shared prefetch: %s", ix, iy, exc
        )
        return (ix, iy), None
    return (ix, iy), array


def load_tiles_threaded(
    coords: Iterable[tuple[int, int]],
    loader: LoadTile,
    *,
    num_threads: int,
) -> dict[tuple[int, int], NDArray]:
    """Load unique tiles on a thread pool. PNG decode and CIFS release the GIL."""
    unique = list(dict.fromkeys(coords))
    if not unique:
        return {}
    if num_threads <= 1:
        loaded: dict[tuple[int, int], NDArray] = {}
        for ix, iy in unique:
            coord, array = _load_tile_coord(loader, ix, iy)
            if array is not None:
                loaded[coord] = array
        return loaded
    pool = nornir_pools.GetThreadPool("segtrain-tile-io", num_threads=num_threads)
    loaded = {}

    def submit(coord: tuple[int, int]):
        ix, iy = coord
        return pool.add_task(f"tile-{ix}-{iy}", _load_tile_coord, loader, ix, iy)

    for task in submit_bounded(submit, unique, max_in_flight=num_threads):
        coord, array = task.wait_return()
        if array is not None:
            loaded[coord] = array
    return loaded


def _publish_shared_tiles(
    arrays: dict[tuple[int, int], NDArray],
) -> tuple[dict[tuple[int, int], Any], list[Any]]:
    """Publish decoded host tiles into read-only shared memory (parent thread)."""
    tiles: dict[tuple[int, int], Any] = {}
    handles: list[Any] = []
    for coord, array in arrays.items():
        host = nornir_imageregistration.EnsureNumpyArray(array)
        meta, _view = nornir_imageregistration.npArrayToSharedArray(
            np.ascontiguousarray(host), read_only=True
        )
        tiles[coord] = meta
        handles.append(meta)
    return tiles, handles


def _unlink_handles(handles: list[Any]) -> None:
    for meta in handles:
        nornir_imageregistration.unlink_shared_memory(meta)


def prefetch_shared_tiles(
    jobs: list[StitchJob],
    loader: LoadTile,
    *,
    io_workers: int,
) -> tuple[dict[tuple[int, int], Any], list[Any]]:
    """Thread-load unique tiles for *jobs* and publish them as shared memory."""
    arrays = load_tiles_threaded(tiles_for_jobs(jobs), loader, num_threads=io_workers)
    return _publish_shared_tiles(arrays)


def stage_tiles_threaded(
    coords: Iterable[tuple[int, int]],
    source_dir: str,
    stage_dir: str | None,
    *,
    prefix: str,
    postfix: str,
    num_threads: int,
) -> None:
    """Copy unique tiles to *stage_dir* with a thread pool when staging is on."""
    if not stage_dir:
        return
    unique = list(dict.fromkeys(coords))
    if not unique:
        return
    if num_threads <= 1:
        for ix, iy in unique:
            stage_tile_file(
                source_dir, stage_dir, prefix=prefix, postfix=postfix, ix=ix, iy=iy
            )
        return
    pool = nornir_pools.GetThreadPool("segtrain-tile-io", num_threads=num_threads)

    def submit(coord: tuple[int, int]):
        ix, iy = coord
        return pool.add_task(
            f"stage-{ix}-{iy}",
            stage_tile_file,
            source_dir,
            stage_dir,
            prefix=prefix,
            postfix=postfix,
            ix=ix,
            iy=iy,
        )

    for task in submit_bounded(submit, unique, max_in_flight=num_threads):
        task.wait()


def run_stitch_jobs(
    jobs: list[StitchJob],
    *,
    loader: LoadTile,
    workers: int,
    use_shared_memory: bool = True,
    io_workers: int | None = None,
) -> list[str]:
    """Stitch jobs. Band gating is the caller's responsibility.

    When *workers* > 1 and *use_shared_memory*, unique tiles are loaded on a
    thread pool and published once via :func:`npArrayToSharedArray` (read-only).
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
    threads = default_io_workers() if io_workers is None else max(1, int(io_workers))
    return _stitch_with_shared_tiles(
        jobs, loader=loader, workers=workers, io_workers=threads
    )


def _submit_shared_stitch(
    jobs: list[StitchJob],
    tiles: dict[tuple[int, int], Any],
    workers: int,
) -> Any:
    """Return a submit_bounded iterator of in-flight encode tasks."""
    pool = nornir_pools.GetMultithreadingPool("segtrain-stitch", num_threads=workers)

    def submit(job: StitchJob):
        return pool.add_task(
            f"stitch-{job.image_key}",
            stitch_job_from_shared,
            job,
            tiles,
        )

    return submit_bounded(submit, jobs, max_in_flight=workers)


def _drain_shared_stitch(task_iter: Any, started: list[Any]) -> list[str]:
    """Wait for already-started encode tasks, then drain the rest of the iterator."""
    results: list[str] = []
    for task in started:
        results.append(task.wait_return())
    for task in task_iter:
        results.append(task.wait_return())
    return results


def _stitch_with_shared_tiles(
    jobs: list[StitchJob],
    *,
    loader: LoadTile,
    workers: int,
    io_workers: int,
) -> list[str]:
    """Thread-load unique tiles, publish shared memory, and encode in a process pool."""
    tiles, handles = prefetch_shared_tiles(jobs, loader, io_workers=io_workers)
    try:
        task_iter = _submit_shared_stitch(jobs, tiles, workers)
        started: list[Any] = []
        for _ in range(min(workers, len(jobs))):
            try:
                started.append(next(task_iter))
            except StopIteration:
                break
        return _drain_shared_stitch(task_iter, started)
    finally:
        _unlink_handles(handles)


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
    column_band: int | None = None,
    finer_dirs: list[str] | None = None,
    tile_x_dim: int = 0,
    tile_y_dim: int = 0,
    io_workers: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[str]:
    """Stitch *jobs* in RAM-sized column bands with overlapped tile I/O.

    Tile reads and optional staging copies run on a thread pool. PNG encode
    runs on a process pool. While the current band encodes, the next band's
    tiles are staged and decoded. Process pools stay warm across bands.
    """
    if not jobs:
        return []
    remaining = sorted(jobs, key=lambda job: (job.snap.ix0, job.snap.iy0, job.image_key))
    total_jobs = len(jobs)
    if on_progress is not None:
        on_progress(0, total_jobs)
    threads = default_io_workers() if io_workers is None else max(1, int(io_workers))
    if column_band is None:
        column_band = default_column_band(
            workers,
            remaining,
            tile_x_dim=tile_x_dim,
            tile_y_dim=tile_y_dim,
        )
    column_band = max(1, int(column_band))
    load_root = stage_dir if stage_dir else source_dir
    loader = disk_tile_loader(
        load_root,
        prefix,
        postfix,
        finer_dirs=finer_dirs,
        tile_x_dim=tile_x_dim,
        tile_y_dim=tile_y_dim,
        repair_dir=source_dir,
    )
    parallel = workers > 1 and use_shared_memory
    written: list[str] = []
    next_shared: tuple[list[StitchJob], dict[tuple[int, int], Any], list[Any]] | None = None

    def prepare_band(band_jobs: list[StitchJob]) -> tuple[dict[tuple[int, int], Any], list[Any]]:
        stage_tiles_threaded(
            tiles_for_jobs(band_jobs),
            source_dir,
            stage_dir,
            prefix=prefix,
            postfix=postfix,
            num_threads=threads,
        )
        if not parallel:
            return {}, []
        return prefetch_shared_tiles(band_jobs, loader, io_workers=threads)

    try:
        while remaining:
            ix_min = remaining[0].snap.ix0
            band = jobs_in_column_band(remaining, ix_min, ix_min + column_band)
            if not band:
                break
            if next_shared is not None:
                band, tiles, handles = next_shared
                next_shared = None
            else:
                tiles, handles = prepare_band(band)
            done = {job.image_key for job in band}
            upcoming = [job for job in remaining if job.image_key not in done]
            if not parallel:
                written.extend(
                    run_stitch_jobs(
                        band,
                        loader=loader,
                        workers=workers,
                        use_shared_memory=False,
                        io_workers=threads,
                    )
                )
                remaining = upcoming
                if on_progress is not None:
                    on_progress(total_jobs - len(remaining), total_jobs)
                continue
            task_iter = _submit_shared_stitch(band, tiles, workers)
            started: list[Any] = []
            try:
                for _ in range(min(workers, len(band))):
                    try:
                        started.append(next(task_iter))
                    except StopIteration:
                        break
                if upcoming:
                    next_ix = upcoming[0].snap.ix0
                    next_band = jobs_in_column_band(upcoming, next_ix, next_ix + column_band)
                    next_shared = (next_band, *prepare_band(next_band))
                written.extend(_drain_shared_stitch(task_iter, started))
            finally:
                _unlink_handles(handles)
            remaining = upcoming
            if on_progress is not None:
                on_progress(total_jobs - len(remaining), total_jobs)
    finally:
        if next_shared is not None:
            _unlink_handles(next_shared[2])
    return written
