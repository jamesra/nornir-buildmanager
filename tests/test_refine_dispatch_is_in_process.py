"""
Refine's serial dispatch is required, not a leftover debug pin.

``RefineStosGroup`` dispatches with ``run_bounded_stos_jobs(None, pool_jobs,
max_in_flight=1)``. That looks like a stage nobody got round to parallelising, and
the two sibling stages really do pass a pool. The difference is architectural.

``ScaleStosGroup`` and ``LinearBlendStosGroup`` run a pooled job that *returns a
result*, which the parent then applies to the volume model
(``_apply_scale_stos_job(context, result)``). Refine's job function is
``_run_refine_or_manual_copy``, which mutates the volume model itself -- it creates
the output node, removes nodes it rejects, and rebases paths on the node it hands
back. Dispatching that through a real pool would pickle ``context`` to a worker and
strand every mutation on a copy.

These tests pin the property refine depends on: with ``pool=None`` the job runs in
the calling process, so its mutations are visible to the caller. If someone swaps in
a pool, or changes the helper to always use one, the first two tests fail.
"""

from __future__ import annotations

import os
import threading

from nornir_buildmanager.operations import stosgroup_workers


class _ModelNode:
    """Stands in for the volume-model node the refine job mutates."""

    def __init__(self):
        self.removed = []
        self.checksum_reset = False


def _job(name, func, context):
    return stosgroup_workers.StosGroupPoolJob(
        name=name, func=func, args=(context,), kwargs={}, context=context)


def test_mutations_made_by_the_job_are_visible_to_the_caller():
    """The property refine relies on: no pickling, so the model is the real one."""
    node = _ModelNode()

    def refine(context):
        context.removed.append('0002-0001.stos')
        context.checksum_reset = True
        return 'done'

    jobs = [_job('0002-0001.stos', refine, node)]

    results = list(stosgroup_workers.run_bounded_stos_jobs(None, jobs, max_in_flight=1))

    assert results[0][1] == 'done'
    assert node.removed == ['0002-0001.stos'], 'mutation landed on a copy, not the model'
    assert node.checksum_reset is True


def test_the_job_runs_in_the_calling_process_and_thread():
    parent = (os.getpid(), threading.get_ident())
    seen = []

    def refine(_context):
        seen.append((os.getpid(), threading.get_ident()))

    jobs = [_job(f'{i}.stos', refine, object()) for i in range(4)]
    list(stosgroup_workers.run_bounded_stos_jobs(None, jobs, max_in_flight=1))

    assert seen == [parent] * 4


def test_the_object_yielded_back_is_the_same_object_not_a_copy():
    """The caller mutates the returned stos node, so identity has to survive."""
    node = _ModelNode()

    jobs = [_job('a.stos', lambda context: context, node)]

    (_job_out, result), = stosgroup_workers.run_bounded_stos_jobs(None, jobs, max_in_flight=1)

    assert result is node


def test_jobs_run_in_order_when_serial():
    order = []
    jobs = [_job(f'{i}.stos', lambda _c, i=i: order.append(i), object()) for i in range(6)]

    list(stosgroup_workers.run_bounded_stos_jobs(None, jobs, max_in_flight=1))

    assert order == [0, 1, 2, 3, 4, 5]


def test_a_single_job_stays_in_process_even_when_a_pool_is_supplied():
    """The helper short-circuits len(jobs) <= 1, so one slice is never shipped out."""
    node = _ModelNode()

    class _ExplodingPool:
        def add_task(self, *args, **kwargs):
            raise AssertionError('a single job must not be dispatched to a pool')

    jobs = [_job('a.stos', lambda context: context.removed.append('x'), node)]

    list(stosgroup_workers.run_bounded_stos_jobs(_ExplodingPool(), jobs, max_in_flight=4))

    assert node.removed == ['x']
