"""Picklable StosBrute pair jobs, the function that registers one pair, and pair executors.

``block.StosBrute`` (one mapping) and ``block.StosBruteAll`` (every mapping of a block)
both register a pair through :func:`run_stos_brute_pair`, so they share one
implementation. A job holds only paths and parameters, never volume-tree nodes, so an
executor may run it in another process.

``StosBruteAll`` hands jobs to a :class:`StosBrutePairExecutor`; :class:`InProcessPairExecutor`
registers each pair in the calling process on the active backend, one at a time, and
:class:`GpuWorkerPairExecutor` registers pairs in N spawned worker processes that all use
CuPy on the shared GPU, so each pair's host-side work runs on its own core.
"""

from __future__ import annotations

import concurrent.futures
import logging
import multiprocessing
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from typing import Any, Protocol

import nornir_imageregistration
import nornir_imageregistration.settings
import nornir_pools
import nornir_shared.misc
from nornir_imageregistration import stos_brute

_logger = logging.getLogger(__name__)

#: Seed applied before every pair, so a pair's noise fills do not depend on which pairs
#: ran before it in the same process.
STOS_BRUTE_PAIR_SEED: int = int(nornir_imageregistration.DEFAULT_RANDOM_DATA_SEED)


@dataclass(frozen=True)
class StosBrutePairJob:
    """Everything one StosBrute pair registration needs, as plain picklable data."""

    pair_name: str
    output_stos_path: str
    target_image_path: str
    source_image_path: str
    target_mask_path: str | None
    source_mask_path: str | None
    downsample: float
    method: nornir_imageregistration.settings.SliceToSliceMethod
    angle_search_range: Any = None
    test_for_flip: bool = True
    warped_scale: float | None = None
    random_seed: int | None = STOS_BRUTE_PAIR_SEED
    #: Write here instead of ``output_stos_path``; the main process renames it when it
    #: publishes the pair, so a crash never leaves an output .stos without metadata.
    staging_path: str | None = None


class StosBrutePairError(RuntimeError):
    """One or more StosBrute pairs failed; ``pair_names`` lists them."""

    pair_names: list[str]

    def __init__(self, message: str, pair_names: list[str]) -> None:
        super().__init__(message)
        self.pair_names = list(pair_names)


#: Weight below which a StosBrute pose is reported as ambiguous. On RC3 the weight at random
#: poses tops out near 1.9-2.1 and real matches score 3-4 or more. Windows vs Linux reruns of
#: 410 RC2 pairs (2026-11) differed by a median 1.98 px (max 25 px) at weight >= 4, but by up
#: to 75.6 px (691-690, weight 2.77) below 3: there the winner barely beats other poses, so
#: small numeric differences between platforms pick a different one.
STOS_BRUTE_AMBIGUOUS_WEIGHT: float = 3.0


@dataclass(frozen=True)
class StosBrutePairResult:
    """What a finished pair reports back to the main process."""

    pair_name: str
    output_stos_path: str
    elapsed_seconds: float
    pid: int
    #: Bytes held by the process's CuPy memory pool after the pair; None on NumPy.
    gpu_pool_bytes: int | None = None
    #: The registration the .stos records, for the per-pair log line (weak pairs show a
    #: low weight; peak_ratio is None when the method did not measure it).
    angle: float | None = None
    offset_yx: tuple[float, float] | None = None
    scale: float | None = None
    flipped_ud: bool | None = None
    weight: float | None = None
    peak_ratio: float | None = None

    @property
    def ambiguous(self) -> bool:
        """True when the pose's weight is too low to beat other poses reliably."""
        return self.weight is not None and self.weight < STOS_BRUTE_AMBIGUOUS_WEIGHT

    def describe(self) -> str:
        """One-line summary of the registration for the build log."""
        if self.angle is None:
            return ""
        offset = (f"({self.offset_yx[1]:.1f}x, {self.offset_yx[0]:.1f}y)"
                  if self.offset_yx is not None else "(?)")
        ratio = f", peak ratio {self.peak_ratio:.2f}" if self.peak_ratio is not None else ""
        return (f"angle {self.angle:.2f}, offset {offset}, scale {self.scale:.4f}, "
                f"flipped {self.flipped_ud}, weight {self.weight:.3f}{ratio}")


