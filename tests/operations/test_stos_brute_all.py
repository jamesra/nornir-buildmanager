"""Tests for pair-level parallel StosBrute: worker, prepare/finish split, StosBruteAll dispatch."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from unittest import mock

import nornir_buildmanager.operations.block as block_mod
import nornir_imageregistration
import nornir_imageregistration.settings
import nornir_pools
import numpy as np
import pytest
from nornir_buildmanager.operations import stos_brute_pairs
from PIL import Image

_REAL_IMAGE = '0017_TEM_Leveled_image__feabinary_Cel64_Mes8_sp4_Mes8.png'
_REAL_MASK = '0017_TEM_Leveled_mask__feabinary_Cel64_Mes8_sp4_Mes8.png'
_SIZE = 290


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
    """A pool-worker style job (NumPy, single thread) for the test image pair."""
    job = stos_brute_pairs.StosBrutePairJob(pair_name=name,
                                            output_stos_path=str(output),
                                            target_image_path=image_pair['target'],
                                            source_image_path=image_pair['source'],
                                            target_mask_path=image_pair['mask'],
                                            source_mask_path=image_pair['mask'],
                                            downsample=1,
                                            method=nornir_imageregistration.settings.SliceToSliceMethod.LogPolar,
                                            test_for_flip=False,
                                            computation_lib='numpy',
                                            single_thread=True)
    return dataclasses.replace(job, **overrides)


@pytest.fixture
def numpy_backend():
    """Run with the NumPy backend and restore the previous one afterwards."""
    previous = nornir_imageregistration.GetActiveComputationLib()
    nornir_imageregistration.SetActiveComputationLib(nornir_imageregistration.ComputationLib.numpy)
    yield
    nornir_imageregistration.SetActiveComputationLib(previous)


def test_worker_round_trip_recovers_rotation(image_pair: dict[str, str], tmp_path: Path, numpy_backend) -> None:
    """The worker writes a .stos whose rigid transform undoes the ~5 degree rotation."""
    output = tmp_path / 'pair.stos'
    result = stos_brute_pairs.run_stos_brute_pair(_job(image_pair, output))
    assert result.output_stos_path == str(output)
    assert output.exists()
    stos = nornir_imageregistration.files.stosfile.StosFile.Load(str(output))
    params = str(stos.Transform).split()
    angle_degrees = np.degrees(float(params[4]))
    assert abs(abs(angle_degrees) - 5) < 1.5, stos.Transform


def test_worker_writes_staging_path(image_pair: dict[str, str], tmp_path: Path, numpy_backend) -> None:
    """With staging_path set the output .stos is not written; the staging file is."""
    output = tmp_path / 'pair.stos'
    staging = tmp_path / 'pair.stos.partial'
    result = stos_brute_pairs.run_stos_brute_pair(_job(image_pair, output, staging_path=str(staging)))
    assert result.output_stos_path == str(staging)
    assert staging.exists()
    assert not output.exists()


def test_parallel_matches_in_process_and_is_order_independent(image_pair: dict[str, str], tmp_path: Path,
                                                              numpy_backend) -> None:
    """Worker-process outputs equal the in-process result byte for byte, whatever ran first.

    The per-pair seed makes each pair's noise independent of earlier pairs in the process;
    the warm-up job below consumes the generator before the measured one in-process.
    """
    reference = tmp_path / 'reference.stos'
    stos_brute_pairs.run_stos_brute_pair(_job(image_pair, tmp_path / 'warmup.stos', name='warmup',
                                              test_for_flip=True))
    stos_brute_pairs.run_stos_brute_pair(_job(image_pair, reference))

    pool = nornir_pools.GetMultithreadingPool('test_stos_brute_all_parity', num_threads=2)
    try:
        tasks = [pool.add_task(f'p{i}', stos_brute_pairs.run_stos_brute_pair,
                               _job(image_pair, tmp_path / f'parallel{i}.stos', name=f'p{i}'))
                 for i in range(3)]
        pids = {task.wait_return().pid for task in tasks}
    finally:
        pool.shutdown()

    expected = reference.read_bytes()
    for i in range(3):
        assert (tmp_path / f'parallel{i}.stos').read_bytes() == expected
    assert os.getpid() not in pids


def test_resolve_workers_auto_numpy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto on NumPy uses all cores but two, capped by MAX_PYTHON_THREADS."""
    monkeypatch.setattr(stos_brute_pairs.nornir_imageregistration, 'UsingCupy', lambda: False)
    monkeypatch.setattr(stos_brute_pairs.os, 'cpu_count', lambda: 16)
    monkeypatch.delenv('MAX_PYTHON_THREADS', raising=False)
    assert stos_brute_pairs.resolve_stos_brute_workers(None) == 14
    assert stos_brute_pairs.resolve_stos_brute_workers(0) == 14
    monkeypatch.setenv('MAX_PYTHON_THREADS', '6')
    assert stos_brute_pairs.resolve_stos_brute_workers(0) == 6
    assert stos_brute_pairs.resolve_stos_brute_workers(12) == 6


