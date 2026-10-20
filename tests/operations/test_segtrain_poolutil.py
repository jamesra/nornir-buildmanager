"""Characterization tests for ``segmentationtraining.poolutil``."""

from __future__ import annotations

from typing import Any
from unittest import mock

import nornir_pools

from nornir_buildmanager.operations.segmentationtraining import poolutil
from nornir_buildmanager.operations.segmentationtraining.poolutil import (
    run_process_jobs,
    submit_bounded,
)


class _FakeTask:
    """Task stub whose ``wait_return`` yields a precomputed value."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def wait_return(self) -> Any:
        return self._value


class _FakePool:
    """Pool stub that runs the callable immediately and records task names."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def add_task(self, name: str, func: Any, job: Any) -> _FakeTask:
        self.names.append(name)
        return _FakeTask(func(job))


def test_empty_jobs_returns_empty_list_without_calls() -> None:
    called: list[int] = []
    assert run_process_jobs(called.append, [], workers=4, name_prefix="x", on_complete=called.append) == []
    assert called == []


def test_single_worker_runs_in_process_in_order() -> None:
    seen: list[int] = []
    results = run_process_jobs(
        lambda v: v + 1, [5, 3, 9], workers=1, name_prefix="x", on_complete=seen.append
    )
    assert results == [6, 4, 10]
    assert seen == [1, 2, 3]


def test_single_worker_does_not_touch_pools() -> None:
    with mock.patch.object(nornir_pools, "GetMultithreadingPool") as get_pool:
        run_process_jobs(lambda v: v, [1, 2], workers=0, name_prefix="x")
    get_pool.assert_not_called()


def test_pooled_branch_returns_job_order_and_counts_completions() -> None:
    pool = _FakePool()
    seen: list[int] = []
    with mock.patch.object(nornir_pools, "GetMultithreadingPool", return_value=pool) as get_pool:
        results = run_process_jobs(
            lambda v: v * 10, [1, 2, 3, 4, 5], workers=2, name_prefix="demo", on_complete=seen.append
        )
    get_pool.assert_called_once_with("segtrain-demo", num_threads=2)
    assert results == [10, 20, 30, 40, 50]
    assert seen == [1, 2, 3, 4, 5]
    assert len(pool.names) == 5
    # The suffix is the count currently outstanding, not a running job number.
    assert pool.names == ["demo-1", "demo-2", "demo-2", "demo-2", "demo-2"]


def test_pooled_branch_without_on_complete() -> None:
    with mock.patch.object(nornir_pools, "GetMultithreadingPool", return_value=_FakePool()):
        assert run_process_jobs(lambda v: -v, [1, 2], workers=3, name_prefix="p") == [-1, -2]


def test_submit_bounded_delegates_arguments() -> None:
    sentinel = iter([1, 2])

    def submit(item: int) -> int:
        return item

    def on_in_flight(count: int) -> None:
        return None

    with mock.patch.object(nornir_pools, "submit_bounded", return_value=sentinel) as delegate:
        result = submit_bounded(submit, [7, 8], max_in_flight=4, on_in_flight=on_in_flight)
    assert result is sentinel
    delegate.assert_called_once_with(submit, [7, 8], max_in_flight=4, on_in_flight=on_in_flight)
    assert poolutil.submit_bounded is submit_bounded
