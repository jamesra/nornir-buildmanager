"""
Tile composition must not hold one pickled slice-to-volume transform per tile.

``_ApplyStosToMosaicTransform`` submitted one ``pool.add_task`` per tile of the
section before collecting anything. Each task carries ``StoVTransform`` -- a whole
dense-grid slice-to-volume transform -- as a pickled argument, so peak argument
memory scaled with tile count. Measured on a 129x129 grid (260 KiB pickled) across
400 tiles that is 101.7 MiB live at once, and it grows with both section size and
grid density.

Submission now slides a bounded window, so the live count is capped regardless of
how many tiles the section has. The property that matters is not a particular
number of bytes but that the peak stops depending on tile count.
"""

from __future__ import annotations

import pytest

from nornir_buildmanager.operations.block import (_gather_volume_space_tiles,
                                                  _submit_tile_compositions)


class _CountingPool:
    """Tracks how many submitted tasks have not yet been collected."""

    def __init__(self):
        self.live = 0
        self.peak = 0
        self.submitted: list[str] = []

    def add_task(self, name, func, *args, **kwargs):
        pool = self
        pool.submitted.append(name)
        pool.live += 1
        pool.peak = max(pool.peak, pool.live)

        class _Task:
            def __init__(self):
                self.args = args

            def wait_return(self):
                pool.live -= 1
                return f'composed {name}'

        return _Task()


def _tiles(count):
    return [(f'tile_{i:04d}.png', object()) for i in range(count)]


def _drain(pool, tiles, cap):
    return [t.wait_return() for t in _submit_tile_compositions(pool, 'StoV', tiles, max_in_flight=cap)]


@pytest.mark.parametrize('cap', [1, 4, 16])
def test_in_flight_never_exceeds_the_cap(cap):
    pool = _CountingPool()

    _drain(pool, _tiles(200), cap)

    assert pool.peak <= cap


def test_peak_does_not_grow_with_tile_count():
    """The regression this guards: peak used to be exactly the tile count."""
    small, large = _CountingPool(), _CountingPool()

    _drain(small, _tiles(50), 16)
    _drain(large, _tiles(500), 16)

    assert large.peak == small.peak
    assert large.peak <= 16


def test_the_old_arrangement_would_have_peaked_at_one_per_tile():
    """Pins what the bound is being compared against."""
    pool = _CountingPool()
    tiles = _tiles(200)

    tasks = [pool.add_task(name, None, 'StoV', mts) for name, mts in tiles]
    for task in tasks:
        task.wait_return()

    assert pool.peak == 200


def test_every_tile_is_still_submitted_exactly_once():
    pool = _CountingPool()
    tiles = _tiles(100)

    results = _drain(pool, tiles, 16)

    assert pool.submitted == [name for name, _ in tiles]
    assert len(results) == 100
    assert pool.live == 0


def test_results_are_yielded_in_submission_order():
    pool = _CountingPool()
    tiles = _tiles(40)

    results = _drain(pool, tiles, 8)

    assert results == [f'composed {name}' for name, _ in tiles]


def test_fewer_tiles_than_the_cap_still_all_run():
    pool = _CountingPool()

    results = _drain(pool, _tiles(3), 16)

    assert len(results) == 3
    assert pool.peak == 3


def test_no_tiles_submits_nothing():
    pool = _CountingPool()

    assert _drain(pool, [], 16) == []
    assert pool.submitted == []


def test_the_transform_is_passed_to_every_task():
    pool = _CountingPool()
    tiles = _tiles(20)

    for task in _submit_tile_compositions(pool, 'StoV', tiles, max_in_flight=4):
        assert task.args[0] == 'StoV'
        task.wait_return()


# --- the streamed tasks still satisfy the #59 contract ------------------------

class _Mosaic:
    def __init__(self, names):
        self.ImageToTransform = {n: 'section-space' for n in names}


def test_streamed_tasks_still_report_failures_correctly(caplog):
    """_gather_volume_space_tiles now counts as it goes rather than calling len()."""
    from nornir_buildmanager.exceptions import NornirUserException

    tiles = _tiles(10)
    mosaic = _Mosaic([name for name, _ in tiles])

    class _FailingPool(_CountingPool):
        def add_task(self, name, func, *args, **kwargs):
            task = super().add_task(name, func, *args, **kwargs)
            if name == 'tile_0003.png':
                def boom():
                    raise ValueError('compose failed')
                task.wait_return = boom
            return task

    pool = _FailingPool()
    import logging
    with pytest.raises(NornirUserException) as caught:
        _gather_volume_space_tiles(
            mosaic,
            _submit_tile_compositions(pool, 'StoV', tiles, max_in_flight=4),
            mosaic_path='volume.mosaic',
            Logger=logging.getLogger(__name__))

    # The count must reflect every tile drawn from the generator, not len(list).
    assert '1 of 10 tiles' in str(caught.value)
    assert 'tile_0003.png' not in mosaic.ImageToTransform
