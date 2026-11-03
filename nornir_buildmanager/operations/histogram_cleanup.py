"""Find and remove ``Histogram`` elements whose files are gone.

A filter keeps one ``Histogram`` element per input-transform type, e.g. ``_Thr10.0`` and
``_Thr2500.0`` after a section is re-pruned at a new threshold. The Histogram stage only
revisits the element for the current type, so an older one stays in the metadata after
its ``Histogram_Thr*.xml``/``.png`` files are deleted. RC3 (2022) had about 810 of these,
and each made MosaicReport log "Missing image file".

An element is stale only when its data file *and* its image file are both missing.
Elements with either file present are kept, so a filter can still hold histograms for
several thresholds. Removing a stale element only edits metadata; no file is deleted.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from typing import cast

from nornir_buildmanager.volumemanager import FilterNode, HistogramNode


def _file_missing(path: str) -> bool:
    """True when *path* is empty (no node) or names no file."""
    return not path or not os.path.exists(path)


def histogram_files_missing(histogram: HistogramNode) -> bool:
    """True if both the histogram's data file and its image file are missing."""
    return _file_missing(histogram.DataFullPath) and _file_missing(histogram.ImageFullPath)


def stale_histograms(filter_node: FilterNode, *, keep_type: str | None = None,
                     transform_derived_only: bool = False) -> list[HistogramNode]:
    """Histograms of *filter_node* whose data and image files are both missing.

    *keep_type* is never returned (the histogram a stage is about to write).
    With *transform_derived_only*, only histograms built from an input transform
    (they carry ``InputTransformType``, e.g. the prune thresholds) are considered,
    which leaves the import-time ``RawDataHistogram`` alone.
    """
    stale: list[HistogramNode] = []
    for histogram in cast(Iterable[HistogramNode], filter_node.findall('Histogram')):
        if keep_type is not None and histogram.get('Type') == keep_type:
            continue
        if transform_derived_only and 'InputTransformType' not in histogram.attrib:
            continue
        if histogram_files_missing(histogram):
            stale.append(histogram)
    return stale


def describe_histogram(histogram: HistogramNode) -> str:
    """One line naming a histogram element and its file paths, for logs."""
    return (f"Histogram Type={histogram.get('Type')!r} data={histogram.DataFullPath or '(none)'} "
            f"image={histogram.ImageFullPath or '(none)'}")


def remove_histograms(filter_node: FilterNode, histograms: Iterable[HistogramNode],
                      logger: logging.Logger) -> int:
    """Detach *histograms* from *filter_node* without touching files; returns the count removed.

    ``Clean()`` is not used because it would also try to delete the element's files;
    these are already gone, and removal must never delete anything on disk.
    """
    removed = 0
    for histogram in histograms:
        logger.info("Removing stale %s from %s", describe_histogram(histogram), filter_node.FullPath)
        filter_node.remove(histogram)
        removed += 1
    return removed
