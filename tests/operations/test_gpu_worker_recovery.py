"""GpuWorkerPairExecutor replaces a worker whose CUDA context is poisoned, and caps worker pools.

On RC2 (2026-11-02) one worker hit cudaErrorIllegalAddress, a sticky error that fails
every later CUDA call in that process, and the executor kept feeding it: 259 pairs failed
instantly. These tests run the executor's lanes on threads with a scripted worker
function, so they need no GPU.
"""

from __future__ import annotations

import concurrent.futures
import threading
from unittest import mock

import pytest
from nornir_buildmanager.operations import stos_brute_pairs


def _job(name: str) -> stos_brute_pairs.StosBrutePairJob:
    return stos_brute_pairs.StosBrutePairJob(pair_name=name, output_stos_path=f'{name}.stos',
                                             target_image_path='t.png', source_image_path='s.png',
                                             target_mask_path=None, source_mask_path=None, downsample=64,
                                             method=None)  # type: ignore[arg-type]


def _result(job: stos_brute_pairs.StosBrutePairJob) -> stos_brute_pairs.StosBrutePairResult:
    return stos_brute_pairs.StosBrutePairResult(pair_name=job.pair_name, output_stos_path=job.output_stos_path,
                                                elapsed_seconds=0.0, pid=threading.get_ident())


class _ScriptedWorkers:
    """Stands in for the worker entry point; each lane (thread pool) is one 'process'."""

    def __init__(self, poison_on: dict[str, int]) -> None:
        #: pair name -> how many times running it poisons the worker
        self.poison_on = dict(poison_on)
        self.poisoned_threads: set[int] = set()
        self.lanes_created = 0
        self.lock = threading.Lock()

    def lane(self) -> concurrent.futures.ThreadPoolExecutor:
        self.lanes_created += 1
        return concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def run(self, job: stos_brute_pairs.StosBrutePairJob) -> stos_brute_pairs.StosBrutePairResult:
        me = threading.get_ident()
        with self.lock:
            if me in self.poisoned_threads:
                raise stos_brute_pairs.GpuWorkerPoisonedError('refused', job.pair_name, caused_here=False)
            if self.poison_on.get(job.pair_name, 0) > 0:
                self.poison_on[job.pair_name] -= 1
                self.poisoned_threads.add(me)
                raise stos_brute_pairs.GpuWorkerPoisonedError('cudaErrorIllegalAddress', job.pair_name,
                                                              caused_here=True)
        if job.pair_name == 'ordinary-failure':
            raise ValueError('no tissue')
        return _result(job)


def _run(workers: _ScriptedWorkers, names: list[str], lanes: int = 2):
    with mock.patch.object(stos_brute_pairs, 'run_stos_brute_pair_on_gpu_worker', side_effect=workers.run):
        executor = stos_brute_pairs.GpuWorkerPairExecutor(lanes, pool_limit=2 ** 30, lane_factory=workers.lane)
        futures = {name: executor.submit(_job(name)) for name in names}
        concurrent.futures.wait(futures.values(), timeout=30)
        executor.shutdown()
    return futures


def test_poisoning_pair_is_retried_on_a_new_worker_and_queued_pairs_still_run() -> None:
    workers = _ScriptedWorkers({'bad': 1})
    futures = _run(workers, ['bad', 'a', 'b', 'c', 'd', 'e'])

    assert all(f.done() and f.exception() is None for f in futures.values())
    assert {f.result().pair_name for f in futures.values()} == set(futures)
    assert workers.lanes_created == 3, 'only the poisoned lane is replaced'


def test_pair_that_poisons_every_worker_fails_after_one_retry() -> None:
    workers = _ScriptedWorkers({'bad': 5})
    futures = _run(workers, ['bad', 'a', 'b'])

    error = futures['bad'].exception()
    assert isinstance(error, stos_brute_pairs.GpuWorkerPoisonedError) and error.caused_here
    assert workers.poison_on['bad'] == 5 - 1 - stos_brute_pairs.GPU_WORKER_POISON_RETRIES
    assert futures['a'].result().pair_name == 'a' and futures['b'].result().pair_name == 'b'


def test_ordinary_failure_is_reported_without_replacing_the_worker() -> None:
    workers = _ScriptedWorkers({})
    futures = _run(workers, ['ordinary-failure', 'a'])

    assert isinstance(futures['ordinary-failure'].exception(), ValueError)
    assert futures['a'].result().pair_name == 'a'
    assert workers.lanes_created == 2


def test_pool_limit_is_an_equal_share_less_a_context() -> None:
    gib = 2 ** 30
    free = 20 * gib
    expected = (free - stos_brute_pairs.GPU_WORKER_RESERVE_BYTES) // 4 - stos_brute_pairs.GPU_WORKER_CONTEXT_BYTES
    assert stos_brute_pairs.gpu_worker_pool_limit(4, free_bytes=free) == expected
    assert stos_brute_pairs.gpu_worker_pool_limit(64, free_bytes=free) == stos_brute_pairs.GPU_WORKER_MIN_POOL_BYTES


@pytest.fixture
def fresh_worker_state(monkeypatch: pytest.MonkeyPatch):
    """A worker that is on CuPy and not yet poisoned."""
    monkeypatch.setattr(stos_brute_pairs, '_worker_poisoned', None)
    monkeypatch.setattr(stos_brute_pairs.nornir_imageregistration, 'UsingCupy', lambda: True)


def test_worker_marks_itself_poisoned_when_its_context_fails(monkeypatch: pytest.MonkeyPatch,
                                                             fresh_worker_state) -> None:
    monkeypatch.setattr(stos_brute_pairs, 'run_stos_brute_pair', mock.Mock(side_effect=RuntimeError('cudaError')))
    monkeypatch.setattr(stos_brute_pairs, '_cuda_context_usable', lambda: False)

    with pytest.raises(stos_brute_pairs.GpuWorkerPoisonedError) as first:
        stos_brute_pairs.run_stos_brute_pair_on_gpu_worker(_job('bad'))
    assert first.value.caused_here

    monkeypatch.setattr(stos_brute_pairs, 'run_stos_brute_pair', mock.Mock(side_effect=AssertionError('ran')))
    with pytest.raises(stos_brute_pairs.GpuWorkerPoisonedError) as later:
        stos_brute_pairs.run_stos_brute_pair_on_gpu_worker(_job('next'))
    assert not later.value.caused_here


def test_worker_with_healthy_context_reraises_the_pair_error(monkeypatch: pytest.MonkeyPatch,
                                                             fresh_worker_state) -> None:
    monkeypatch.setattr(stos_brute_pairs, 'run_stos_brute_pair', mock.Mock(side_effect=ValueError('no tissue')))
    monkeypatch.setattr(stos_brute_pairs, '_cuda_context_usable', lambda: True)

    with pytest.raises(ValueError):
        stos_brute_pairs.run_stos_brute_pair_on_gpu_worker(_job('a'))
    assert stos_brute_pairs._worker_poisoned is None


def test_poisoned_error_survives_pickling() -> None:
    import pickle
    error = stos_brute_pairs.GpuWorkerPoisonedError('msg', 'p', caused_here=True)
    copy = pickle.loads(pickle.dumps(error))
    assert (str(copy), copy.pair_name, copy.caused_here) == ('msg', 'p', True)
