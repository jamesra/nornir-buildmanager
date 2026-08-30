"""
CalculateHistogram held one tile buffer per tile, for the whole MRC file.

The memmap for each tile was created in the *submission* loop::

    for iTile, tile_header in enumerate(mrc_obj.tile_meta):
        img = mrc_obj.get_tile_as_numpy_memmap(iTile)
        t = pool.add_task(..., img, ...)
        tasks.append(t)

    for t in tasks:                      # nothing is consumed until here
        ...

Each buffer stays reachable as a task argument until that task is consumed, and no
task was consumed until every one had been queued. Peak memory therefore scaled with
tile count rather than with cores. Measured on a 120-tile file, 120 of 120 buffers
were alive at once; at a realistic 4096x4096 uint16 tile that is 3.8 GiB, and MRC
files routinely hold more tiles than that.

A bounded sliding window now drains in submission order, sized to the pool rather
than to the machine -- queueing deeper than the pool can run only buys more resident
buffers. Peak is flat at ~33 from 60 tiles through 480, and throughput is unchanged
(102.7 ms vs 108.1 ms median over 400 tiles).

The sibling sites named in the finding, ExportImages and cache_tiles, do not have
this problem: ExportImage opens the tile *inside* the worker, so those queue only
integers and shared references and are bounded by thread concurrency already.
"""

from __future__ import annotations

import gc
import weakref
from typing import cast

import numpy as np
import pytest

import nornir_imageregistration.image_stats
from nornir_buildmanager.importers import mrc

TILE_PX = 64


class _Tracker:
    """Counts how many tile buffers are alive at the same moment."""

    def __init__(self):
        self.live = 0
        self.peak = 0
        self.created = 0

    def make(self, _iTile):
        arr = np.full(TILE_PX * TILE_PX, 7, dtype=np.uint16)
        self.created += 1
        self.live += 1
        self.peak = max(self.peak, self.live)
        weakref.finalize(arr, self._released)
        return arr

    def _released(self):
        self.live -= 1


class _Histogram:
    def __init__(self):
        self.n = 1

    def AddHistogram(self, other):
        self.n += other.n


class _FakeMrc:
    def __init__(self, n_tiles, tracker):
        self.num_tiles = n_tiles
        self.tile_meta = list(range(n_tiles))
        self._tracker = tracker

    def get_tile_as_numpy_memmap(self, iTile):
        return self._tracker.make(iTile)


@pytest.fixture(autouse=True)
def _stub_histogram(monkeypatch):
    def work(img, bpp=None, num_bins=None):
        int(np.asarray(img).sum())
        return _Histogram()

    monkeypatch.setattr(nornir_imageregistration.image_stats, 'HistogramOfArray', work)


def _run(n_tiles):
    tracker = _Tracker()
    result = mrc.MRCImport.CalculateHistogram(
        cast(mrc.MRCFile, _FakeMrc(n_tiles, tracker)), bpp=16)
    gc.collect()
    return tracker, result


# --- the bound ----------------------------------------------------------------

def test_peak_live_buffers_is_far_below_the_tile_count():
    tracker, _ = _run(240)

    assert tracker.created == 240
    assert tracker.peak < 240 / 2, (
        f'{tracker.peak} of 240 tile buffers were alive at once')


def test_peak_does_not_grow_with_tile_count():
    """The property that matters: bounded by cores, not by file size."""
    small, _ = _run(60)
    large, _ = _run(480)

    assert large.created == 8 * small.created
    # Allow a little slack for finalizers that have not run yet.
    assert large.peak <= small.peak + 8, (
        f'peak grew from {small.peak} at 60 tiles to {large.peak} at 480')


# --- correctness must be untouched --------------------------------------------

@pytest.mark.parametrize('n_tiles', [1, 2, 33, 100])
def test_every_tile_is_still_folded_into_the_composite(n_tiles):
    tracker, result = _run(n_tiles)

    assert tracker.created == n_tiles
    assert result is not None
    assert result.n == n_tiles


def test_a_failing_tile_still_raises():
    def boom(img, bpp=None, num_bins=None):
        raise ValueError('bad tile')

    nornir_imageregistration.image_stats.HistogramOfArray = boom

    with pytest.raises(ValueError, match='bad tile'):
        _run(40)


def test_no_tile_buffers_are_retained_after_the_call():
    tracker, _ = _run(80)

    assert tracker.live == 0, 'tile buffers outlived CalculateHistogram'


# --- the shape of the fix -----------------------------------------------------

def test_submission_and_consumption_are_interleaved():
    """A single drain loop after the full submission loop is what caused this."""
    import inspect

    source = inspect.getsource(mrc.MRCImport.CalculateHistogram)
    code = '\n'.join(line for line in source.splitlines()
                     if not line.lstrip().startswith('#'))

    assert 'tasks.append' not in code, 'accumulating every task reintroduces the bound'
    assert 'popleft' in code
