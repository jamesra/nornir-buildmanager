"""Rebuild damaged raw tiles from a legacy 8-bit import of the same section.

RC3's raw TIFs were truncated to 0 bytes after its 2022 legacy build (66 files, 45 of them with a
2022 Raw8 tile; RC3 session, 2026-11-16). The crushed importer skips an unreadable tile, leaving a
hole the 2022 build does not have. James (2026-11-16): use the 2022 tile when it can be mapped back to
the quantized tiles, even roughly.

The legacy tile is a contrast stretch of the raw counts (section min, max and gamma, clipped to
8 bits). Rather than trusting stored contrast metadata, the mapping is learned from the section's own
tiles that exist in both forms:

- **Orientation**: each training pair is compared in the four flips; the one with the highest
  correlation is used, and every pair must agree on it with correlation >= ``MIN_CORRELATION``.
- **Numbering**: the legacy tile numbers the idoc position the importer used. A pair that does not
  correlate means the numbering differs (a 2022 import with tiles missing at the time, #153), so the
  section is refused rather than guessed. The idoc neighbours of each damaged tile are always among the
  training pairs, so the numbering is checked on both sides of it.
- **Levels**: ``lut[v]`` is the median raw count of the pixels whose legacy value is ``v``, pooled
  over the training pairs, filled across empty levels and made non-decreasing.

The rebuilt tile is ``lut[legacy]`` in raw orientation and dtype, written in place of the damaged file
so the normal import (gain-first, crushed) treats it like any other tile. It carries only the 256
levels of the legacy tile and the 2022 clipping. Next to each rebuilt tile a
``<name>.recovered.json`` records its source and the fit.

Run it on the import copy of a section, never on RawData itself. Only 0-byte, missing or unreadable
tiles are written; a readable tile is never touched. Dry run unless ``--apply``.

Usage:
  python -m nornir_buildmanager.importers.legacy_tile_recovery <section.idoc> <legacy TilePyramid/001 dir>
  python -m nornir_buildmanager.importers.legacy_tile_recovery <section.idoc> <legacy dir> --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
import PIL.Image
from nornir_imageregistration import noise_crush
from numpy.typing import NDArray

import nornir_buildmanager.templates
from nornir_buildmanager.importers.idoc import IDoc

#: Every training pair must correlate at least this well with its raw tile in the chosen orientation.
MIN_CORRELATION = 0.9
#: Training pairs per section, spread over the idoc, besides each damaged tile's idoc neighbours.
TRAINING_TILES = 8
#: Pixel stride used to fit the levels and to test the orientation (a 4096 tile gives 1M fit pixels).
FIT_STRIDE = 4

_ORIENTATIONS: dict[str, Callable[[NDArray], NDArray]] = {
    'identity': lambda a: a,
    'flipud': lambda a: a[::-1, :],
    'fliplr': lambda a: a[:, ::-1],
    'rot180': lambda a: a[::-1, ::-1],
}

logger = logging.getLogger(__name__)


@dataclass
class SectionTile:
    """One idoc tile: its raw file and the legacy tile that numbers the same idoc position."""
    index: int
    raw_path: str
    legacy_path: str


@dataclass
class LevelFit:
    """The legacy -> raw mapping learned for one section."""
    orientation: str
    lut: NDArray
    correlations: dict[int, float]
    rms_error: float
    raw_dtype: str
    training_indices: list[int] = field(default_factory=list)


class RecoveryRefused(Exception):
    """The section's legacy tiles cannot be mapped back with confidence; nothing is written."""


def raw_tile_is_damaged(path: str) -> bool:
    """True when the raw tile is missing, empty, or not a readable image."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return True
    try:
        with PIL.Image.open(path):
            pass
    except PIL.UnidentifiedImageError:
        return True
    return False


def section_tiles(idoc_path: str, legacy_dir: str) -> list[SectionTile]:
    """The idoc's tiles with their raw path and the legacy tile named by the importer for that position."""
    idoc = IDoc.Load(idoc_path, usecache=False)
    raw_dir = os.path.dirname(idoc_path)
    tiles = []
    for fallback, tile in enumerate(idoc.tiles):
        index = tile.TileIndex if tile.TileIndex is not None else fallback
        legacy_name = (nornir_buildmanager.templates.Current.TileCoordFormat % index) + '.png'
        tiles.append(SectionTile(index, os.path.join(raw_dir, tile.Image), os.path.join(legacy_dir, legacy_name)))
    return tiles


