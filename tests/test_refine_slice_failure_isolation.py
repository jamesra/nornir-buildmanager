"""
One slice that refine cannot handle must not take the rest of the stage with it.

Refine runs strictly one slice at a time -- the caller passes ``pool=None`` and
``max_in_flight=1`` to ``run_bounded_stos_jobs``, which then calls each job inline
and yields the result. Nothing between that loop and ``_run_refine_or_manual_copy``
catches anything, so the ``except Exception ... raise`` in the latter aborted the
whole generator on the first bad slice and every sibling after it was never
attempted.

That was also inconsistent with the two checks immediately below it in the same
function: a refine that produces *no* output, or one that produces an unloadable
transform, already drops just that slice and carries on. A raised exception is now
handled the same way, and the caller reports a summary at the end so a run in which
everything failed is still obvious.
"""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace
from typing import cast

import pytest
from nornir_imageregistration.files import stosfile

from nornir_buildmanager.operations import stosgroup_workers
from nornir_buildmanager.operations.block import (_RefineStosJobContext,
                                                  _run_refine_or_manual_copy)


def _write_valid_stos(out_path: str) -> None:
    """Write a loadable rigid .stos so the post-refine validity checks pass."""
    directory = os.path.dirname(out_path)
    stos = stosfile.StosFile()
    stos.ControlImagePath = directory
    stos.MappedImagePath = directory
    stos.ControlImageName = '0001.png'
    stos.MappedImageName = '0002.png'
    stos.ControlImageDim = [0, 0, 512, 512]
    stos.MappedImageDim = [0, 0, 512, 512]
    stos.Transform = ('FixedCenterOfRotationAffineTransform_double_2_2 vp 8 '
                      '1 0 0 1 0 0 0 0 fp 3 0 256 256')
    stos.Save(out_path)


class _SectionMapping:
    """Records the stos nodes the refine step discards."""

    def __init__(self):
        self.removed = []

    def remove(self, node):
        self.removed.append(node)


def _context(tmp_path, name='0002-0001.stos') -> _RefineStosJobContext:
    """Only the attributes the refine branch touches; the real context needs a volume."""
    output_stos_path = os.path.join(str(tmp_path), name)
    return cast(_RefineStosJobContext, SimpleNamespace(
        decision=stosgroup_workers.RefineScanDecision.REFINE,
        decision_reason='',
        output_stos_node=SimpleNamespace(FullPath=output_stos_path, Path=name),
        output_section_mapping_node=_SectionMapping(),
        input_stos_path=os.path.join(str(tmp_path), 'in_' + name),
        output_stos_path=output_stos_path,
        mapped_section=2,
        control_section=1,
    ))


def test_a_raising_slice_is_dropped_rather_than_aborting(tmp_path):
    context = _context(tmp_path)

    def refine(_in_path, _out_path, **_kwargs):
        raise ValueError('ir-stos-grid blew up on this pair')

    stos_node, refined = _run_refine_or_manual_copy(context, refine, {})

    assert stos_node is None
    assert refined is False
    assert context.output_section_mapping_node.removed == [context.output_stos_node]


def test_a_partial_output_left_by_the_failure_is_not_kept(tmp_path):
    """An exception mid-write can leave a truncated .stos that would look real."""
    context = _context(tmp_path)

    def refine(_in_path, out_path, **_kwargs):
        with open(out_path, 'w') as f:
            f.write('half a transform')
        raise ValueError('crashed after writing')

    _run_refine_or_manual_copy(context, refine, {})

    assert not os.path.exists(context.output_stos_path)


def test_the_failure_is_logged_at_error_with_the_cause(tmp_path, caplog):
    context = _context(tmp_path)

    def refine(_in_path, _out_path, **_kwargs):
        raise ValueError('ir-stos-grid blew up on this pair')

    with caplog.at_level(logging.ERROR):
        _run_refine_or_manual_copy(context, refine, {})

    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any('blew up on this pair' in m for m in messages)


def test_a_successful_slice_is_unaffected(tmp_path):
    context = _context(tmp_path)

    def refine(_in_path, out_path, **_kwargs):
        _write_valid_stos(out_path)

    stos_node, refined = _run_refine_or_manual_copy(context, refine, {})

    assert stos_node is context.output_stos_node
    assert refined is True
    assert context.output_section_mapping_node.removed == []


# --- the property the finding is really about ---------------------------------

def test_every_slice_is_attempted_even_when_one_fails(tmp_path):
    """Before the fix the generator aborted and later slices never ran."""
    contexts = [_context(tmp_path, f'000{i}-0001.stos') for i in range(1, 6)]
    attempted: list[str] = []

    def refine(in_path, out_path, **_kwargs):
        attempted.append(os.path.basename(out_path))
        if out_path.endswith('0002-0001.stos'):
            raise ValueError('ir-stos-grid blew up on this pair')
        _write_valid_stos(out_path)

    jobs = [stosgroup_workers.StosGroupPoolJob(
        name=os.path.basename(c.output_stos_path),
        func=_run_refine_or_manual_copy,
        args=(c, refine, {}),
        kwargs={},
        context=c) for c in contexts]

    results = list(stosgroup_workers.run_bounded_stos_jobs(None, jobs, max_in_flight=1))

    assert len(attempted) == 5, 'the failing slice must not stop its siblings'
    assert len(results) == 5

    dropped = [job.name for job, (node, _) in results if node is None]
    assert dropped == ['0002-0001.stos']


def test_keyboard_interrupt_still_stops_the_stage(tmp_path):
    """Skipping is for bad data, not for the operator cancelling the run."""
    context = _context(tmp_path)

    def refine(_in_path, _out_path, **_kwargs):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        _run_refine_or_manual_copy(context, refine, {})
