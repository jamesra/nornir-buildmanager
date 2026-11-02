"""Each refine job starts loading the next refined pair's images before it computes.

Refine runs one pair at a time, so the pipeline overlaps the next pair's image load
with the current pair: ``RefineInvoker`` gives each job the next job's input stos,
skipping manual copies (they read no images), and ``_run_refine_or_manual_copy``
hands it to the prefetch function before calling RefineFunc.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import cast

from nornir_buildmanager.operations import stosgroup_workers
from nornir_buildmanager.operations.block import (
    _next_refine_inputs,
    _RefineStosJobContext,
    _run_refine_or_manual_copy,
)

_REFINE = stosgroup_workers.RefineScanDecision.REFINE
_MANUAL = stosgroup_workers.RefineScanDecision.MANUAL_COPY


def _job(name: str, decision=_REFINE) -> _RefineStosJobContext:
    return cast(_RefineStosJobContext, SimpleNamespace(decision=decision, input_stos_path=name))


def test_each_job_gets_the_next_refined_input():
    jobs = [_job('a'), _job('b'), _job('c')]

    assert _next_refine_inputs(jobs) == ['b', 'c', None]


def test_manual_copies_are_skipped():
    jobs = [_job('a'), _job('m1', _MANUAL), _job('b'), _job('m2', _MANUAL)]

    assert _next_refine_inputs(jobs) == ['b', 'b', None, None]


def test_no_jobs():
    assert _next_refine_inputs([]) == []


class _SectionMapping:
    def remove(self, node):
        pass


def test_prefetch_runs_before_refine(tmp_path):
    output_stos_path = os.path.join(str(tmp_path), '0002-0001.stos')
    context = cast(_RefineStosJobContext, SimpleNamespace(
        decision=_REFINE,
        decision_reason='',
        output_stos_node=SimpleNamespace(FullPath=output_stos_path, Path='0002-0001.stos'),
        output_section_mapping_node=_SectionMapping(),
        input_stos_path=os.path.join(str(tmp_path), 'in.stos'),
        output_stos_path=output_stos_path,
        mapped_section=2,
        control_section=1,
    ))
    calls: list[tuple[str, str]] = []

    def refine(in_path, _out_path, **_kwargs):
        calls.append(('refine', in_path))

    _run_refine_or_manual_copy(
        context, refine, {},
        prefetch_func=lambda path: calls.append(('prefetch', path)),
        next_input_stos_path='next.stos')

    assert calls == [('prefetch', 'next.stos'), ('refine', context.input_stos_path)]


def test_no_prefetch_without_a_next_pair(tmp_path):
    context = cast(_RefineStosJobContext, SimpleNamespace(
        decision=_REFINE, decision_reason='',
        output_stos_node=SimpleNamespace(FullPath='x', Path='x'),
        output_section_mapping_node=_SectionMapping(),
        input_stos_path='in.stos', output_stos_path=os.path.join(str(tmp_path), 'out.stos'),
        mapped_section=2, control_section=1))
    calls: list[str] = []

    _run_refine_or_manual_copy(
        context, lambda *_a, **_k: None, {},
        prefetch_func=calls.append, next_input_stos_path=None)

    assert calls == []
