"""Bounded pool helpers for stitch encode (processes) and tile I/O (threads)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def submit_bounded(
    submit: Callable[[T], Any],
    items: Iterable[T],
    *,
    max_in_flight: int,
    on_in_flight: Callable[[int], None] | None = None,
) -> Iterator[Any]:
    """Delegate to ``nornir_pools.submit_bounded``.

    The pools import stays inside the call so a mask-only import does not load it.
    """
    import nornir_pools

    return nornir_pools.submit_bounded(
        submit,
        items,
        max_in_flight=max_in_flight,
        on_in_flight=on_in_flight,
    )


def run_process_jobs(
    func: Callable[[T], R],
    jobs: list[T],
    *,
    workers: int,
    name_prefix: str,
    on_complete: Callable[[int], None] | None = None,
) -> list[R]:
    """Run *func* per job. workers<=1 stays in-process (tests / tiny sections)."""
    if not jobs:
        return []
    if workers <= 1:
        results: list[R] = []
        for index, job in enumerate(jobs, start=1):
            results.append(func(job))
            if on_complete is not None:
                on_complete(index)
        return results
    # GetMultithreadingPool runs pickleable callables in worker processes.
    # GetProcessPool launches shell commands and is the wrong pool here.
    import nornir_pools

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
        if on_complete is not None:
            on_complete(len(results))
    return results
