"""``_submit_tile_compositions`` must match the old window and wire AddTransforms.

Window depth / FIFO / empty input stay covered by
``tests/test_tile_composition_in_flight_bound.py``. This module pins what that
file does not: old-versus-new equivalence after the ``submit_bounded`` move,
``task.imagename`` for ``_gather_volume_space_tiles``, and AddTransforms/StoV args.
"""

from __future__ import annotations

import collections
import unittest
from unittest.mock import patch

import nornir_imageregistration
from hypothesis import given, settings
from hypothesis import strategies as st

from nornir_buildmanager.operations import block as block_ops


class _Task:
    def __init__(self, name: str, pool: _Pool) -> None:
        self.name = name
        self._pool = pool

    def wait(self) -> None:
        self._pool.on_wait(self)


class _Pool:
    """Records add_task calls; wait() only tracks drain order."""

    def __init__(self) -> None:
        self.add_calls: list[tuple] = []
        self.wait_order: list[str] = []

    def add_task(self, name, func, *args, **kwargs) -> _Task:
        self.add_calls.append((name, func, args, kwargs))
        return _Task(name, self)

    def on_wait(self, task: _Task) -> None:
        self.wait_order.append(task.name)


def _hand_rolled_submit(pool, stov_transform, image_to_transform, *, max_in_flight: int):
    """Pre-feca4a83 window (kept here for old-vs-new equivalence)."""
    max_in_flight = max(1, max_in_flight)
    in_flight: collections.deque = collections.deque()

    for imagename, mosaic_to_section_transform in image_to_transform:
        task = pool.add_task(imagename, nornir_imageregistration.transforms.AddTransforms,
                             stov_transform, mosaic_to_section_transform)
        task.imagename = imagename
        if hasattr(mosaic_to_section_transform, 'gridWidth'):
            task.dimX = mosaic_to_section_transform.gridWidth
        if hasattr(mosaic_to_section_transform, 'gridHeight'):
            task.dimY = mosaic_to_section_transform.gridHeight

        in_flight.append(task)
        if len(in_flight) >= max_in_flight:
            yield in_flight.popleft()

    while in_flight:
        yield in_flight.popleft()


class TestSubmitTileCompositions(unittest.TestCase):
    """Unique coverage for the ``submit_bounded`` consolidation."""

    def _items(self, count: int) -> list[tuple[str, object]]:
        return [(f"tile_{i}.png", object()) for i in range(count)]

    def _drain(self, submit_fn, stov: object, items: list, cap: int) -> tuple[_Pool, list]:
        pool = _Pool()
        tasks = []
        for task in submit_fn(pool, stov, items, max_in_flight=cap):
            tasks.append(task)
            task.wait()
        return pool, tasks

    @given(
        count=st.integers(min_value=0, max_value=40),
        cap=st.integers(min_value=-3, max_value=20),
    )
    @settings(max_examples=40, deadline=None)
    def test_matches_hand_rolled_window_and_calls(self, count: int, cap: int) -> None:
        items = self._items(count)
        stov = object()
        new_pool, new_tasks = self._drain(
            block_ops._submit_tile_compositions, stov, items, cap
        )
        old_pool, old_tasks = self._drain(_hand_rolled_submit, stov, items, cap)

        self.assertEqual(old_pool.add_calls, new_pool.add_calls)
        self.assertEqual([t.name for t in old_tasks], [t.name for t in new_tasks])
        self.assertEqual(old_pool.wait_order, new_pool.wait_order)
        self.assertEqual(
            [t.imagename for t in old_tasks],
            [t.imagename for t in new_tasks],
        )

    def test_add_task_receives_AddTransforms_stov_and_imagename(self) -> None:
        pool = _Pool()
        stov = object()
        mosaic_tf = object()
        tasks = list(
            block_ops._submit_tile_compositions(
                pool, stov, [("t.png", mosaic_tf)], max_in_flight=1
            )
        )
        name, func, args, kwargs = pool.add_calls[0]
        self.assertEqual("t.png", name)
        self.assertIs(nornir_imageregistration.transforms.AddTransforms, func)
        self.assertEqual((stov, mosaic_tf), args)
        self.assertEqual({}, kwargs)
        self.assertEqual("t.png", tasks[0].imagename)

    def test_delegates_to_nornir_pools_submit_bounded(self) -> None:
        pool = _Pool()
        with patch(
            "nornir_buildmanager.operations.block.nornir_pools.submit_bounded",
            wraps=block_ops.nornir_pools.submit_bounded,
        ) as mocked:
            list(
                block_ops._submit_tile_compositions(
                    pool, object(), self._items(5), max_in_flight=2
                )
            )
            mocked.assert_called_once()
            _, kwargs = mocked.call_args
            self.assertEqual(2, kwargs["max_in_flight"])


if __name__ == "__main__":
    unittest.main()
