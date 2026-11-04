"""
MosaicToVolume composes one section per pool task, with a bounded number in flight.

The main process used to parse each section's .stos and grid .mosaic itself and farm out
only the per-tile compositions, one section at a time. On srv that kept 4.7% of 128 cores
busy and took about 2 h for 1600 sections (RC3, 2026-11-04). Each worker now builds a
whole section, and at most one section per worker is in flight, because each task holds
a section's mosaic and its slice-to-volume transform.

A section that fails no longer stops the others: its error is raised once every other
section has been built.
"""

from __future__ import annotations

import logging
from unittest import mock

import pytest
from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations import block as block_mod
from nornir_buildmanager.operations.block import (
    _compose_mosaic_to_volume,
    _MosaicToVolumeJob,
    _MosaicToVolumeResult,
)


class _CountingPool:
    """Runs a task when it is collected and tracks how many are submitted but not collected."""

    def __init__(self, max_workers: int, results: dict[str, _MosaicToVolumeResult | BaseException]):
        self.max_workers = max_workers
        self.results = results
        self.live = 0
        self.peak = 0
        self.submitted: list[str] = []

    def add_task(self, name, func, *args, **kwargs):
        pool = self
        pool.submitted.append(name)
        pool.live += 1
        pool.peak = max(pool.peak, pool.live)

        class _Task:
            def wait_return(self):
                pool.live -= 1
                result = pool.results.get(name, _MosaicToVolumeResult(4))
                if isinstance(result, BaseException):
                    raise result
                return result

        return _Task()


def _job(number: int) -> _MosaicToVolumeJob:
    channel = mock.Mock(name=f'channel{number}')
    transform = mock.Mock(FullPath=f'{number:04d}/Grid.mosaic', Parent=channel, Checksum=f'grid{number}')
    output = mock.Mock(FullPath=f'{number:04d}/ChannelToVolume_Untranslated.mosaic')
    return _MosaicToVolumeJob(mock.Mock(name=f'stos{number}'), transform, output)


def _run(jobs: list[_MosaicToVolumeJob], pool: _CountingPool):
    """Drive BuildMosaicToVolumeTransforms with one channel per job; return what it yielded."""
    channels = [job.transform_node.Parent for job in jobs]
    for channel, job in zip(channels, jobs):
        channel.GetTransform.return_value = job.transform_node
    jobs_by_transform = {id(job.transform_node): [job] for job in jobs}

    def channel_jobs(stos_map, stos_group, transform_node, *args, **kwargs):
        return jobs_by_transform[id(transform_node)]

    block = mock.Mock()
    with mock.patch.object(block_mod.nornir_buildmanager.volumemanager, 'SearchCollection', return_value=channels), \
            mock.patch.object(block_mod, 'BuildChannelMosaicToVolumeTransform', side_effect=channel_jobs), \
            mock.patch.object(block_mod, '_mosaic_to_volume_task_args', return_value=('s', 1.0, 'm', 'o')), \
            mock.patch.object(block_mod.nornir_pools, 'GetLocalMachinePool', return_value=pool), \
            mock.patch.object(block_mod, '__MoveMosaicsToZeroOrigin') as move, \
            mock.patch.object(block_mod, 'report_iterate'):
        yielded = []
        try:
            for node in block_mod.BuildMosaicToVolumeTransforms(
                    mock.Mock(), mock.Mock(), block, 'TEM', 'Grid', 'ChannelToVolume', logging.getLogger(__name__)):
                yielded.append(node)  # noqa: PERF402 - keeps what was yielded before a raise
        except NornirUserException as e:
            return yielded, e, move
    return yielded, None, move


@pytest.mark.parametrize('workers', [1, 4, 16])
def test_sections_in_flight_never_exceed_the_workers(workers):
    pool = _CountingPool(workers, {})
    jobs = [_job(n) for n in range(1, 41)]

    yielded, error, _ = _run(jobs, pool)

    assert error is None
    assert pool.peak <= workers
    assert len(pool.submitted) == 40
    assert [node for node in yielded if node in [j.transform_node.Parent for j in jobs]] == \
        [j.transform_node.Parent for j in jobs]