def run_stos_brute_pair(job: StosBrutePairJob) -> StosBrutePairResult:
    """Register one section pair with stos-brute and save the .stos file.

    Module-level and picklable so a process pool can run it. Uses the backend active in
    the calling process. Writes ``job.staging_path`` when set, else
    ``job.output_stos_path``; the caller updates checksums and volume metadata.
    """
    started = time.perf_counter()
    if job.random_seed is not None:
        nornir_imageregistration.seed_random_data(job.random_seed)

    method = job.method
    if method is None:
        method = nornir_imageregistration.settings.SliceToSliceMethod.BruteForce

    common: dict[str, Any] = {
        'target_image': job.target_image_path,
        'source_image': job.source_image_path,
        'target_mask': job.target_mask_path,
        'source_mask': job.source_mask_path,
        'AngleSearchRange': job.angle_search_range,
        'TestFlip': job.test_for_flip,
        'WarpedImageScaleFactors': job.warped_scale,
        'method': method,
    }

    if method == nornir_imageregistration.settings.SliceToSliceMethod.BruteForce:
        alignment = stos_brute.SliceToSliceRigidRegistration(**common)
    elif method == nornir_imageregistration.settings.SliceToSliceMethod.LogPolar:
        # Match Pyre Operations→Log Polar (Fast): overlap 0.75 and max dim 818.
        # Independent of computation backend (NumPy or CuPy).
        alignment = stos_brute.SliceToSliceRigidRegistration(
            **common,
            MinOverlap=stos_brute.LOGPOLAR_PIPELINE_MIN_OVERLAP,
            LargestDimension=stos_brute.LOGPOLAR_PIPELINE_LARGEST_DIMENSION,
        )
    else:
        raise ValueError("Unknown method " + str(method))

    # Close thread pools to prevent workers from sticking around and slowing the rest of the run.
    # Process pools stay warm; all tasks must finish before continuing (ReleaseStagePools waits first).
    nornir_pools.ReleaseStagePools()

    stos = alignment.ToStos(job.target_image_path,
                            job.source_image_path,
                            job.target_mask_path,
                            job.source_mask_path,
                            PixelSpacing=job.downsample)

    write_path = job.staging_path if job.staging_path is not None else job.output_stos_path
    stos.Save(write_path)

    gpu_pool_bytes = None
    if nornir_imageregistration.UsingCupy():
        memory_pool = nornir_imageregistration.cp.get_default_memory_pool()  # pyright: ignore[reportAttributeAccessIssue]
        gpu_pool_bytes = int(memory_pool.total_bytes())

    return StosBrutePairResult(pair_name=job.pair_name,
                               output_stos_path=write_path,
                               elapsed_seconds=time.perf_counter() - started,
                               pid=os.getpid(),
                               gpu_pool_bytes=gpu_pool_bytes,
                               **_alignment_summary(alignment))


def _alignment_summary(alignment: Any) -> dict[str, Any]:
    """The registration fields of a StosBrutePairResult; empty if *alignment* lacks them.

    Only feeds the log line, so an alignment record without the usual attributes must
    not fail a pair that has already been saved.
    """
    try:
        peak = [float(v) for v in alignment.peak]
        ratio = alignment.peak_ratio
        return {'angle': float(alignment.angle),
                'offset_yx': (peak[0], peak[1]),
                'scale': float(alignment.scale),
                'flipped_ud': bool(alignment.flippedud),
                'weight': float(alignment.weight),
                'peak_ratio': float(ratio) if ratio is not None else None}
    except (AttributeError, TypeError, ValueError, IndexError):
        return {}


