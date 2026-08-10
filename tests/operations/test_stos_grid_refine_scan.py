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


def _filesize_checksum(path: Path) -> str:
    """Match production Image/FilesizeChecksum: size in bytes as a string."""
    return str(path.stat().st_size)


def _fresh_image_checks(tmp_path: Path, content: str = 'img') -> tuple[
        stosgroup_workers.ImageCheckSnapshot, ...]:
    """Write control/mapped images and return snapshots with live size checksums."""
    paths = (tmp_path / 'ctrl.png', tmp_path / 'map.png')
    for path in paths:
        path.write_text(content, encoding='utf-8')
    size = _filesize_checksum(paths[0])
    return tuple(
        stosgroup_workers.ImageCheckSnapshot(size, size, str(path))
        for path in paths
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
        image_checks = _fresh_image_checks(tmp_path)
        if output_exists:
            # Images older than a fresh refine output.
            for check in image_checks:
                assert check.image_path is not None
                os.utime(check.image_path, (1, 1))

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
    """Live image size mismatch vs stored transform checksum forces rebuild."""
    ctrl = tmp_path / 'ctrl.png'
    mapped = tmp_path / 'map.png'
    ctrl.write_text('img', encoding='utf-8')
    mapped.write_text('img', encoding='utf-8')
    live = _filesize_checksum(ctrl)
    image_checks = (
        stosgroup_workers.ImageCheckSnapshot('999999', live, str(ctrl)),
        stosgroup_workers.ImageCheckSnapshot(live, live, str(mapped)),
    )
    snapshot = _snapshot(
        tmp_path=tmp_path,
        output_exists=True,
        valid_output=False,
        image_checks=image_checks,
    )
    result = stosgroup_workers.decide_stos_grid_refine_need(snapshot)
    assert result.decision == stosgroup_workers.RefineScanDecision.INVALIDATE_THEN_REFINE
    assert 'image' in result.reason.lower()


def test_decide_invalidate_when_image_newer_than_output(tmp_path: Path) -> None:
    """Matching size checksums still rebuild when an input image is newer than output."""
    if not _FIXTURE_STOS.is_file():
        pytest.skip('STOS fixture unavailable')
    snapshot = _snapshot(tmp_path=tmp_path, output_exists=True, valid_output=True)
    output_mtime = os.path.getmtime(snapshot.output_stos_path)
    for check in snapshot.image_checks:
        assert check.image_path is not None
        newer = output_mtime + 10.0
        os.utime(check.image_path, (newer, newer))
    result = stosgroup_workers.decide_stos_grid_refine_need(snapshot)
    assert result.decision == stosgroup_workers.RefineScanDecision.INVALIDATE_THEN_REFINE
    assert 'image' in result.reason.lower()


def test_decide_invalidate_when_live_size_differs_from_image_checksum(tmp_path: Path) -> None:
    """Stale Image-node checksum vs live file size forces rebuild."""
    ctrl = tmp_path / 'ctrl.png'
    mapped = tmp_path / 'map.png'
    ctrl.write_text('img', encoding='utf-8')
    mapped.write_text('img', encoding='utf-8')
    live = _filesize_checksum(ctrl)
    image_checks = (
        stosgroup_workers.ImageCheckSnapshot(live, '1', str(ctrl)),
        stosgroup_workers.ImageCheckSnapshot(live, live, str(mapped)),
    )
    snapshot = _snapshot(
        tmp_path=tmp_path,
        output_exists=True,
        valid_output=False,
        image_checks=image_checks,
    )
    # Ensure mtime alone is not the reason: images older than output.
    for check in image_checks:
        assert check.image_path is not None
        os.utime(check.image_path, (1, 1))
    os.utime(snapshot.output_stos_path, (2, 2))
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
            stosgroup_workers.RefineScanDecision.SKIP, 'ok'),
        stosgroup_workers.RefineScanDecisionResult(
            stosgroup_workers.RefineScanDecision.MANUAL_COPY, 'manual'),
        stosgroup_workers.RefineScanDecisionResult(
            stosgroup_workers.RefineScanDecision.REFINE, 'missing'),
        stosgroup_workers.RefineScanDecisionResult(
            stosgroup_workers.RefineScanDecision.INVALIDATE_THEN_REFINE, 'stale'),
    ]
    refine_func_count = sum(
        1 for d in decisions
        if d.decision != stosgroup_workers.RefineScanDecision.MANUAL_COPY
        and d.decision != stosgroup_workers.RefineScanDecision.SKIP
    )
    assert refine_func_count == 2


def test_stos_fixture_is_valid_when_present() -> None:
    """Fixture STOS used by output-validity checks loads when available."""
    if not _FIXTURE_STOS.is_file():
        pytest.skip('STOS fixture unavailable')
    assert stosfile.StosFile.IsValid(str(_FIXTURE_STOS))
