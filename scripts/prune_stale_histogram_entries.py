#!/usr/bin/env python3
"""Remove ``Histogram`` elements whose data and image files are both gone.

One-time cleanup for volumes re-pruned at a new threshold before the Histogram stage
learned to drop dead entries. RC3's Raw8 filters each kept a ``Histogram Type="_Thr10.0"``
element from a 2022-05 prune whose ``Histogram_Thr10.0.xml``/``.png`` were deleted, so
MosaicReport logged about 810 "Missing image file" errors.

Rules (``nornir_buildmanager.operations.histogram_cleanup``):

- Only ``Histogram`` children of ``Filter`` elements are considered.
- An element is removed only when its data file *and* its image file are both missing.
  An element with either file present is kept, so histograms for several thresholds can
  coexist. Elements whose image alone is missing are listed but left for the Histogram
  or AdjustContrast stage to regenerate.
- No file is deleted; only the metadata entry goes.

Dry run by default. ``--apply`` copies each filter's VolumeData.xml into ``--backup-dir``
(same relative path as in the volume) before saving the filter through the volume model,
so the SQLite metadata shadow is updated by the normal save path.

Usage:
  python prune_stale_histogram_entries.py /storage2/RC3
  python prune_stale_histogram_entries.py /storage2/RC3 --apply \\
      --backup-dir /storage2/_nornir_backups/RC3/2026-11-03/stale-histograms
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from collections.abc import Iterable, Iterator
from typing import cast

import nornir_buildmanager.volumemanager
from nornir_buildmanager.operations import histogram_cleanup
from nornir_buildmanager.volumemanager import FilterNode, HistogramNode

_logger = logging.getLogger('prune_stale_histogram_entries')


def iter_filters(volume_path: str) -> Iterator[FilterNode]:
    """Every Filter element of the volume, loading linked containers as it goes."""
    volume = nornir_buildmanager.volumemanager.VolumeManager.Load(volume_path)
    if volume is None:
        raise SystemExit(f"No VolumeData.xml in {volume_path}")
    yield from cast(Iterable[FilterNode], volume.findall('Block/Section/Channel/Filter'))


def backup_filter_metadata(filter_node: FilterNode, volume_path: str, backup_dir: str) -> str:
    """Copy the filter's VolumeData.xml under *backup_dir*, keeping its path relative to the volume."""
    source = os.path.join(filter_node.FullPath, 'VolumeData.xml')
    relative = os.path.relpath(source, volume_path)
    target = os.path.join(backup_dir, relative)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copy2(source, target)
    return target


def run(volume_path: str, apply: bool, backup_dir: str | None) -> dict[str, int]:
    """Scan (and with *apply*, clean) the volume; returns the counts it printed."""
    volume_path = os.path.abspath(volume_path)
    counts = {'filters': 0, 'filters_changed': 0, 'stale': 0, 'image_only_missing': 0}
    for filter_node in iter_filters(volume_path):
        counts['filters'] += 1
        stale = histogram_cleanup.stale_histograms(filter_node)
        for histogram in cast(Iterable[HistogramNode], filter_node.findall('Histogram')):
            if histogram not in stale and histogram.ImageFullPath and not os.path.exists(histogram.ImageFullPath):
                counts['image_only_missing'] += 1
                print(f"KEEP (image missing, data present) {filter_node.FullPath}: "
                      f"{histogram_cleanup.describe_histogram(histogram)}")
        if not stale:
            continue
        counts['stale'] += len(stale)
        counts['filters_changed'] += 1
        for histogram in stale:
            print(f"{'REMOVE' if apply else 'WOULD REMOVE'} {filter_node.FullPath}: "
                  f"{histogram_cleanup.describe_histogram(histogram)}")
        if not apply:
            continue
        assert backup_dir is not None
        backup = backup_filter_metadata(filter_node, volume_path, backup_dir)
        _logger.info("Backed up %s", backup)
        histogram_cleanup.remove_histograms(filter_node, stale, _logger)
        filter_node.Save(recurse=False)

    verb = 'Removed' if apply else 'Would remove'
    print(f"{verb} {counts['stale']} stale histogram element(s) from {counts['filters_changed']} of "
          f"{counts['filters']} filter(s); {counts['image_only_missing']} histogram(s) with only the image "
          f"missing were kept.")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or '').split('\n\n')[0])
    parser.add_argument('volume_path', help='Volume directory (contains the top VolumeData.xml)')
    parser.add_argument('--apply', action='store_true', help='Remove the entries (default: dry run)')
    parser.add_argument('--backup-dir', help='Where to copy each changed VolumeData.xml first; required with --apply')
    args = parser.parse_args(argv)
    if args.apply and not args.backup_dir:
        parser.error('--apply needs --backup-dir')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    run(args.volume_path, args.apply, args.backup_dir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