def _load_legacy(path: str) -> NDArray:
    with PIL.Image.open(path) as im:
        image = np.asarray(im)
    if image.ndim != 2 or image.dtype != np.uint8:
        raise RecoveryRefused(f"Legacy tile {path} is {image.dtype} {image.shape}, expected 8-bit greyscale")
    return image


def _correlation(a: NDArray, b: NDArray) -> float:
    a = a.astype(np.float64).ravel()
    b = b.astype(np.float64).ravel()
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _training_tiles(tiles: Sequence[SectionTile], damaged: set[int]) -> list[SectionTile]:
    """Readable tiles with a legacy twin: each damaged tile's nearest such neighbours, plus an even spread."""
    usable = [t for t in tiles if t.index not in damaged and os.path.exists(t.legacy_path)]
    if not usable:
        return []
    chosen: dict[int, SectionTile] = {}
    for d in sorted(damaged):
        below = [t for t in usable if t.index < d]
        above = [t for t in usable if t.index > d]
        if below:
            chosen[below[-1].index] = below[-1]
        if above:
            chosen[above[0].index] = above[0]
    for i in noise_crush.sample_indices(len(usable), TRAINING_TILES):
        chosen[usable[i].index] = usable[i]
    return [chosen[k] for k in sorted(chosen)]


def _fill_lut(medians: NDArray, counts: NDArray) -> NDArray:
    """Interpolate levels no training pixel had, then make the table non-decreasing."""
    levels = np.arange(256)
    seen = counts > 0
    lut = np.interp(levels, levels[seen], medians[seen])
    return np.maximum.accumulate(lut)


def fit_levels(training: Sequence[SectionTile]) -> LevelFit:
    """Learn the orientation and the legacy-level -> raw-count table from tiles present in both forms.

    :raises RecoveryRefused: no training pairs, mismatched shapes, or a pair that does not correlate.
    """
    if not training:
        raise RecoveryRefused("No readable raw tile in this section has a legacy tile to learn from")

    pairs: list[tuple[int, NDArray, NDArray]] = []
    raw_dtype = ''
    for t in training:
        raw = noise_crush.load_raw_tile(t.raw_path)
        legacy = _load_legacy(t.legacy_path)
        if raw.shape != legacy.shape:
            raise RecoveryRefused(f"Tile {t.index}: raw {raw.shape} and legacy {legacy.shape} differ in size")
        raw_dtype = raw.dtype.name
        pairs.append((t.index, raw[::FIT_STRIDE, ::FIT_STRIDE], legacy))

    scores = {name: [_correlation(raw, orient(legacy)[::FIT_STRIDE, ::FIT_STRIDE]) for _, raw, legacy in pairs]
              for name, orient in _ORIENTATIONS.items()}
    orientation = max(scores, key=lambda name: float(np.median(scores[name])))
    correlations = {index: score for (index, _, _), score in zip(pairs, scores[orientation])}
    poor = {i: round(c, 3) for i, c in correlations.items() if not c >= MIN_CORRELATION}
    if poor:
        raise RecoveryRefused(f"Legacy tiles do not match their raw tiles in orientation {orientation} "
                              f"(correlation below {MIN_CORRELATION}: {poor}); the 2022 numbering may differ")

    legacy_values = np.concatenate([_ORIENTATIONS[orientation](legacy)[::FIT_STRIDE, ::FIT_STRIDE].ravel()
                                    for _, _, legacy in pairs])
    raw_values = np.concatenate([raw.ravel() for _, raw, _ in pairs]).astype(np.float64)
    order = np.argsort(legacy_values, kind='stable')
    legacy_sorted = legacy_values[order]
    raw_sorted = raw_values[order]
    counts = np.bincount(legacy_sorted, minlength=256)
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    medians = np.array([np.median(raw_sorted[s:s + n]) if n else 0.0 for s, n in zip(starts, counts)])
    lut = _fill_lut(medians, counts)

    rms = float(np.sqrt(np.mean((lut[legacy_values] - raw_values) ** 2)))
    return LevelFit(orientation, lut, correlations, rms, raw_dtype, [t.index for t in training])