class StosBrutePairExecutor(Protocol):
    """Runs StosBrute pair jobs for ``block.StosBruteAll``.

    ``StosBruteAll`` prepares and publishes pairs in the main process, in order, and keeps
    at most ``max_in_flight`` jobs submitted; the executor only computes.
    """

    #: Most jobs ``StosBruteAll`` keeps submitted at once.
    max_in_flight: int
    #: Shown in the progress label and log, e.g. ``'in process'``.
    description: str

    def submit(self, job: StosBrutePairJob) -> concurrent.futures.Future[StosBrutePairResult]:
        """Start (or run) *job*; the future holds its result or exception."""
        ...

    def shutdown(self, cancel_pending: bool = False) -> None:
        """Release resources; with *cancel_pending*, abandon jobs not yet finished."""
        ...


class InProcessPairExecutor:
    """Registers each pair in the calling process, on its active backend, during ``submit``."""

    max_in_flight: int = 1
    description: str = 'in process'

    def submit(self, job: StosBrutePairJob) -> concurrent.futures.Future[StosBrutePairResult]:
        """Run *job* now; an ``Exception`` lands in the future, interrupts propagate."""
        future: concurrent.futures.Future[StosBrutePairResult] = concurrent.futures.Future()
        try:
            future.set_result(run_stos_brute_pair(job))
        except Exception as e:  # noqa: BLE001 - reported per pair by StosBruteAll
            future.set_exception(e)
        return future

    def shutdown(self, cancel_pending: bool = False) -> None:
        """Nothing to release."""


#: Jobs submitted per GPU worker: one running, one queued so a worker never waits on the
#: main process preparing or publishing the next pair.
GPU_WORKER_JOBS_PER_WORKER: int = 2

#: Device memory one GPU worker needs: its CUDA context plus its CuPy pool after a pair.
#: Measured 1.2-1.3 GB per worker on srv (RTX 4500 Ada, RC3 ds64 pairs), rounded up.
GPU_WORKER_DEVICE_BYTES: int = int(1.5 * 2 ** 30)

#: Device memory left free when sizing the automatic worker count (main process, others).
GPU_WORKER_RESERVE_BYTES: int = 2 ** 30


