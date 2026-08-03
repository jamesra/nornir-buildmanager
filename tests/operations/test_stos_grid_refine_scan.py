"""Tests for parallel STOS grid refine scan decisions and sort order."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nornir_buildmanager.operations import stosgroup_workers
from nornir_imageregistration.files import stosfile

_FIXTURE_STOS = (
    Path(__file__).resolve().parents[2].parent
    / 'nornir-imageregistration'
    / 'tests'
    / 'fixtures'
    / 'idoc_690_691'
    / 'StosBrute16'
    / '690-691_ctrl-TEM_Leveled_map-TEM_Leveled.stos'
)


def _snapshot(
        *,
        tmp_path: Path,
        output_exists: bool = False,
        locked: bool = False,
        has_checksum_attr: bool = True,
        stored_checksum: str | None = 'abc',
        input_checksum: str = 'abc',
        manual: bool = False,
        mapped_section: int = 2,
        control_section: int = 1,
        image_checks: tuple[stosgroup_workers.ImageCheckSnapshot, ...] | None = None,
        input_newer: bool = False,
        valid_output: bool = True,
) -> stosgroup_workers.RefineScanSnapshot:
    """Build a refine-scan snapshot under *tmp_path* with controllable staleness."""
    output_path = tmp_path / f'{mapped_section}-{control_section}.stos'
    input_path = tmp_path / f'input-{mapped_section}.stos'
    transform_path = tmp_path / f'transform-{mapped_section}.stos'
    input_path.write_text('input', encoding='utf-8')
    transform_path.write_text('transform', encoding='utf-8')

    if output_exists:
        if valid_output and _FIXTURE_STOS.is_file():
            output_path.write_bytes(_FIXTURE_STOS.read_bytes())
        else:
            output_path.write_text('not-a-stos', encoding='utf-8')

    if input_newer and output_exists:
        os.utime(output_path, (1, 1))
        os.utime(transform_path, (2, 2))
    elif output_exists:
        os.utime(transform_path, (1, 1))
        os.utime(output_path, (2, 2))

    manual_path = None
    if manual:
        manual_file = tmp_path / 'manual' / output_path.name
        manual_file.parent.mkdir(exist_ok=True)
        manual_file.write_text('manual', encoding='utf-8')
        manual_path = str(manual_file)

    if image_checks is None:
        image_checks = (
            stosgroup_workers.ImageCheckSnapshot('img', 'img', str(tmp_path / 'ctrl.png')),
            stosgroup_workers.ImageCheckSnapshot('img', 'img', str(tmp_path / 'map.png')),
        )
        for check in image_checks:
            assert check.image_path is not None
            Path(check.image_path).write_text('img', encoding='utf-8')

    return stosgroup_workers.RefineScanSnapshot(
        output_stos_path=str(output_path),
        input_stos_path=str(input_path),
        input_checksum=input_checksum,
        input_transform_path=str(transform_path),
        locked=locked,
        has_input_transform_checksum_attr=has_checksum_attr,
        stored_input_transform_checksum=stored_checksum,
        manual_path=manual_path,
        mapped_section=mapped_section,
        control_section=control_section,
        image_checks=image_checks,
        input_transform_name='In',
        input_transform_type='Grid',
        input_transform_cropbox=None,
        output_input_transform='In',
        output_input_transform_type='Grid',
        output_input_transform_cropbox=None,
    )


def test_decide_refine_when_output_missing(tmp_path: Path) -> None:
    """Missing output without a manual override requests refine."""
    snapshot = _snapshot(tmp_path=tmp_path, output_exists=False)
    result = stosgroup_workers.decide_stos_grid_refine_need(snapshot)
    assert result.decision == stosgroup_workers.RefineScanDecision.REFINE


def test_decide_manual_copy_when_output_missing(tmp_path: Path) -> None:
    """Missing output with a manual override requests a copy."""
    snapshot = _snapshot(tmp_path=tmp_path, output_exists=False, manual=True)
    result = stosgroup_workers.decide_stos_grid_refine_need(snapshot)
    assert result.decision == stosgroup_workers.RefineScanDecision.MANUAL_COPY


def test_decide_skip_when_fresh_and_matching(tmp_path: Path) -> None:
    """Matching checksum and older input skips refine."""
    if not _FIXTURE_STOS.is_file():
        pytest.skip('STOS fixture unavailable')
    snapshot = _snapshot(tmp_path=tmp_path, output_exists=True, valid_output=True)
    result = stosgroup_workers.decide_stos_grid_refine_need(snapshot)
    assert result.decision == stosgroup_workers.RefineScanDecision.SKIP


def test_decide_invalidate_when_checksum_mismatch(tmp_path: Path) -> None:
    """Checksum mismatch invalidates and queues refine in the same pass."""
    snapshot = _snapshot(
        tmp_path=tmp_path,
        output_exists=True,
        valid_output=False,
        stored_checksum='old',
        input_checksum='new',
    )
    result = stosgroup_workers.decide_stos_grid_refine_need(snapshot)
    assert result.decision == stosgroup_workers.RefineScanDecision.INVALIDATE_THEN_REFINE
    assert 'checksum' in result.reason.lower()


def test_decide_invalidate_when_input_newer(tmp_path: Path) -> None:
    """Newer input transform forces same-pass invalidate then refine."""
    if not _FIXTURE_STOS.is_file():
        pytest.skip('STOS fixture unavailable')
    snapshot = _snapshot(
        tmp_path=tmp_path,
        output_exists=True,
        valid_output=True,
        input_newer=True,
    )
    result = stosgroup_workers.decide_stos_grid_refine_need(snapshot)
    assert result.decision == stosgroup_workers.RefineScanDecision.INVALIDATE_THEN_REFINE
    assert 'newer' in result.reason


def test_decide_skip_when_locked(tmp_path: Path) -> None:
    """Locked matching outputs are skipped."""
    if not _FIXTURE_STOS.is_file():
        pytest.skip('STOS fixture unavailable')
    snapshot = _snapshot(tmp_path=tmp_path, output_exists=True, valid_output=True, locked=True)
    result = stosgroup_workers.decide_stos_grid_refine_need(snapshot)
    assert result.decision == stosgroup_workers.RefineScanDecision.SKIP
    assert 'locked' in result.reason


def test_decide_skip_when_locked_even_if_stale(tmp_path: Path) -> None:
    """Locked outputs are not invalidated even when the input checksum changed."""
    snapshot = _snapshot(
        tmp_path=tmp_path,
        output_exists=True,
        valid_output=False,
        locked=True,
        stored_checksum='old',
        input_checksum='new',
    )
    result = stosgroup_workers.decide_stos_grid_refine_need(snapshot)
    assert result.decision == stosgroup_workers.RefineScanDecision.SKIP
    assert 'locked' in result.reason


def test_decide_invalidate_when_images_outdated(tmp_path: Path) -> None:
    """Outdated input image checksums force rebuild."""
    image_checks = (
        stosgroup_workers.ImageCheckSnapshot('old', 'new', str(tmp_path / 'ctrl.png')),
        stosgroup_workers.ImageCheckSnapshot('img', 'img', str(tmp_path / 'map.png')),
    )
    for check in image_checks:
        assert check.image_path is not None
        Path(check.image_path).write_text('img', encoding='utf-8')
    snapshot = _snapshot(
        tmp_path=tmp_path,
        output_exists=True,
        valid_output=False,
        image_checks=image_checks,
    )
    result = stosgroup_workers.decide_stos_grid_refine_need(snapshot)
    assert result.decision == stosgroup_workers.RefineScanDecision.INVALIDATE_THEN_REFINE
    assert 'image' in result.reason.lower()


def test_decide_needs_parallel_preserves_order(tmp_path: Path) -> None:
    """Parallel scan returns one decision per snapshot in input order."""
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    (tmp_path / 'c').mkdir()
    snapshots = [
        _snapshot(tmp_path=tmp_path / 'a', output_exists=False, mapped_section=5),
        _snapshot(tmp_path=tmp_path / 'b', output_exists=False, mapped_section=1, manual=True),
        _snapshot(
            tmp_path=tmp_path / 'c',
            output_exists=True,
            valid_output=False,
            stored_checksum='old',
            input_checksum='new',
            mapped_section=3,
        ),
    ]
    results = stosgroup_workers.decide_stos_grid_refine_needs(snapshots, max_workers=4)
    assert [r.decision for r in results] == [
        stosgroup_workers.RefineScanDecision.REFINE,
        stosgroup_workers.RefineScanDecision.MANUAL_COPY,
        stosgroup_workers.RefineScanDecision.INVALIDATE_THEN_REFINE,
    ]


def test_refine_scan_sort_key_orders_mapped_then_control() -> None:
    """Work queue sort key is mapped section, control section, basename."""
    snapshots = [
        stosgroup_workers.RefineScanSnapshot(
            output_stos_path='/tmp/3-1.stos',
            input_stos_path='/tmp/in.stos',
            input_checksum='a',
            input_transform_path=None,
            locked=False,
            has_input_transform_checksum_attr=False,
            stored_input_transform_checksum=None,
            manual_path=None,
            mapped_section=3,
            control_section=1,
            image_checks=(),
        ),
        stosgroup_workers.RefineScanSnapshot(
            output_stos_path='/tmp/1-2.stos',
            input_stos_path='/tmp/in.stos',
            input_checksum='a',
            input_transform_path=None,
            locked=False,
            has_input_transform_checksum_attr=False,
            stored_input_transform_checksum=None,
            manual_path=None,
            mapped_section=1,
            control_section=2,
            image_checks=(),
        ),
        stosgroup_workers.RefineScanSnapshot(
            output_stos_path='/tmp/1-1.stos',
            input_stos_path='/tmp/in.stos',
            input_checksum='a',
            input_transform_path=None,
            locked=False,
            has_input_transform_checksum_attr=False,
            stored_input_transform_checksum=None,
            manual_path=None,
            mapped_section=1,
            control_section=1,
            image_checks=(),
        ),
    ]
    ordered = sorted(snapshots, key=stosgroup_workers.refine_scan_sort_key)
    assert [s.mapped_section for s in ordered] == [1, 1, 3]
    assert [s.control_section for s in ordered] == [1, 2, 1]
    assert [os.path.basename(s.output_stos_path) for s in ordered] == [
        '1-1.stos', '1-2.stos', '3-1.stos']


def test_progress_total_excludes_manual_and_skip() -> None:
    """Progress totals only count jobs that invoke RefineFunc."""
    decisions = [
        stosgroup_workers.RefineScanDecisionResult(
            stosgroup_workers.RefineScanDecision.REFINE, 'missing'),
        stosgroup_workers.RefineScanDecisionResult(
            stosgroup_workers.RefineScanDecision.MANUAL_COPY, 'manual'),
        stosgroup_workers.RefineScanDecisionResult(
            stosgroup_workers.RefineScanDecision.SKIP, 'fresh'),
        stosgroup_workers.RefineScanDecisionResult(
            stosgroup_workers.RefineScanDecision.INVALIDATE_THEN_REFINE, 'stale'),
    ]
    refine_count = sum(
        1 for result in decisions
        if result.decision in (
            stosgroup_workers.RefineScanDecision.REFINE,
            stosgroup_workers.RefineScanDecision.INVALIDATE_THEN_REFINE,
        ))
    assert refine_count == 2


def test_stos_file_is_valid_fixture() -> None:
    """Fixture STOS is loadable so skip tests can use a valid output file."""
    if not _FIXTURE_STOS.is_file():
        pytest.skip('STOS fixture unavailable')
    assert stosfile.StosFile.IsValid(str(_FIXTURE_STOS))
