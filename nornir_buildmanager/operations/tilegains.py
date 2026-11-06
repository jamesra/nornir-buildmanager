"""
TileGains: per-tile brightness gains for a channel's filter from the overlaps of a mosaic (beam drift, RC2's white
stripe). Design: ``.cursor/plans/crush_optimized_pipeline.plan.md`` (brightness-drift); the solve is
``nornir_imageregistration.tile_gains``.

Runs against any ``.mosaic`` (``Stage`` after import, ``Translated_*`` / ``Grid_*`` after alignment). Writes:

- ``TileGains_<transform>.json`` in the filter folder: gains, provenance and solve diagnostics;
- for crushed filters, each tile's ``nornir_crush`` chunk at every pyramid level (``gain``, ``effective_step``,
  ``effective_offset``), rewritten without decoding pixels and only when some gain moved by more than the tolerance;
- ``TileGainsSource`` (transform name and checksum) and ``TileGainsChecksum`` on the filter node.

Legacy (uncrushed) filters get the JSON and attributes only; their tiles are not changed.
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import nornir_imageregistration
import numpy as np
from nornir_imageregistration import noise_crush, tile_gains
from nornir_shared import prettyoutput
from PIL import Image

#: A rerun rewrites tile chunks only when some gain changed by more than this fraction (plan: ~0.5%).
GAIN_REWRITE_TOLERANCE = 0.005
#: Concurrent tile reads and chunk rewrites over the NAS.
_GAIN_IO_WORKERS = 16
#: Legacy 8-bit tiles: values at or beyond these are clipped (black background, saturation), not measurements.
_LEGACY_CLIP_LOW, _LEGACY_CLIP_HIGH = 2, 253


def _existing_names(folder: str) -> set[str]:
    """File names in ``folder`` from one directory listing (one round trip, not one stat per tile over the NAS)."""
    try:
        with os.scandir(folder) as entries:
            return {entry.name for entry in entries}
    except OSError:
        return set()


def _placements(mosaic: nornir_imageregistration.Mosaic, tile_height: float,
                tile_width: float) -> dict[int, tile_gains.TilePlacement]:
    """Tile number to its mosaic-space box: the transform's bounding box when it has one, else origin plus tile size."""
    out = {}
    for name, transform in mosaic.ImageToTransform.items():
        number = int(os.path.splitext(name)[0])
        try:
            box = transform.FixedBoundingBox
        except (AttributeError, NotImplementedError, ValueError):
            box = None
        if box is not None and box.Height > 0 and box.Width > 0:
            out[number] = tile_gains.TilePlacement(float(box.MinY), float(box.MinX), float(box.Height), float(box.Width))
            continue
        origin = transform.Transform(np.array([[0.0, 0.0]]))[0]
        origin = np.asarray(origin.get() if hasattr(origin, 'get') else origin, dtype=np.float64)
        out[number] = tile_gains.TilePlacement(float(origin[0]), float(origin[1]), tile_height, tile_width)
    return out


def _counts_reader(level_path: str, names: dict[int, str], crush: noise_crush.CrushParams | None,
                   cutoffs: tuple[float, float] | None):
    """Reader returning a tile in counts (clipped pixels 0). Crushed: inflate codes; legacy: undo the contrast stretch."""

    def read(tile: int) -> np.ndarray:
        with Image.open(os.path.join(level_path, names[tile])) as im:
            values = np.asarray(im)
        if crush is not None:
            counts = noise_crush.uncrush(values, crush, dtype=np.float32)
            counts[(values == 0) | (values >= crush.max_code)] = 0.0
            return counts
        values = values.astype(np.float32)
        lo, hi = cutoffs if cutoffs is not None else (0.0, 255.0)
        counts = lo + values * ((hi - lo) / 255.0)
        counts[(values <= _LEGACY_CLIP_LOW) | (values >= _LEGACY_CLIP_HIGH)] = 0.0
        return counts

    return read


def _previous_gains(path: str) -> dict[int, float] | None:
    try:
        with open(path, encoding='utf-8') as f:
            return {int(k): float(v) for k, v in json.load(f)['gains'].items()}
    except (OSError, ValueError, KeyError):
        return None


def _as_bool(value) -> bool:
    """Pipeline XML passes literal attribute values as strings."""
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes')
    return bool(value)