def gpu_worker_memory_limit(free_bytes: int | None = None) -> int | None:
    """How many GPU workers fit in the device's free memory, or None if it can't be read.

    *free_bytes* defaults to the current device's free memory (needs CuPy).
    """
    if free_bytes is None:
        try:
            free_bytes = int(nornir_imageregistration.cp.cuda.runtime.memGetInfo()[0])  # pyright: ignore[reportAttributeAccessIssue]
        except (AttributeError, RuntimeError, ImportError):
            return None
    return max(1, (free_bytes - GPU_WORKER_RESERVE_BYTES) // GPU_WORKER_DEVICE_BYTES)


def auto_gpu_worker_count(cpu_count: int | None = None, free_bytes: int | None = None) -> int:
    """Default GPU worker count: one per core, capped by what the GPU's free memory holds.

    Each worker does its pair's host-side work on its own core, so cores set the useful
    ceiling; device memory (``GPU_WORKER_DEVICE_BYTES`` each) sets the hard one.
    """
    cores = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    limit = gpu_worker_memory_limit(free_bytes)
    return max(1, min(cores, limit) if limit is not None else cores)


#: Device memory a worker's CUDA context takes outside its CuPy pool, subtracted from its
#: share of free memory to get the pool cap. Estimate: srv workers held 1.2-1.3 GB in
#: total after a ds64 pair (``GPU_WORKER_DEVICE_BYTES``), of which a context is a few
#: hundred MB.
GPU_WORKER_CONTEXT_BYTES: int = 512 * 2 ** 20

#: Smallest pool cap a worker gets; below it pairs would fail with out-of-memory anyway.
GPU_WORKER_MIN_POOL_BYTES: int = 512 * 2 ** 20

#: Times the pair that poisoned a worker's CUDA context is retried on a fresh worker.
GPU_WORKER_POISON_RETRIES: int = 1

#: Set in a GPU worker once its CUDA context fails the health probe; later jobs are refused.
_worker_poisoned: str | None = None


class GpuWorkerPoisonedError(RuntimeError):
    """A GPU worker's CUDA context is unusable (a sticky error such as cudaErrorIllegalAddress).

    ``caused_here`` is True for the job whose failure poisoned the worker and False for
    jobs the worker refused afterwards. The executor replaces the worker and resubmits.
    """

    pair_name: str
    caused_here: bool

    def __init__(self, message: str, pair_name: str, caused_here: bool) -> None:
        super().__init__(message)
        self.pair_name = pair_name
        self.caused_here = caused_here

    def __reduce__(self) -> tuple[Any, ...]:
        return (GpuWorkerPoisonedError, (str(self), self.pair_name, self.caused_here))


def gpu_worker_pool_limit(workers: int, free_bytes: int | None = None) -> int | None:
    """CuPy pool cap for each of *workers*: an equal share of free device memory.

    The share is free memory less ``GPU_WORKER_RESERVE_BYTES``, split evenly, less one
    CUDA context. A capped pool makes a worker that needs more fail its pair with an
    out-of-memory error instead of oversubscribing a GPU that may also drive a display.
    None if free memory can't be read.
    """
    if free_bytes is None:
        try:
            free_bytes = int(nornir_imageregistration.cp.cuda.runtime.memGetInfo()[0])  # pyright: ignore[reportAttributeAccessIssue]
        except (AttributeError, RuntimeError, ImportError):
            return None
    share = (free_bytes - GPU_WORKER_RESERVE_BYTES) // max(1, workers) - GPU_WORKER_CONTEXT_BYTES
    return max(GPU_WORKER_MIN_POOL_BYTES, share)


def _init_gpu_pair_worker(log_queue: Any, log_level: int, pool_limit: int | None = None) -> None:
    """Spawned worker initializer: queue logging, CuPy through the child opt-in, pool cap."""
    if log_queue is not None:
        nornir_shared.misc.ConfigureWorkerQueueLogging(log_queue=log_queue, level=log_level)
    try:
        nornir_imageregistration.SetActiveComputationLib(nornir_imageregistration.ComputationLib.cupy,
                                                         allow_in_child=True)
    except (RuntimeError, ModuleNotFoundError) as e:
        # Reported per pair by run_stos_brute_pair_on_gpu_worker, which names the pair.
        _logger.error("StosBrute GPU worker %d could not activate CuPy: %s", os.getpid(), e)
        return
    if pool_limit is not None and nornir_imageregistration.UsingCupy():
        nornir_imageregistration.cp.get_default_memory_pool().set_limit(size=int(pool_limit))  # pyright: ignore[reportAttributeAccessIssue]


def _cuda_context_usable() -> bool:
    """True if this process's CUDA context still runs work (a sticky error fails every call)."""
    try:
        cp = nornir_imageregistration.cp
        cp.cuda.runtime.deviceSynchronize()  # pyright: ignore[reportAttributeAccessIssue]
        probe = cp.arange(4, dtype=cp.float32)  # pyright: ignore[reportAttributeAccessIssue]
        return float(probe.sum()) == 6.0
    except Exception:  # noqa: BLE001 - any failure means the context can't be trusted
        return False


def run_stos_brute_pair_on_gpu_worker(job: StosBrutePairJob) -> StosBrutePairResult:
    """GPU worker entry point: refuse NumPy fallback and poisoned contexts, then register the pair.

    After a failure the worker probes its CUDA context. A sticky CUDA error leaves every
    later call failing, so the worker marks itself poisoned, raises
    :class:`GpuWorkerPoisonedError` and refuses further jobs until the executor replaces it.
    """
    global _worker_poisoned
    if _worker_poisoned is not None:
        raise GpuWorkerPoisonedError(f"StosBrute GPU worker {os.getpid()} has an unusable CUDA context "
                                     f"({_worker_poisoned}); pair {job.pair_name} not registered",
                                     job.pair_name, caused_here=False)
    if not nornir_imageregistration.UsingCupy():
        raise RuntimeError(f"StosBrute GPU worker {os.getpid()} is not on CuPy (activation or runtime "
                           f"probe failed); pair {job.pair_name} not registered")
    try:
        return run_stos_brute_pair(job)
    except Exception as e:
        if _cuda_context_usable():
            raise
        _worker_poisoned = f"{type(e).__name__}: {e}"
        _logger.error("StosBrute GPU worker %d: CUDA context unusable after pair %s: %s",
                      os.getpid(), job.pair_name, _worker_poisoned)
        raise GpuWorkerPoisonedError(f"StosBrute GPU worker {os.getpid()} lost its CUDA context on pair "
                                     f"{job.pair_name}: {_worker_poisoned}", job.pair_name,
                                     caused_here=True) from e


@dataclass
class _LaneJob:
    """A job submitted to a GPU worker lane, with the future handed to the caller."""

    job: StosBrutePairJob
    future: concurrent.futures.Future[StosBrutePairResult]
    poison_retries: int = 0


class GpuWorkerPairExecutor:
    """Registers pairs in ``workers`` spawned processes, each on CuPy, sharing the GPU.

    About three quarters of a CuPy pair is host NumPy work (log-polar scale search) with
    the GPU mostly idle, so several CuPy processes interleave their GPU work while each
    does its host work on its own core. Workers are spawned (fresh interpreters, never
    forked, so no inherited CUDA state) and opt in with
    ``SetActiveComputationLib(cupy, allow_in_child=True)``. Each worker keeps its own CUDA
    context and a CuPy memory pool capped at ``pool_limit`` bytes.

    Each worker is a one-process pool (a lane), so a worker whose CUDA context is poisoned
    by a sticky error is replaced alone: its lane restarts, the jobs queued on it are
    resubmitted, and the job that poisoned it is retried ``GPU_WORKER_POISON_RETRIES``
    times before its error is reported. RC2 (2026-11-02) lost 259 pairs to one poisoned
    worker before this.
    """

    max_in_flight: int
    description: str
    workers: int
    pool_limit: int | None
    _lane_factory: Callable[[], concurrent.futures.ProcessPoolExecutor]
    _lanes: list[concurrent.futures.ProcessPoolExecutor]
    _lane_jobs: list[list[_LaneJob]]
    _lock: threading.RLock
    _closed: bool
    _log_queue: Any = None
    _log_level: int = logging.INFO

    def __init__(self, workers: int, pool_limit: int | None = None,
                 lane_factory: Callable[[], concurrent.futures.ProcessPoolExecutor] | None = None) -> None:
        if workers < 1:
            raise ValueError(f"workers must be >= 1, got {workers}")
        self.workers = int(workers)
        self.max_in_flight = GPU_WORKER_JOBS_PER_WORKER * self.workers
        self.pool_limit = pool_limit if pool_limit is not None else gpu_worker_pool_limit(self.workers)
        limit_text = (f", {self.pool_limit / 2 ** 30:.1f} GB GPU pool cap each"
                      if self.pool_limit is not None else "")
        self.description = f"{self.workers} GPU worker processes{limit_text}"
        self._lane_factory = lane_factory if lane_factory is not None else self._spawn_lane
        self._lock = threading.RLock()
        self._closed = False
        self._lanes = [self._lane_factory() for _ in range(self.workers)]
        self._lane_jobs = [[] for _ in range(self.workers)]

    def _spawn_lane(self) -> concurrent.futures.ProcessPoolExecutor:
        """One spawned CuPy worker, built through nornir_pools so its settings match other pools."""
        if self._log_queue is None:
            self._log_level = logging.getLogger().getEffectiveLevel()
            self._log_queue = nornir_shared.misc.StartMultiprocessLoggingListener(level=self._log_level)
        return nornir_pools.CreateProcessPoolExecutor(1,
                                                      mp_context=multiprocessing.get_context('spawn'),
                                                      initializer=_init_gpu_pair_worker,
                                                      initargs=(self._log_queue, self._log_level,
                                                                self.pool_limit))

    def submit(self, job: StosBrutePairJob) -> concurrent.futures.Future[StosBrutePairResult]:
        """Queue *job* on the worker with the fewest outstanding jobs."""
        lane_job = _LaneJob(job=job, future=concurrent.futures.Future())
        with self._lock:
            lane = min(range(self.workers), key=lambda i: len(self._lane_jobs[i]))
            self._submit_to_lane(lane, lane_job)
        return lane_job.future

    def _submit_to_lane(self, lane: int, lane_job: _LaneJob) -> None:
        """Send *lane_job* to *lane*'s worker; the caller holds ``_lock``."""
        self._lane_jobs[lane].append(lane_job)
        pool = self._lanes[lane]
        try:
            inner = pool.submit(run_stos_brute_pair_on_gpu_worker, lane_job.job)
        except BrokenProcessPool as e:
            # The worker died between jobs; report it through the same path as a running job.
            inner = concurrent.futures.Future()
            inner.set_exception(e)
        inner.add_done_callback(lambda f: self._on_done(lane, pool, lane_job, f))

    def _retire_lane(self, lane: int, pool: concurrent.futures.ProcessPoolExecutor, reason: object) -> None:
        """Replace *lane*'s worker if it is still *pool*; the caller holds ``_lock``."""
        if self._lanes[lane] is not pool:
            return
        _logger.warning("StosBrute GPU worker lane %d is unusable (%s); starting a new worker", lane, reason)
        self._lanes[lane] = self._lane_factory()
        # Off this callback thread: the old pool's shutdown joins the thread running us.
        threading.Thread(target=_terminate_pool, args=(pool,), daemon=True,
                         name='stos-brute-retire-gpu-worker').start()

    def _on_done(self, lane: int, pool: concurrent.futures.ProcessPoolExecutor, lane_job: _LaneJob,
                 inner: concurrent.futures.Future[StosBrutePairResult]) -> None:
        """Done callback: settle the caller's future, which must never be left pending."""
        try:
            self._settle(lane, pool, lane_job, inner)
        except Exception as e:  # noqa: BLE001 - e.g. a replacement worker failed to start
            _logger.error("StosBrute could not settle pair %s: %s", lane_job.job.pair_name, e)
            if not lane_job.future.done():
                lane_job.future.set_exception(e)

    def _settle(self, lane: int, pool: concurrent.futures.ProcessPoolExecutor, lane_job: _LaneJob,
                inner: concurrent.futures.Future[StosBrutePairResult]) -> None:
        """Pass a lane's result on, or replace a poisoned worker and resubmit its jobs."""
        error = None if inner.cancelled() else inner.exception()
        with self._lock:
            if lane_job in self._lane_jobs[lane]:
                self._lane_jobs[lane].remove(lane_job)
            retired = self._lanes[lane] is not pool
            if self._closed:
                retry = False
            elif isinstance(error, GpuWorkerPoisonedError) and error.caused_here:
                self._retire_lane(lane, pool, error)
                retry = lane_job.poison_retries < GPU_WORKER_POISON_RETRIES
                lane_job.poison_retries += 1
            elif isinstance(error, (GpuWorkerPoisonedError, BrokenProcessPool)) \
                    or (inner.cancelled() and retired):
                # Refused by, or lost with, a poisoned worker: not this pair's fault.
                self._retire_lane(lane, pool, error)
                retry = True
            else:
                retry = False
            if retry:
                self._submit_to_lane(lane, lane_job)
                return
        if error is not None:
            lane_job.future.set_exception(error)
        elif inner.cancelled():
            lane_job.future.cancel()
        else:
            lane_job.future.set_result(inner.result())

    def shutdown(self, cancel_pending: bool = False) -> None:
        """Wait for the workers to exit; with *cancel_pending*, drop queued jobs and stop running ones."""
        with self._lock:
            self._closed = True
            lanes = list(self._lanes)
        for pool in lanes:
            if cancel_pending:
                _terminate_pool(pool)
            else:
                pool.shutdown(wait=True)
        if cancel_pending:
            with self._lock:
                abandoned = [lane_job for jobs in self._lane_jobs for lane_job in jobs]
            for lane_job in abandoned:
                lane_job.future.cancel()


def _terminate_pool(pool: concurrent.futures.ProcessPoolExecutor) -> None:
    """Stop *pool*'s workers without waiting for their jobs."""
    terminate = getattr(pool, 'terminate_workers', None)  # Python 3.14+
    if terminate is not None:
        terminate()
        return
    processes = list((getattr(pool, '_processes', None) or {}).values())  # Python 3.13
    pool.shutdown(wait=False, cancel_futures=True)
    for process in processes:
        process.terminate()
    for process in processes:
        process.join()
