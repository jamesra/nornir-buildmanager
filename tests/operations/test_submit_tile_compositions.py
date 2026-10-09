"""Bounded tile-composition submit must not pickle one StoV copy per tile.

``_submit_tile_compositions`` slides a max_in_flight window via
``nornir_pools.submit_bounded`` so assemble keeps at most that many pickled
slice-to-volume transforms live. These tests pin the window depth, FIFO yield
order, and task attribute wiring without running a real pool.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import nornir_imageregistration

from nornir_buildmanager.operations import block as block_ops


class _Task:
    def __init__(self, name: str, pool: _Pool) -> None:
        self.name = name
        self._pool = pool

    def wait(self) -> None:
        self._pool.on_wait(self)


class _Pool:
    """Counts submitted-but-unawaited tasks and records the high-water mark."""

    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.outstanding = 0
        self.peak = 0
        self.wait_order: list[str] = []
        self.add_calls: list[tuple] = []

    def add_task(self, name, func, *args, **kwargs) -> _Task:
        self.submitted.append(name)
        self.outstanding += 1
        self.peak = max(self.peak, self.outstanding)
        self.add_calls.append((name, func, args, kwargs))
        return _Task(name, self)

    def on_wait(self, task: _Task) -> None:
        self.outstanding -= 1
        self.wait_order.append(task.name)


class _GridTransform:
    def __init__(self, grid_width: int | None = None, grid_height: int | None = None) -> None:
        if grid_width is not None:
            self.gridWidth = grid_width
        if grid_height is not None:
            self.gridHeight = grid_height


class TestSubmitTileCompositions(unittest.TestCase):
    """Properties of ``_submit_tile_compositions``."""

    def _items(self, count: int):
        return [(f"tile_{i}.png", _GridTransform(i + 1, i + 2)) for i in range(count)]

    def _run(self, count: int, cap: int) -> tuple[_Pool, list]:
        pool = _Pool()
        stov = object()
        tasks = []
        for task in block_ops._submit_tile_compositions(
            pool, stov, self._items(count), max_in_flight=cap
        ):
            tasks.append(task)
            task.wait()
        return pool, tasks

    def test_window_never_exceeds_max_in_flight(self) -> None:
        for count, cap in ((20, 2), (100, 4), (7, 100)):
            with self.subTest(count=count, cap=cap):
                pool, _ = self._run(count, cap)
                self.assertEqual(count, len(pool.submitted))
                self.assertLessEqual(pool.peak, max(1, cap))

    def test_tasks_yielded_in_submission_order(self) -> None:
        pool, tasks = self._run(30, 3)
        names = [t.name for t in tasks]
        self.assertEqual(pool.submitted, names)
        self.assertEqual(pool.submitted, pool.wait_order)

    def test_task_attributes_match_tile_and_grid(self) -> None:
        pool = _Pool()
        stov = object()
        items = [
            ("a.png", _GridTransform(10, 20)),
            ("b.png", _GridTransform()),
            ("c.png", _GridTransform(3)),
        ]

        tasks = list(
            block_ops._submit_tile_compositions(pool, stov, items, max_in_flight=2)
        )

        self.assertEqual(["a.png", "b.png", "c.png"], [t.imagename for t in tasks])
        self.assertEqual(10, tasks[0].dimX)
        self.assertEqual(20, tasks[0].dimY)
        self.assertFalse(hasattr(tasks[1], "dimX"))
        self.assertFalse(hasattr(tasks[1], "dimY"))
        self.assertEqual(3, tasks[2].dimX)
        self.assertFalse(hasattr(tasks[2], "dimY"))

    def test_add_task_receives_AddTransforms_and_stov(self) -> None:
        pool = _Pool()
        stov = object()
        mosaic_tf = _GridTransform(1, 1)
        list(
            block_ops._submit_tile_compositions(
                pool, stov, [("t.png", mosaic_tf)], max_in_flight=1
            )
        )
        name, func, args, kwargs = pool.add_calls[0]
        self.assertEqual("t.png", name)
        self.assertIs(nornir_imageregistration.transforms.AddTransforms, func)
        self.assertEqual((stov, mosaic_tf), args)
        self.assertEqual({}, kwargs)

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

    def test_nonpositive_cap_clamped_to_one(self) -> None:
        for cap in (0, -3):
            with self.subTest(cap=cap):
                pool, _ = self._run(10, cap)
                self.assertEqual(10, len(pool.submitted))
                self.assertLessEqual(pool.peak, 1)

    def test_empty_input_submits_nothing(self) -> None:
        pool, tasks = self._run(0, 4)
        self.assertEqual([], pool.submitted)
        self.assertEqual([], tasks)


if __name__ == "__main__":
    unittest.main()