def rebuild_tile(legacy_path: str, fit: LevelFit) -> NDArray:
    """The raw tile rebuilt from its legacy tile: raw orientation and dtype, counts from the level table."""
    legacy = _ORIENTATIONS[fit.orientation](_load_legacy(legacy_path))
    info = np.iinfo(np.dtype(fit.raw_dtype))
    counts = np.clip(np.rint(fit.lut[legacy]), info.min, info.max)
    return np.ascontiguousarray(counts.astype(fit.raw_dtype))


def recover_section(idoc_path: str, legacy_dir: str, apply: bool) -> list[str]:
    """Rebuild the section's damaged raw tiles that have a legacy twin; return the paths (to be) written.

    :raises RecoveryRefused: the mapping cannot be learned with confidence.
    """
    tiles = section_tiles(idoc_path, legacy_dir)
    damaged = {t.index for t in tiles if raw_tile_is_damaged(t.raw_path)}
    targets = [t for t in tiles if t.index in damaged and os.path.exists(t.legacy_path)]
    for t in tiles:
        if t.index in damaged and not os.path.exists(t.legacy_path):
            logger.warning("Tile %d (%s) is damaged and has no legacy tile %s; it stays out of the import",
                           t.index, t.raw_path, t.legacy_path)
    if not targets:
        logger.info("%s: no damaged tile with a legacy tile", idoc_path)
        return []

    fit = fit_levels(_training_tiles(tiles, damaged))
    logger.info("%s: orientation %s, %d training tiles, correlation %.3f-%.3f, rms %.1f counts",
                idoc_path, fit.orientation, len(fit.training_indices), min(fit.correlations.values()),
                max(fit.correlations.values()), fit.rms_error)

    written = []
    for t in targets:
        image = rebuild_tile(t.legacy_path, fit)
        if not raw_tile_is_damaged(t.raw_path):
            raise RecoveryRefused(f"{t.raw_path} became readable; refusing to overwrite it")
        if apply:
            PIL.Image.fromarray(image).save(t.raw_path, format='TIFF')
            provenance = {'recovered_from': os.path.abspath(t.legacy_path), 'idoc_index': t.index,
                          'orientation': fit.orientation, 'training_indices': fit.training_indices,
                          'correlations': {str(k): v for k, v in fit.correlations.items()},
                          'rms_error_counts': fit.rms_error, 'levels': 256,
                          'note': 'Rebuilt from an 8-bit legacy tile; the raw file was damaged.'}
            with open(t.raw_path + '.recovered.json', 'w', encoding='utf-8') as f:
                json.dump(provenance, f, indent=1)
        logger.info("%s tile %d: %s <- %s", 'Rebuilt' if apply else 'Would rebuild', t.index, t.raw_path,
                    t.legacy_path)
        written.append(t.raw_path)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry: one section per run; exit 2 when the section is refused."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('idoc', help="The section's .idoc in the import copy (raw tiles beside it)")
    parser.add_argument('legacy_dir', help="The legacy import's full-resolution tiles (Raw8/TilePyramid/001)")
    parser.add_argument('--apply', action='store_true', help="Write the rebuilt tiles (default: dry run)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        written = recover_section(args.idoc, args.legacy_dir, args.apply)
    except RecoveryRefused as e:
        logger.error("%s: refused: %s", args.idoc, e)
        return 2
    logger.info("%d tile(s) %s", len(written), 'rebuilt' if args.apply else 'would be rebuilt')
    return 0


if __name__ == '__main__':
    sys.exit(main())
