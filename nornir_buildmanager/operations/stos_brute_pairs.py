"""Picklable StosBrute pair jobs, the worker that registers one pair, and worker-count policy.

``block.StosBrute`` (one mapping, serial) and ``block.StosBruteAll`` (every mapping of a
block, optionally across a process pool) both register a pair through
:func:`run_stos_brute_pair`, so the serial and parallel paths share one implementation.
A job holds only paths and parameters; the volume tree never crosses the process boundary.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

import nornir_imageregistration
import nornir_imageregistration.settings
import nornir_pools
from nornir_imageregistration import stos_brute

_logger = logging.getLogger(__name__)

#: Seed applied before every pair, so a pair's noise fills do not depend on which pairs
#: ran before it in the same process (or on which worker it landed).
STOS_BRUTE_PAIR_SEED: int = int(nornir_imageregistration.DEFAULT_RANDOM_DATA_SEED)

#: Auto worker count leaves this many cores for the main process and the OS.
_AUTO_RESERVED_CORES: int = 2

#: Thread-count variables pinned to 1 in NumPy pool workers so N workers do not each
#: start a BLAS / OpenMP pool the size of the machine.
_THREAD_ENV_VARS: tuple[str, ...] = (
    'OMP_NUM_THREADS',
    'OPENBLAS_NUM_THREADS',
    'MKL_NUM_THREADS',
    'NUMEXPR_NUM_THREADS',
    'VECLIB_MAXIMUM_THREADS',
)


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
    #: ``'numpy'`` / ``'cupy'`` to activate in the process running the job; None leaves
    #: the process backend alone (the serial path in the main process).
    computation_lib: str | None = None
    #: False keeps today's serial behaviour (angle sweep may use the global thread pool).
    #: Pool workers pass True so N workers do not each start a machine-sized thread pool.
    single_thread: bool = False
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


def _activate_computation_lib(lib_name: str | None) -> None:
    """Switch this process to *lib_name* (``numpy`` or ``cupy``) when it differs."""
    if lib_name is None:
        return
    lib = nornir_imageregistration.ComputationLib[lib_name.lower()]
    if nornir_imageregistration.GetActiveComputationLib() != lib:
        nornir_imageregistration.SetActiveComputationLib(lib)


def _pin_worker_threads() -> None:
    """Limit native thread pools in a NumPy pool worker to one thread each.

    Best effort: the environment variables only reach libraries loaded after this call,
    so already-loaded BLAS keeps its pool unless ``threadpoolctl`` is installed. The
    stos-brute path is FFT / ndimage / scikit-image work, which is single-threaded.
    """
    for name in _THREAD_ENV_VARS:
        os.environ[name] = '1'
    try:
        import threadpoolctl  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]  # optional
    except ImportError:
        pass
    else:
        threadpoolctl.threadpool_limits(1)
    cv2 = sys.modules.get('cv2')
    if cv2 is not None:
        cv2.setNumThreads(1)


def run_stos_brute_pair(job: StosBrutePairJob) -> StosBrutePairResult:
    """Register one section pair with stos-brute and save the .stos file.

    Module-level and picklable so a process pool can run it. Writes
    ``job.output_stos_path``; the caller updates checksums and volume metadata.
    """
    started = time.perf_counter()
    if job.computation_lib is not None:
        _activate_computation_lib(job.computation_lib)
    if job.single_thread and job.computation_lib == 'numpy':
        _pin_worker_threads()
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
        'SingleThread': job.single_thread,
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

    return StosBrutePairResult(pair_name=job.pair_name,
                               output_stos_path=write_path,
                               elapsed_seconds=time.perf_counter() - started,
                               pid=os.getpid())


def auto_numpy_worker_count() -> int:
    """Auto NumPy worker count: all cores but two, capped by MAX_PYTHON_THREADS."""
    count = max(1, (os.cpu_count() or 1) - _AUTO_RESERVED_CORES)
    limited = nornir_pools.ApplyOSThreadLimit(count)
    return max(1, int(limited if limited is not None else count))


def resolve_stos_brute_workers(requested: int | None) -> int:
    """Return how many pairs StosBruteAll registers at once.

    ``1`` is the serial path in the main process (today's behaviour, on the active
    backend). Larger values run that many NumPy worker processes:
    ``nornir_imageregistration`` forces every multiprocessing child to NumPy and refuses
    CuPy there, so pool workers cannot use the GPU.

    ``None`` or ``<= 0`` is auto: NumPy backend -> :func:`auto_numpy_worker_count`;
    CuPy backend -> 1 (serial on the GPU), so a GPU build does not silently move its
    scoring to the CPU. Pass an explicit count to run NumPy workers under a CuPy build.
    """
    if requested is not None and requested > 0:
        if requested == 1:
            return 1
        limited = nornir_pools.ApplyOSThreadLimit(int(requested))
        return max(1, int(limited if limited is not None else requested))

    if nornir_imageregistration.UsingCupy():
        return 1
    return auto_numpy_worker_count()


def default_stos_brute_max_in_flight(workers: int) -> int:
    """Jobs queued in the pool at once: two per worker keeps workers busy while the
    main process waits on the oldest job, and bounds main-process bookkeeping."""
    return max(1, 2 * int(workers))
