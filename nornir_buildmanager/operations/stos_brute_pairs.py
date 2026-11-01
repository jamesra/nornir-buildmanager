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
import time
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


def _init_gpu_pair_worker(log_queue: Any, log_level: int) -> None:
    """Spawned worker initializer: queue logging, then CuPy through the child opt-in."""
    if log_queue is not None:
        nornir_shared.misc.ConfigureWorkerQueueLogging(log_queue=log_queue, level=log_level)
    try:
        nornir_imageregistration.SetActiveComputationLib(nornir_imageregistration.ComputationLib.cupy,
                                                         allow_in_child=True)
    except (RuntimeError, ModuleNotFoundError) as e:
        # Reported per pair by run_stos_brute_pair_on_gpu_worker, which names the pair.
        _logger.error("StosBrute GPU worker %d could not activate CuPy: %s", os.getpid(), e)


def run_stos_brute_pair_on_gpu_worker(job: StosBrutePairJob) -> StosBrutePairResult:
    """GPU worker entry point: refuse to fall back to NumPy silently, then register the pair."""
    if not nornir_imageregistration.UsingCupy():
        raise RuntimeError(f"StosBrute GPU worker {os.getpid()} is not on CuPy (activation or runtime "
                           f"probe failed); pair {job.pair_name} not registered")
    return run_stos_brute_pair(job)


class GpuWorkerPairExecutor:
    """Registers pairs in ``workers`` spawned processes, each on CuPy, sharing the GPU.

    About three quarters of a CuPy pair is host NumPy work (log-polar scale search) with
    the GPU mostly idle, so several CuPy processes interleave their GPU work while each
    does its host work on its own core. Workers are spawned (fresh interpreters, never
    forked, so no inherited CUDA state) and opt in with
    ``SetActiveComputationLib(cupy, allow_in_child=True)``. Each worker keeps its own CUDA
    context and CuPy memory pool on the device.
    """

    max_in_flight: int
    description: str
    workers: int
    _pool: concurrent.futures.ProcessPoolExecutor

    def __init__(self, workers: int) -> None:
        if workers < 1:
            raise ValueError(f"workers must be >= 1, got {workers}")
        self.workers = int(workers)
        self.max_in_flight = GPU_WORKER_JOBS_PER_WORKER * self.workers
        self.description = f"{self.workers} GPU worker processes"
        log_level = logging.getLogger().getEffectiveLevel()
        log_queue = nornir_shared.misc.StartMultiprocessLoggingListener(level=log_level)
        self._pool = concurrent.futures.ProcessPoolExecutor(max_workers=self.workers,
                                                            mp_context=multiprocessing.get_context('spawn'),
                                                            initializer=_init_gpu_pair_worker,
                                                            initargs=(log_queue, log_level))

    def submit(self, job: StosBrutePairJob) -> concurrent.futures.Future[StosBrutePairResult]:
        """Queue *job* on a worker."""
        return self._pool.submit(run_stos_brute_pair_on_gpu_worker, job)

    def shutdown(self, cancel_pending: bool = False) -> None:
        """Wait for the workers to exit; with *cancel_pending*, drop queued jobs and stop running ones."""
        if not cancel_pending:
            self._pool.shutdown(wait=True)
            return
        terminate = getattr(self._pool, 'terminate_workers', None)  # Python 3.14+
        if terminate is not None:
            terminate()
            return
        processes = list((getattr(self._pool, '_processes', None) or {}).values())  # Python 3.13
        self._pool.shutdown(wait=False, cancel_futures=True)
        for process in processes:
            process.terminate()
        for process in processes:
            process.join()