def test_resolve_workers_auto_cupy_is_serial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto on CuPy stays serial on the GPU; an explicit count still gets NumPy workers."""
    monkeypatch.setattr(stos_brute_pairs.nornir_imageregistration, 'UsingCupy', lambda: True)
    monkeypatch.delenv('MAX_PYTHON_THREADS', raising=False)
    assert stos_brute_pairs.resolve_stos_brute_workers(0) == 1
    assert stos_brute_pairs.resolve_stos_brute_workers(1) == 1
    assert stos_brute_pairs.resolve_stos_brute_workers(4) == 4


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


def test_workers_one_runs_stos_brute_per_mapping_in_order() -> None:
    """-Workers 1 calls StosBrute per mapping, in map order, with fresh Parameters and NoFlipCheck."""
    mappings = [_mapping(5, [4]), _mapping(5, [6]), _mapping(4, [3])]
    stos_map = mock.Mock()
    stos_map.Mappings = iter(mappings)
    seen: list[tuple[object, dict, bool]] = []

    def _fake_stos_brute(Parameters, mapping_node, *args, **kwargs):
        seen.append((mapping_node, dict(Parameters), kwargs['NoFlipCheck']))
        del Parameters['Downsample']
        yield mapping_node

    with mock.patch.object(block_mod, 'StosBrute', side_effect=_fake_stos_brute), \
            mock.patch.object(block_mod, 'report_stos_work_progress'):
        out = list(block_mod.StosBruteAll({'Downsample': 64, 'NoFlipCheck': True}, stos_map, mock.Mock(),
                                          'TEM', 'Blob', mock.Mock(), mock.Mock(), Workers=1,
                                          OutputGroup='StosBrute64'))
    assert out == mappings
    assert [s[0] for s in seen] == mappings
    assert all(s[1]['Downsample'] == 64 for s in seen)
    assert all(s[2] is True for s in seen)


def _parallel_fixture(tmp_path: Path, jobs: list[stos_brute_pairs.StosBrutePairJob]):
    """Pair tasks, prepared results and a stos group mock for _stos_brute_all_parallel."""
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


def test_parallel_failure_names_pair_and_keeps_completed(image_pair: dict[str, str], tmp_path: Path,
                                                         numpy_backend) -> None:
    """One failing worker: the others are published and saved, then the error names the pair."""
    bad = _job(image_pair, tmp_path / 'bad.stos', name='0007-0006_bad.stos',
               source_image_path=str(tmp_path / 'missing.png'),
               staging_path=str(tmp_path / 'bad.stos.partial'))
    good = [_job(image_pair, tmp_path / f'good{i}.stos', name=f'good{i}',
                 staging_path=str(tmp_path / f'good{i}.stos.partial')) for i in range(2)]
    tasks, prepared, group = _parallel_fixture(tmp_path, [good[0], bad, good[1]])

    finished: list[str] = []

    def _finish(stos_group, prep, control, mapped):
        os.replace(prep.job.staging_path, prep.stos_node.FullPath)
        finished.append(prep.job.pair_name)
        return prep.stos_node

    yielded = []
    with mock.patch.object(block_mod, 'prepare_filter_to_filter_brute_registration', side_effect=prepared), \
            mock.patch.object(block_mod, 'finish_filter_to_filter_brute_registration', side_effect=_finish), \
            mock.patch.object(block_mod, 'report_stos_work_progress'), \
            mock.patch.object(block_mod.nornir_buildmanager.volumemanager.stosgroupnode.StosGroupNode,
                              'GenerateStosFilename', side_effect=lambda c, m: f'{m.FullPath}.stos'):
        gen = block_mod._stos_brute_all_parallel({'UseMasks': True}, tasks, 3, group,
                                                 nornir_imageregistration.settings.SliceToSliceMethod.LogPolar,
                                                 mock.Mock(), 2, OutputGroup='StosBrute64', Type='Brute',
                                                 NoFlipCheck=True)
        with pytest.raises(stos_brute_pairs.StosBrutePairError) as err:
            yielded.extend(gen)  # items saved before the raise stay in the list

    assert '0007-0006_bad.stos' in str(err.value)
    assert err.value.pair_names == ['0007-0006_bad.stos']
    # The first good pair was submitted before the failure, so it finishes. The third pair
    # is submitted alongside (2 workers -> 4 in flight) and also completes.
    assert sorted(finished) == ['good0', 'good1']
    assert (tmp_path / 'good0.stos').exists() and (tmp_path / 'good1.stos').exists()
    assert not (tmp_path / 'bad.stos').exists() and not (tmp_path / 'bad.stos.partial').exists()
    assert yielded and yielded[-1] is group, "completed pairs must be saved before the error propagates"


def test_stos_brute_all_parallel_runs_all_mappings(image_pair: dict[str, str], tmp_path: Path,
                                                   numpy_backend) -> None:
    """Every pair of every mapping is registered once and published in the main process."""
    jobs = [_job(image_pair, tmp_path / f'p{i}.stos', name=f'p{i}', staging_path=str(tmp_path / f'p{i}.partial'))
            for i in range(3)]
    tasks, prepared, group = _parallel_fixture(tmp_path, jobs)
    published: list[str] = []

    def _finish(stos_group, prep, control, mapped):
        os.replace(prep.job.staging_path, prep.stos_node.FullPath)
        published.append(prep.job.pair_name)
        return prep.stos_node

    with mock.patch.object(block_mod, 'prepare_filter_to_filter_brute_registration', side_effect=prepared), \
            mock.patch.object(block_mod, 'finish_filter_to_filter_brute_registration', side_effect=_finish), \
            mock.patch.object(block_mod, 'report_stos_work_progress') as report, \
            mock.patch.object(block_mod.nornir_buildmanager.volumemanager.stosgroupnode.StosGroupNode,
                              'GenerateStosFilename', side_effect=lambda c, m: f'{m.FullPath}.stos'):
        out = list(block_mod._stos_brute_all_parallel({'UseMasks': True}, tasks, 3, group,
                                                      nornir_imageregistration.settings.SliceToSliceMethod.LogPolar,
                                                      mock.Mock(), 2, OutputGroup='StosBrute64'))
    assert published == ['p0', 'p1', 'p2']
    assert out[-1] is group
    reference = (tmp_path / 'p0.stos').read_bytes()
    assert (tmp_path / 'p1.stos').read_bytes() == reference == (tmp_path / 'p2.stos').read_bytes()
    pair_reports = [c.args[1] for c in report.call_args_list if c.args[0] == 'stos_brute:pairs']
    mapping_reports = [c.args[1] for c in report.call_args_list if c.args[0] == 'stos_brute:mappings']
    assert pair_reports == [0, 1, 2, 3]
    assert mapping_reports == [0, 1, 2, 3]
