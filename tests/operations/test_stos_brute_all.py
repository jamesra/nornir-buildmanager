"""Tests for StosBruteAll: pair job, prepare/finish split, the pair loop, staging and batched saves."""

from __future__ import annotations

import concurrent.futures
import dataclasses
import os
import time
from pathlib import Path
from unittest import mock

import nornir_buildmanager.operations.block as block_mod
import nornir_imageregistration
import nornir_imageregistration.settings
import numpy as np
import pytest
from nornir_buildmanager.operations import stos_brute_pairs
from PIL import Image

_REAL_IMAGE = '0017_TEM_Leveled_image__feabinary_Cel64_Mes8_sp4_Mes8.png'
_REAL_MASK = '0017_TEM_Leveled_mask__feabinary_Cel64_Mes8_sp4_Mes8.png'
_SIZE = 290
_LOG_POLAR = nornir_imageregistration.settings.SliceToSliceMethod.LogPolar


def _target_image() -> tuple[Image.Image, Image.Image]:
    """Real TEM image and mask at _SIZE when TESTINPUTPATH has them, else a smooth texture."""
    root = os.environ.get('TESTINPUTPATH')
    if root:
        image_path = Path(root) / 'Images' / _REAL_IMAGE
        mask_path = Path(root) / 'Images' / _REAL_MASK
        if image_path.exists() and mask_path.exists():
            return (Image.open(image_path).convert('L').resize((_SIZE, _SIZE)),
                    Image.open(mask_path).convert('L').resize((_SIZE, _SIZE), Image.Resampling.NEAREST))
    rng = np.random.default_rng(17)
    noise = rng.random((_SIZE // 10, _SIZE // 10))
    texture = Image.fromarray((noise * 255).astype(np.uint8)).resize((_SIZE, _SIZE), Image.Resampling.BICUBIC)
    return texture, Image.new('L', (_SIZE, _SIZE), 255)


@pytest.fixture(scope='module')
def image_pair(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """Target, mask and a rotated + shifted source image, small enough for ~2 s registrations."""
    folder = tmp_path_factory.mktemp('stos_brute_pair')
    target, mask = _target_image()
    source = target.rotate(5, fillcolor=128).transform(
        target.size, Image.Transform.AFFINE, (1, 0, 8, 0, 1, -5), fillcolor=128)
    paths = {'target': str(folder / 'target.png'), 'source': str(folder / 'source.png'),
             'mask': str(folder / 'mask.png')}
    target.save(paths['target'])
    source.save(paths['source'])
    mask.save(paths['mask'])
    return paths


def _job(image_pair: dict[str, str], output: Path, name: str = 'pair', **overrides) -> stos_brute_pairs.StosBrutePairJob:
    """A job for the test image pair."""
    job = stos_brute_pairs.StosBrutePairJob(pair_name=name,
                                            output_stos_path=str(output),
                                            target_image_path=image_pair['target'],
                                            source_image_path=image_pair['source'],
                                            target_mask_path=image_pair['mask'],
                                            source_mask_path=image_pair['mask'],
                                            downsample=1,
                                            method=_LOG_POLAR,
                                            test_for_flip=False)
    return dataclasses.replace(job, **overrides)


@pytest.fixture
def numpy_backend():
    """Run with the NumPy backend and restore the previous one afterwards."""
    previous = nornir_imageregistration.GetActiveComputationLib()
    nornir_imageregistration.SetActiveComputationLib(nornir_imageregistration.ComputationLib.numpy)
    yield
    nornir_imageregistration.SetActiveComputationLib(previous)


def test_job_round_trip_recovers_rotation(image_pair: dict[str, str], tmp_path: Path, numpy_backend) -> None:
    """run_stos_brute_pair writes a .stos whose rigid transform undoes the ~5 degree rotation."""
    output = tmp_path / 'pair.stos'
    result = stos_brute_pairs.run_stos_brute_pair(_job(image_pair, output))
    assert result.output_stos_path == str(output)
    assert result.pid == os.getpid()
    assert output.exists()
    stos = nornir_imageregistration.files.stosfile.StosFile.Load(str(output))
    params = str(stos.Transform).split()
    angle_degrees = np.degrees(float(params[4]))
    assert abs(abs(angle_degrees) - 5) < 1.5, stos.Transform
    # The result carries the registration for the per-pair log line.
    assert result.angle is not None and abs(abs(result.angle) - 5) < 1.5
    assert result.weight is not None and result.weight > 0
    assert result.scale is not None and result.offset_yx is not None
    assert result.pair_name in f"StosBrute {result.pair_name}" and 'weight' in result.describe()


def test_job_writes_staging_path(image_pair: dict[str, str], tmp_path: Path, numpy_backend) -> None:
    """With staging_path set the output .stos is not written; the staging file is."""
    output = tmp_path / 'pair.stos'
    staging = tmp_path / 'pair.stos.partial'
    result = stos_brute_pairs.run_stos_brute_pair(_job(image_pair, output, staging_path=str(staging)))
    assert result.output_stos_path == str(staging)
    assert staging.exists()
    assert not output.exists()


def test_job_reseeds_before_registering(tmp_path: Path) -> None:
    """Each pair reseeds the noise generator with its job's seed before registering."""
    calls: list[str] = []
    with mock.patch.object(stos_brute_pairs.nornir_imageregistration, 'seed_random_data',
                           side_effect=lambda seed: calls.append(f'seed {seed}')), \
            mock.patch.object(stos_brute_pairs.stos_brute, 'SliceToSliceRigidRegistration',
                              side_effect=lambda **kw: calls.append('register') or mock.Mock()):
        stos_brute_pairs.run_stos_brute_pair(_job({'target': 't', 'source': 's', 'mask': 'm'},
                                                  tmp_path / 'p.stos', random_seed=123))
    assert calls == ['seed 123', 'register']


def test_in_process_executor_runs_on_submit_and_captures_errors() -> None:
    """InProcessPairExecutor computes during submit; an Exception lands in the future."""
    executor = stos_brute_pairs.InProcessPairExecutor()
    assert executor.max_in_flight == 1
    done = mock.Mock()
    with mock.patch.object(stos_brute_pairs, 'run_stos_brute_pair', return_value=done) as run:
        future = executor.submit(mock.sentinel.job)
        run.assert_called_once_with(mock.sentinel.job)
    assert future.done() and future.result() is done

    with mock.patch.object(stos_brute_pairs, 'run_stos_brute_pair', side_effect=OSError('boom')):
        future = executor.submit(mock.sentinel.job)
    assert isinstance(future.exception(), OSError)

    with mock.patch.object(stos_brute_pairs, 'run_stos_brute_pair', side_effect=KeyboardInterrupt), \
            pytest.raises(KeyboardInterrupt):
        executor.submit(mock.sentinel.job)


def test_filter_to_filter_composes_prepare_compute_finish(tmp_path: Path) -> None:
    """Serial FilterToFilterBruteRegistration = prepare, run the job here, finish."""
    node = mock.Mock()
    job = mock.Mock()
    compute = block_mod.BrutePairPrepared(stos_node=node, action=block_mod.BrutePairAction.COMPUTE, job=job)
    with mock.patch.object(block_mod, 'prepare_filter_to_filter_brute_registration', return_value=compute), \
            mock.patch.object(block_mod.stos_brute_pairs, 'run_stos_brute_pair') as run, \
            mock.patch.object(block_mod, 'finish_filter_to_filter_brute_registration', return_value=node) as finish:
        result = block_mod.FilterToFilterBruteRegistration(mock.Mock(), mock.Mock(), mock.Mock(), 'Brute',
                                                           'x.stos', False)
    assert result is node
    run.assert_called_once_with(job)
    finish.assert_called_once()

    current = block_mod.BrutePairPrepared(stos_node=node, action=block_mod.BrutePairAction.CURRENT)
    for prepared in (None, current):
        with mock.patch.object(block_mod, 'prepare_filter_to_filter_brute_registration', return_value=prepared), \
                mock.patch.object(block_mod.stos_brute_pairs, 'run_stos_brute_pair') as run:
            assert block_mod.FilterToFilterBruteRegistration(mock.Mock(), mock.Mock(), mock.Mock(), 'Brute',
                                                             'x.stos', False) is None
        run.assert_not_called()


def test_finish_moves_staged_file_and_records_checksums(tmp_path: Path) -> None:
    """finish renames the staged .stos over the output, then resets checksums."""
    output = tmp_path / 'pair.stos'
    staging = tmp_path / 'pair.stos.partial'
    staging.write_text('staged')
    node = mock.Mock()
    node.FullPath = str(output)
    job = mock.Mock()
    job.staging_path = str(staging)
    group = mock.Mock()
    control, mapped = mock.Mock(), mock.Mock()
    prepared = block_mod.BrutePairPrepared(stos_node=node, action=block_mod.BrutePairAction.COMPUTE, job=job)
    assert block_mod.finish_filter_to_filter_brute_registration(group, prepared, control, mapped) is node
    assert output.read_text() == 'staged'
    assert not staging.exists()
    node.ResetChecksum.assert_called_once()
    group.AddChecksumsToStos.assert_called_once_with(node, control, mapped)


def _mapping(control: int, mapped: list[int]) -> mock.Mock:
    mapping = mock.Mock()
    mapping.Control = control
    mapping.Mapped = mapped
    return mapping


def test_stos_brute_all_runs_pairs_in_process_with_no_flip_check() -> None:
    """StosBruteAll enumerates every mapping's pairs and runs them on an in-process executor.

    NoFlipCheck arrives as a <Parameters> entry (AlignSections) and must reach the loop.
    """
    mappings = [_mapping(5, [4]), _mapping(5, [6]), _mapping(4, [3])]
    stos_map = mock.Mock()
    stos_map.Mappings = iter(mappings)
    block_node = mock.Mock()
    group = mock.Mock()
    block_node.GetOrCreateStosGroup.return_value = (False, group)
    seen: dict[str, object] = {}
    task = mock.Mock(needs_work=True)

    def _fake_pairs(Parameters, pair_tasks, mapping_count, stos_group_node, Method, Logger, executor, **kwargs):
        seen.update(tasks=pair_tasks, mapping_count=mapping_count, executor=executor,
                    no_flip=kwargs['NoFlipCheck'], group=stos_group_node)
        yield stos_group_node

    with mock.patch.object(block_mod, '_enumerate_brute_pair_tasks', return_value=[task]) as enumerate_tasks, \
            mock.patch.object(block_mod, '_stos_brute_pairs', side_effect=_fake_pairs), \
            mock.patch.object(block_mod.os, 'makedirs'):
        out = list(block_mod.StosBruteAll({'Downsample': 64, 'NoFlipCheck': True}, stos_map, block_node,
                                          'TEM', 'Blob', _LOG_POLAR, mock.Mock(), OutputGroup='StosBrute64'))
    assert out == [group]
    block_node.GetOrCreateStosGroup.assert_called_once_with('StosBrute64', downsample=64)
    assert enumerate_tasks.call_args.args[0] == mappings
    assert seen['tasks'] == [task] and seen['mapping_count'] == 3 and seen['group'] is group
    assert isinstance(seen['executor'], stos_brute_pairs.InProcessPairExecutor)
    assert seen['no_flip'] is True


def _loop_fixture(jobs: list[stos_brute_pairs.StosBrutePairJob]):
    """Pair tasks, prepared results and a stos group mock for _stos_brute_pairs."""
    tasks = [block_mod._BrutePairTask(mapping_index=i, mapped_section=i, mapped_filter=mock.Mock(FullPath=f'm{i}'),
                                      control_filter=mock.Mock(FullPath=f'c{i}'), needs_work=True)
             for i in range(len(jobs))]
    prepared = []
    for job in jobs:
        node = mock.Mock()
        node.FullPath = job.output_stos_path
        prepared.append(block_mod.BrutePairPrepared(stos_node=node, action=block_mod.BrutePairAction.COMPUTE,
                                                    job=job))
    group = mock.Mock()
    group.Name = 'StosBrute64'
    group.GetOrCreateSectionMapping.return_value = (False, mock.Mock())
    return tasks, prepared, group


def _finish_recording(record: list[str]):
    """A finish stand-in that renames the staged file and records the pair."""

    def _finish(stos_group, prep, control, mapped):
        os.replace(prep.job.staging_path, prep.stos_node.FullPath)
        record.append(prep.job.pair_name)
        return prep.stos_node

    return _finish


def _patched_loop(prepared, finish, **extra):
    """Patch prepare/finish/progress/filenames around a _stos_brute_pairs run."""
    stack = [mock.patch.object(block_mod, 'prepare_filter_to_filter_brute_registration', side_effect=prepared),
             mock.patch.object(block_mod, 'finish_filter_to_filter_brute_registration', side_effect=finish),
             mock.patch.object(block_mod.nornir_buildmanager.volumemanager.stosgroupnode.StosGroupNode,
                               'GenerateStosFilename', side_effect=lambda c, m: f'{m.FullPath}.stos')]
    stack.append(mock.patch.object(block_mod, 'report_stos_work_progress', **extra))
    return stack


def test_loop_registers_every_pair_in_process(image_pair: dict[str, str], tmp_path: Path, numpy_backend) -> None:
    """Every pair is registered in this process, staged, published in order and saved."""
    jobs = [_job(image_pair, tmp_path / f'p{i}.stos', name=f'p{i}', staging_path=str(tmp_path / f'p{i}.partial'))
            for i in range(3)]
    tasks, prepared, group = _loop_fixture(jobs)
    published: list[str] = []
    patches = _patched_loop(prepared, _finish_recording(published))
    with patches[0] as prepare, patches[1], patches[2], patches[3] as report:
        out = list(block_mod._stos_brute_pairs({'UseMasks': True}, tasks, 3, group, _LOG_POLAR, mock.Mock(),
                                               stos_brute_pairs.InProcessPairExecutor(),
                                               save_interval_seconds=1e9,  # 3 real pairs can pass 15 s under load
                                               OutputGroup='StosBrute64', NoFlipCheck=True))
    assert published == ['p0', 'p1', 'p2']
    assert out == [group], "one save at the end when the save interval has not elapsed"
    assert all(c.kwargs['test_for_flip'] is False for c in prepare.call_args_list)
    assert all(c.kwargs['staging_suffix'] == '.partial' for c in prepare.call_args_list)
    # Per-pair reseeding: identical inputs give byte-identical outputs whatever ran before.
    reference = (tmp_path / 'p0.stos').read_bytes()
    assert (tmp_path / 'p1.stos').read_bytes() == reference == (tmp_path / 'p2.stos').read_bytes()
    assert not any(tmp_path.glob('*.partial'))
    pair_reports = [c.args[1] for c in report.call_args_list if c.args[0] == 'stos_brute:pairs']
    mapping_reports = [c.args[1] for c in report.call_args_list if c.args[0] == 'stos_brute:mappings']
    assert pair_reports == [0, 1, 2, 3]
    assert mapping_reports == [0, 1, 2, 3]


class _RecordingExecutor:
    """In-process stand-in that writes a fake staged .stos and records the call order."""

    max_in_flight: int = 1
    description: str = 'recording'

    def __init__(self, events: list[str], fail: set[str] | None = None) -> None:
        self.events = events
        self.fail = fail or set()
        self.shutdowns: list[bool] = []

    def submit(self, job: stos_brute_pairs.StosBrutePairJob) -> concurrent.futures.Future:
        """Write the staging file (or fail) immediately."""
        self.events.append(f'compute {job.pair_name}')
        future: concurrent.futures.Future = concurrent.futures.Future()
        staging = job.staging_path
        assert staging is not None
        if job.pair_name in self.fail:
            Path(staging).write_text('half written')
            future.set_exception(RuntimeError(f'cannot register {job.pair_name}'))
        else:
            Path(staging).write_text(job.pair_name)
            future.set_result(stos_brute_pairs.StosBrutePairResult(job.pair_name, staging, 0.0, os.getpid()))
        return future

    def shutdown(self, cancel_pending: bool = False) -> None:
        """Record the shutdown."""
        self.shutdowns.append(cancel_pending)


def _fake_jobs(tmp_path: Path, names: list[str]) -> list[stos_brute_pairs.StosBrutePairJob]:
    return [_job({'target': 't', 'source': 's', 'mask': 'm'}, tmp_path / f'{n}.stos', name=n,
                 staging_path=str(tmp_path / f'{n}.stos.partial')) for n in names]


def test_loop_order_is_prepare_compute_finish_per_pair(tmp_path: Path) -> None:
    """With one job in flight each pair is prepared, computed and published before the next."""
    events: list[str] = []
    tasks, prepared, group = _loop_fixture(_fake_jobs(tmp_path, ['a', 'b']))
    prepared_iter = iter(prepared)

    def _prepare(**kwargs):
        prep = next(prepared_iter)
        events.append(f'prepare {prep.job.pair_name}')
        return prep

    finished: list[str] = []

    def _finish(stos_group, prep, control, mapped):
        events.append(f'finish {prep.job.pair_name}')
        return _finish_recording(finished)(stos_group, prep, control, mapped)

    executor = _RecordingExecutor(events)
    patches = _patched_loop(_prepare, _finish)
    with patches[0], patches[1], patches[2], patches[3]:
        list(block_mod._stos_brute_pairs({}, tasks, 2, group, _LOG_POLAR, mock.Mock(), executor))
    assert events == ['prepare a', 'compute a', 'finish a', 'prepare b', 'compute b', 'finish b']
    assert executor.shutdowns == [False]
    assert (tmp_path / 'a.stos').read_text() == 'a'


def test_loop_saves_in_batches(tmp_path: Path) -> None:
    """The StosGroup is yielded when the save interval has elapsed and at the end, not per pair.

    The fake clock advances 10 s per reading: start 0; pair a 10 (no save); b 20 (save,
    reset at 30); c 40 (no); d 50 (save, reset at 60); nothing unsaved at the end.
    """
    names = ['a', 'b', 'c', 'd']
    for interval, expected_saves in ((1e9, 1), (15.0, 2)):
        folder = tmp_path / str(int(min(interval, 99)))
        folder.mkdir()
        tasks, prepared, group = _loop_fixture(_fake_jobs(folder, names))
        published: list[str] = []
        patches = _patched_loop(prepared, _finish_recording(published))
        ticks = iter(range(0, 1000, 10))
        with patches[0], patches[1], patches[2], patches[3], \
                mock.patch.object(block_mod.time, 'monotonic', side_effect=lambda _ticks=ticks: float(next(_ticks))):
            out = list(block_mod._stos_brute_pairs({}, tasks, 4, group, _LOG_POLAR, mock.Mock(),
                                                   _RecordingExecutor([]), save_interval_seconds=interval))
        assert published == names
        assert all(item is group for item in out)
        assert len(out) == expected_saves, (interval, len(out))


def test_loop_failure_names_pair_and_keeps_completed(tmp_path: Path) -> None:
    """A failing pair stops new submissions; completed pairs are saved, then the error names it."""
    events: list[str] = []
    tasks, prepared, group = _loop_fixture(_fake_jobs(tmp_path, ['good0', '0007-0006_bad.stos', 'good1']))
    finished: list[str] = []
    executor = _RecordingExecutor(events, fail={'0007-0006_bad.stos'})
    yielded: list[object] = []
    patches = _patched_loop(prepared, _finish_recording(finished))
    with patches[0], patches[1], patches[2], patches[3]:
        gen = block_mod._stos_brute_pairs({}, tasks, 3, group, _LOG_POLAR, mock.Mock(), executor)
        with pytest.raises(stos_brute_pairs.StosBrutePairError) as err:
            yielded.extend(gen)  # items saved before the raise stay in the list

    assert '0007-0006_bad.stos' in str(err.value)
    assert err.value.pair_names == ['0007-0006_bad.stos']
    assert isinstance(err.value.__cause__, RuntimeError)
    assert finished == ['good0']
    assert events == ['compute good0', 'compute 0007-0006_bad.stos'], "no submissions after a failure"
    assert not (tmp_path / '0007-0006_bad.stos.partial').exists(), "a failed pair's staging file is removed"
    assert not (tmp_path / '0007-0006_bad.stos').exists()
    assert yielded and yielded[-1] is group, "completed pairs must be saved before the error propagates"


def test_loop_interrupt_discards_staging_and_saves(tmp_path: Path) -> None:
    """An interrupt during compute removes that pair's staging file, saves, cancels the executor."""
    tasks, prepared, group = _loop_fixture(_fake_jobs(tmp_path, ['a', 'b']))
    finished: list[str] = []

    class _Interrupting(_RecordingExecutor):
        def submit(self, job):
            if job.pair_name == 'b':
                Path(str(job.staging_path)).write_text('partial')
                raise KeyboardInterrupt
            return super().submit(job)

    executor = _Interrupting([])
    yielded: list[object] = []
    patches = _patched_loop(prepared, _finish_recording(finished))
    with patches[0], patches[1], patches[2], patches[3]:
        gen = block_mod._stos_brute_pairs({}, tasks, 2, group, _LOG_POLAR, mock.Mock(), executor)
        with pytest.raises(KeyboardInterrupt):
            yielded.extend(gen)
    assert finished == ['a']
    assert yielded == [group]
    assert executor.shutdowns == [True]
    assert not (tmp_path / 'b.stos.partial').exists()


def test_parse_gpu_workers() -> None:
    """Unset / empty pipeline attribute means one pair at a time in process."""
    assert block_mod._parse_gpu_workers(None) == 1
    assert block_mod._parse_gpu_workers('') == 1
    assert block_mod._parse_gpu_workers('4') == 4


def test_create_executor_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """GPU workers only for N > 1 on CuPy with more than one pair; capped at the pair count."""
    logger = mock.Mock()
    created: list[int] = []
    monkeypatch.setattr(block_mod.stos_brute_pairs, 'GpuWorkerPairExecutor',
                        lambda workers: created.append(workers) or mock.sentinel.gpu)

    monkeypatch.setattr(block_mod.nornir_imageregistration, 'UsingCupy', lambda: True)
    assert isinstance(block_mod._create_stos_brute_executor(1, 10, logger), stos_brute_pairs.InProcessPairExecutor)
    assert isinstance(block_mod._create_stos_brute_executor(4, 1, logger), stos_brute_pairs.InProcessPairExecutor)
    assert block_mod._create_stos_brute_executor(4, 10, logger) is mock.sentinel.gpu
    assert block_mod._create_stos_brute_executor(8, 3, logger) is mock.sentinel.gpu
    assert created == [4, 3]

    monkeypatch.setattr(block_mod.nornir_imageregistration, 'UsingCupy', lambda: False)
    assert isinstance(block_mod._create_stos_brute_executor(4, 10, logger), stos_brute_pairs.InProcessPairExecutor)
    logger.warning.assert_called_once()
    assert created == [4, 3]


def test_stos_brute_all_passes_gpu_workers_to_executor_choice() -> None:
    """The pipeline's GpuWorkers attribute and the count of pairs needing work pick the executor."""
    stos_map = mock.Mock()
    stos_map.Mappings = iter([_mapping(5, [4])])
    block_node = mock.Mock()
    block_node.GetOrCreateStosGroup.return_value = (False, mock.Mock())
    tasks = [mock.Mock(needs_work=True), mock.Mock(needs_work=False), mock.Mock(needs_work=True)]
    with mock.patch.object(block_mod, '_enumerate_brute_pair_tasks', return_value=tasks), \
            mock.patch.object(block_mod, '_create_stos_brute_executor', return_value=mock.sentinel.executor) as create, \
            mock.patch.object(block_mod, '_stos_brute_pairs', return_value=iter(())) as loop, \
            mock.patch.object(block_mod.os, 'makedirs'):
        list(block_mod.StosBruteAll({'Downsample': 16}, stos_map, block_node, 'TEM', 'Blob', _LOG_POLAR,
                                    mock.Mock(), GpuWorkers='3', OutputGroup='StosBrute16'))
    assert create.call_args.args[:2] == (3, 2)
    assert loop.call_args.args[6] is mock.sentinel.executor


def test_gpu_worker_entry_refuses_numpy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A GPU worker that ended up on NumPy fails the pair instead of silently running on the CPU."""
    monkeypatch.setattr(stos_brute_pairs.nornir_imageregistration, 'UsingCupy', lambda: False)
    with mock.patch.object(stos_brute_pairs, 'run_stos_brute_pair') as run, \
            pytest.raises(RuntimeError, match='not on CuPy'):
        stos_brute_pairs.run_stos_brute_pair_on_gpu_worker(_fake_jobs(tmp_path, ['x'])[0])
    run.assert_not_called()


@pytest.fixture
def cupy_backend():
    """Run with the CuPy backend (skip without a usable GPU) and restore the previous one."""
    if not nornir_imageregistration.HasCupy():
        pytest.skip('CuPy not installed')
    previous = nornir_imageregistration.GetActiveComputationLib()
    nornir_imageregistration.SetActiveComputationLib(nornir_imageregistration.ComputationLib.cupy)
    if not nornir_imageregistration.UsingCupy():
        nornir_imageregistration.SetActiveComputationLib(previous)
        pytest.skip('CuPy runtime probe failed (no usable GPU)')
    yield
    nornir_imageregistration.SetActiveComputationLib(previous)


@pytest.mark.gpu
def test_gpu_workers_match_in_process_cupy(image_pair: dict[str, str], tmp_path: Path, cupy_backend) -> None:
    """Spawned CuPy workers write the same .stos bytes as an in-process CuPy registration."""
    reference = tmp_path / 'reference.stos'
    stos_brute_pairs.InProcessPairExecutor().submit(_job(image_pair, reference)).result()

    executor = stos_brute_pairs.GpuWorkerPairExecutor(2)
    try:
        futures = [executor.submit(_job(image_pair, tmp_path / f'w{i}.stos', name=f'w{i}')) for i in range(3)]
        results = [f.result(timeout=600) for f in futures]
    finally:
        executor.shutdown()

    assert all(r.pid != os.getpid() for r in results)
    assert all(r.gpu_pool_bytes is not None for r in results), 'workers must have run on CuPy'
    expected = reference.read_bytes()
    for i in range(3):
        assert (tmp_path / f'w{i}.stos').read_bytes() == expected


@pytest.mark.gpu
def test_gpu_worker_executor_cancel_stops_workers(image_pair: dict[str, str], tmp_path: Path, cupy_backend) -> None:
    """shutdown(cancel_pending=True) returns without waiting for queued registrations."""
    executor = stos_brute_pairs.GpuWorkerPairExecutor(1)
    futures = [executor.submit(_job(image_pair, tmp_path / f'c{i}.stos', name=f'c{i}')) for i in range(4)]
    started = time.perf_counter()
    executor.shutdown(cancel_pending=True)
    assert time.perf_counter() - started < 60
    assert not all(f.done() and not f.cancelled() and f.exception() is None for f in futures)