def TileGains(TransformNode, FilterNode, Downsample: int = 4, Logger: logging.Logger | None = None,
              CrushedOnly: bool | str = False, SkipIfCurrent: bool | str = False, **kwargs):
    """
    Compute per-tile gains for ``FilterNode`` from the overlaps of ``TransformNode``'s mosaic and record them.

    ``Downsample`` picks the pyramid level read (gains at downsample 8 matched level 1 on RC3; RC2 at 4: ~5 s per
    813-tile section). ``CrushedOnly`` skips legacy (uncrushed) filters; ``SkipIfCurrent`` skips a filter whose
    gains were already computed from this transform at its current checksum. ImportIDoc and ImportMRC call it with
    both, on ``Stage`` at level 1 (the only level an import writes; its tiles are still in the OS cache). Returns the
    filter node when its metadata changed, so the pipeline saves it.
    """
    if Logger is None:
        Logger = logging.getLogger(__name__)
    Downsample = int(Downsample)
    if TransformNode is None or FilterNode is None:
        return None
    if _as_bool(CrushedOnly) and noise_crush.CrushParams.from_attributes(FilterNode.attrib) is None:
        return None
    source = f"{TransformNode.Name}:{TransformNode.attrib.get('Checksum', '')}"
    if _as_bool(SkipIfCurrent) and FilterNode.attrib.get('TileGainsSource') == source and \
            os.path.exists(os.path.join(FilterNode.FullPath, f"TileGains_{TransformNode.Name}.json")):
        return None
    if not FilterNode.HasTilePyramid:
        Logger.warning(f"TileGains: {FilterNode.FullPath} has no tile pyramid")
        return None

    pyramid = FilterNode.TilePyramid
    level = pyramid.GetLevel(Downsample)
    if level is None:
        Logger.warning(f"TileGains: {pyramid.FullPath} has no level {Downsample}")
        return None
    level_path = level.FullPath

    crush = noise_crush.CrushParams.from_attributes(FilterNode.attrib)
    cutoffs = None
    if crush is None and 'MinIntensityCutoff' in FilterNode.attrib and 'MaxIntensityCutoff' in FilterNode.attrib:
        cutoffs = (float(FilterNode.attrib['MinIntensityCutoff']), float(FilterNode.attrib['MaxIntensityCutoff']))

    mosaic = nornir_imageregistration.Mosaic.LoadFromMosaicFile(TransformNode.FullPath)
    names = {int(os.path.splitext(n)[0]): n for n in mosaic.ImageToTransform}
    present = _existing_names(level_path)
    names = {t: n for t, n in names.items() if n in present}
    if not names:
        Logger.warning(f"TileGains: no tiles of {TransformNode.FullPath} found in {level_path}")
        return None
    with Image.open(os.path.join(level_path, names[min(names)])) as im:
        tile_width, tile_height = im.size[0] * Downsample, im.size[1] * Downsample
    placements = {t: p for t, p in _placements(mosaic, tile_height, tile_width).items() if t in names}

    start = time.perf_counter()
    # Full-resolution tiles are 16x the pixels of level 4: fewer readers in flight keep memory near 1 GB.
    workers = _GAIN_IO_WORKERS if Downsample >= 2 else _GAIN_IO_WORKERS // 2
    result = tile_gains.compute_tile_gains(placements, _counts_reader(level_path, names, crush, cutoffs),
                                           Downsample, low=0.5, high=float('inf'), max_workers=workers)
    elapsed = time.perf_counter() - start

    gains_path = os.path.join(FilterNode.FullPath, f"TileGains_{TransformNode.Name}.json")
    previous = _previous_gains(gains_path)
    changed = previous is None or set(previous) != set(result.gains) or any(
        abs(result.gains[t] / previous[t] - 1.0) > GAIN_REWRITE_TOLERANCE for t in result.gains)
    values = np.array(list(result.gains.values()))
    summary = {
        'transform': TransformNode.Name, 'transform_checksum': TransformNode.attrib.get('Checksum', ''),
        'filter': FilterNode.Name, 'downsample': Downsample, 'crushed': crush is not None,
        'pairs_used': result.pairs_used, 'pairs_rejected': result.pairs_rejected,
        'pairs_skipped': result.pairs_skipped, 'residual_mad': result.residual_mad,
        'shading': result.shading, 'tiles_without_pairs': result.tiles_without_pairs,
        'gain_p5_p50_p95': [float(np.percentile(values, q)) for q in (5, 50, 95)], 'seconds': round(elapsed, 1),
        'checksum': result.checksum(), 'gains': {str(t): g for t, g in sorted(result.gains.items())}}
    message = (f"TileGains {FilterNode.FullPath} on {TransformNode.Name}: {len(values)} tiles, "
               f"{result.pairs_used} overlaps ({result.pairs_rejected} rejected, {result.pairs_skipped} without tissue), "
               f"gain p5-p95 {summary['gain_p5_p50_p95'][0]:.3f}-{summary['gain_p5_p50_p95'][2]:.3f}, {elapsed:.1f} s")
    Logger.info(message)
    prettyoutput.Log(message)

    if not changed:
        if FilterNode.attrib.get('TileGainsSource') == source:
            return None
        # Same gains from a transform with a new checksum: record the new source so SkipIfCurrent sees it, but leave
        # the JSON, the chunks and the gain checksum as they are.
        FilterNode.TileGainsSource = source
        return FilterNode

    with open(gains_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=1)

    if crush is not None:
        jobs = []
        for pyramid_level in pyramid.Levels:
            present = _existing_names(pyramid_level.FullPath)
            for tile, name in names.items():
                if name in present:
                    jobs.append((os.path.join(pyramid_level.FullPath, name), result.gains[tile]))
        with ThreadPoolExecutor(max_workers=_GAIN_IO_WORKERS) as executor:
            stamped = sum(executor.map(lambda job: tile_gains.stamp_gain_png(*job), jobs))
        Logger.info(f"TileGains: wrote gains into {stamped} of {len(jobs)} tile chunks")

    # Attribute assignment (not .attrib[...]) marks the node changed, so the pipeline's save writes it.
    FilterNode.TileGainsSource = source
    FilterNode.TileGainsChecksum = result.checksum()
    return FilterNode
