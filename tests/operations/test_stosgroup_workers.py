"""Tests for parallel STOS group workers and bounded pool dispatch."""

from __future__ import annotations

import shutil
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


@pytest.fixture
def fixture_stos(tmp_path: Path) -> Path:
    """Copy the rigid STOS fixture into a writable temp directory."""
    stos_dir = tmp_path / 'stos'
    stos_dir.mkdir()
    dest = stos_dir / _FIXTURE_STOS.name
    shutil.copyfile(_FIXTURE_STOS, dest)
    return dest


def test_linear_blend_stos_file_writes_output(fixture_stos: Path, tmp_path: Path) -> None:
    """Linear blend copies input, blends, and returns diagnostics."""
    output_path = tmp_path / 'blended.stos'
    result = stosgroup_workers.linear_blend_stos_file(
        str(fixture_stos),
        str(output_path),
        min_blend=0.05,
        travel_limit=100.0,
        ignore_rotation=False,
        reblend_iterations=1,
        reblend_tolerance=0.5,
    )
    assert output_path.is_file()
    assert isinstance(result, stosgroup_workers.LinearBlendResult)
    assert isinstance(result.changed, bool)


def test_scale_stos_file_copy_only_when_unchanged(fixture_stos: Path, tmp_path: Path) -> None:
    """Scaling with identical metadata returns generated=False."""
    loaded = stosfile.StosFile.Load(str(fixture_stos))
    result = stosgroup_workers.scale_stos_file(
        str(fixture_stos),
        str(tmp_path / 'scaled.stos'),
        input_downsample=16,
        output_downsample=16,
        control_image_path=loaded.ControlImageFullPath,
        mapped_image_path=loaded.MappedImageFullPath,
        control_mask_path=loaded.ControlMaskFullPath,
        mapped_mask_path=loaded.MappedMaskFullPath,
        use_masks=loaded.HasMasks,
    )
    assert result.generated is False
    assert not (tmp_path / 'scaled.stos').exists()


def test_scale_stos_file_generates_when_downsample_changes(fixture_stos: Path, tmp_path: Path) -> None:
    """Scaling to a new downsample level writes a new STOS file."""
    loaded = stosfile.StosFile.Load(str(fixture_stos))
    output_path = tmp_path / 'scaled.stos'
    result = stosgroup_workers.scale_stos_file(
        str(fixture_stos),
        str(output_path),
        input_downsample=16,
        output_downsample=8,
        control_image_path=loaded.ControlImageFullPath,
        mapped_image_path=loaded.MappedImageFullPath,
        control_mask_path=loaded.ControlMaskFullPath,
        mapped_mask_path=loaded.MappedMaskFullPath,
        use_masks=loaded.HasMasks,
    )
    assert result.generated is True
    assert output_path.is_file()


def test_resolve_stos_group_workers_caps_cupy_multiprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """CuPy defaults and explicit multiprocess requests are capped to one worker."""
    import nornir_imageregistration

    monkeypatch.setattr(nornir_imageregistration, 'UsingCupy', lambda: True)
    assert stosgroup_workers.resolve_stos_group_workers(None) == 1
    assert stosgroup_workers.resolve_stos_group_workers(32) == 1
    assert stosgroup_workers.resolve_stos_group_workers(1) == 1

    monkeypatch.setattr(nornir_imageregistration, 'UsingCupy', lambda: False)
    assert stosgroup_workers.resolve_stos_group_workers(None) is None
    assert stosgroup_workers.resolve_stos_group_workers(8) == 8


def test_run_bounded_stos_jobs_serial_when_single_job() -> None:
    """A single job runs inline without a pool."""

    def add_one(value: int) -> int:
        return value + 1

    jobs = [
        stosgroup_workers.StosGroupPoolJob(
            name='one',
            func=add_one,
            args=(1,),
            kwargs={},
        )
    ]
    results = list(stosgroup_workers.run_bounded_stos_jobs(None, jobs, max_in_flight=4))
    assert len(results) == 1
    assert results[0][1] == 2


def test_run_bounded_stos_jobs_respects_max_in_flight() -> None:
    """Bounded dispatch does not exceed the configured in-flight cap."""

    class _RecordingTask:
        def __init__(self, pool: '_RecordingPool', func, args, kwargs):
            self._pool = pool
            self._func = func
            self._args = args
            self._kwargs = kwargs

        def wait_return(self):
            try:
                return self._func(*self._args, **self._kwargs)
            finally:
                self._pool.active -= 1

    class _RecordingPool:
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0

        def add_task(self, _name, func, *args, **kwargs):
            self.active += 1
            self.peak = max(self.peak, self.active)
            return _RecordingTask(self, func, args, kwargs)

    jobs = [
        stosgroup_workers.StosGroupPoolJob(
            name=f'job-{index}',
            func=lambda index=index: index,
            args=(),
            kwargs={},
        )
        for index in range(4)
    ]

    pool = _RecordingPool()
    results = list(stosgroup_workers.run_bounded_stos_jobs(pool, jobs, max_in_flight=2))
    assert len(results) == 4
    assert pool.peak <= 2
