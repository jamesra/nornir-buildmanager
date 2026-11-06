"""Backfill the code histogram and display range of a crushed filter imported before importers recorded them."""

from __future__ import annotations

import logging

from nornir_imageregistration import noise_crush

from nornir_buildmanager.importers import shared


def ComputeCodeHistogram(FilterNode, Min: float | str = shared.DEFAULT_CONTRAST_CUTOFFS[0],
                         Max: float | str = shared.DEFAULT_CONTRAST_CUTOFFS[1], SampleEvery: int | str = 1,
                         Force: bool | str = False, Logger: logging.Logger | None = None, **kwargs):
    """Sum the level-1 code histogram of a crushed filter and store it with ``CrushDisplayMin/Max``.

    Same values the importer records: the histogram of every level-1 tile, and the display window at the cumulative
    ``Min``/``Max`` fractions (the import's -Min/-Max, 0.0001/0.9999 by default). A filter that already has a histogram
    of the right size is left alone unless ``Force``. ``SampleEvery`` N reads every Nth tile. Legacy (uncrushed)
    filters are skipped. Yields the filter when it changed so the pipeline saves it.
    """
    if Logger is None:
        Logger = logging.getLogger(__name__)

    params = noise_crush.CrushParams.from_attributes(FilterNode.attrib)
    if params is None:
        return
    force = Force if isinstance(Force, bool) else str(Force).strip().lower() in ('1', 'true', 'yes')
    if not force and shared.GetFilterCodeHistogram(FilterNode, params) is not None \
            and FilterNode.CrushDisplayRange is not None:
        return
    if not FilterNode.HasTilePyramid:
        Logger.warning(f"ComputeCodeHistograms: {FilterNode.FullPath} has no tile pyramid")
        return
    level = FilterNode.TilePyramid.GetLevel(1)
    if level is None:
        Logger.warning(f"ComputeCodeHistograms: {FilterNode.TilePyramid.FullPath} has no level 1")
        return

    histogram = shared.CodeHistogramFromTiles(level.FullPath, params, sample_every=max(1, int(SampleEvery)))
    if histogram is None:
        Logger.warning(f"ComputeCodeHistograms: no tiles in {level.FullPath}")
        return
    if shared.SetFilterCodeHistogram(FilterNode, histogram, (float(Min), float(Max))):
        yield FilterNode
