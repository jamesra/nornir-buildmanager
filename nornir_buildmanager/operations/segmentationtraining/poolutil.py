"""Bounded pool helpers for stitch encode (processes) and tile I/O (threads)."""

from __future__ import annotations

import collections
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

import nornir_pools

T = TypeVar("T")
R = TypeVar("R")


def submit_bounded(
    submit: Callable[[T], Any],
    items: Iterable[T],
    *,
    max_in_flight: int,
    on_in_flight: Callable[[int], None] | None = None,
) -> Iterator[Any]:
    """Yield tasks in submission order while keeping at most *max_in_flight* queued.

    ``GetMultithreadingPool`` runs pickleable Python callables in worker
    processes (not ``GetProcessPool``, which launches shell commands).
    """
    max_in_flight = max(1, max_in_flight)
    in_flight: collections.deque = collections.deque()
    for item in items:
        in_flight.append(submit(item))
        if on_in_flight is not None:
            on_in_flight(len(in_flight))
        if len(in_flight) >= max_in_flight:
            yield in_flight.popleft()
    while in_flight:
        if on_in_flight is not None:
            on_in_flight(len(in_flight))
        yield in_flight.popleft()


def run_process_jobs(
    func: Callable[[T], R],
    jobs: list[T],
    *,
    workers: int,
    name_prefix: str,
) -> list[R]:
    """Run *func* per job. workers<=1 stays in-process (tests / tiny sections)."""
    if not jobs:
        return []
    if workers <= 1:
        return [func(job) for job in jobs]
    pool = nornir_pools.GetMultithreadingPool(
        f"segtrain-{name_prefix}",
        num_threads=workers,
    )
    results: list[R] = []
    peak = 0
    outstanding = 0

    def submit(job: T):
        nonlocal outstanding, peak
        outstanding += 1
        peak = max(peak, outstanding)
        return pool.add_task(f"{name_prefix}-{outstanding}", func, job)

    for task in submit_bounded(submit, jobs, max_in_flight=workers):
        results.append(task.wait_return())
        outstanding -= 1
    return results