def test_each_section_is_one_task():
    pool = _CountingPool(8, {})
    jobs = [_job(n) for n in range(1, 6)]

    _run(jobs, pool)

    assert pool.submitted == [job.transform_node.FullPath for job in jobs]


def test_finished_sections_record_their_inputs():
    pool = _CountingPool(4, {})
    job = _job(1)

    _run([job], pool)

    job.output_transform_node.ResetChecksum.assert_called_once()
    job.output_transform_node.SetTransform.assert_called_once_with(job.stos_transform_node)
    assert job.output_transform_node.InputMosaicTransformChecksum == 'grid1'


def test_a_failed_section_does_not_stop_the_others():
    jobs = [_job(n) for n in range(1, 6)]
    pool = _CountingPool(2, {
        jobs[1].transform_node.FullPath: _MosaicToVolumeResult(0, '1 of 4 tiles could not be composed'),
        jobs[3].transform_node.FullPath: RuntimeError('worker died'),
    })

    yielded, error, move = _run(jobs, pool)

    assert error is not None
    assert '2 of 5 section mosaics' in str(error)
    assert '0002/ChannelToVolume_Untranslated.mosaic' in str(error)
    assert '1 of 4 tiles could not be composed' in str(error)
    assert 'worker died' in str(error)
    # Every section was built or reported, and every channel saved.
    assert len(pool.submitted) == 5
    assert yielded == [job.transform_node.Parent for job in jobs]
    for good in (jobs[0], jobs[2], jobs[4]):
        good.output_transform_node.ResetChecksum.assert_called_once()
    jobs[1].output_transform_node.ResetChecksum.assert_not_called()
    # As before, nothing is moved to the volume origin while a section is missing.
    move.assert_not_called()


def test_worker_reports_a_nonfinite_stos_as_text():
    """NornirUserException does not survive pickling, so the worker returns the message."""
    stos = mock.Mock(Transform='GridTransform_double_2_2 vp 2 nan 0 fp 2 0 0')
    with mock.patch.object(block_mod.stosfile.StosFile, 'Load', return_value=stos):
        result = _compose_mosaic_to_volume('3-2.stos', 1.0, 'Grid.mosaic', 'out.mosaic')

    assert result.tiles_written == 0
    assert result.user_error is not None and 'NaN/Inf' in result.user_error and '3-2.stos' in result.user_error


def test_worker_reports_failed_tiles_as_text():
    stos = mock.Mock(Transform='GridTransform_double_2_2 vp 2 0 0 fp 2 0 0')
    stos.ChangeTransformPixelSpacing.return_value = stos
    mosaic_transform = mock.Mock(ImageToTransform={'a.png': 'section a', 'b.png': 'section b'})
    mosaic_transform.IsOriginAtZero.return_value = True

    def compose(stov, tile):
        if tile == 'section b':
            raise ValueError('compose failed')
        return 'volume a'

    with mock.patch.object(block_mod.stosfile.StosFile, 'Load', return_value=stos), \
            mock.patch.object(block_mod.factory, 'LoadTransform'), \
            mock.patch.object(block_mod.assemble, 'transform_for_host_assembly'), \
            mock.patch.object(block_mod.assemble, 'precompute_forward_fallback'), \
            mock.patch.object(block_mod.mosaic.Mosaic, 'LoadFromMosaicFile', return_value=mosaic_transform), \
            mock.patch.object(block_mod.nornir_imageregistration.transforms, 'AddTransforms', side_effect=compose):
        result = _compose_mosaic_to_volume('3-2.stos', 1.0, 'Grid.mosaic', 'out.mosaic')

    assert result.tiles_written == 0
    assert result.user_error is not None and '1 of 2 tiles' in result.user_error and 'b.png' in result.user_error
    mosaic_transform.ToMosaicFile.assert_not_called()
