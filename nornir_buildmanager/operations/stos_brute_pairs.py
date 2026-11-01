"""Picklable StosBrute pair jobs, the function that registers one pair, and pair executors.

``block.StosBrute`` (one mapping) and ``block.StosBruteAll`` (every mapping of a block)
both register a pair through :func:`run_stos_brute_pair`, so they share one
implementation. A job holds only paths and parameters, never volume-tree nodes, so an
executor may run it in another process.

``StosBruteAll`` hands jobs to a :class:`StosBrutePairExecutor`; :class:`InProcessPairExecutor`
registers each pair in the calling process on the active backend, one at a time.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

import nornir_imageregistration
import nornir_imageregistration.settings
import nornir_pools
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

    return StosBrutePairResult(pair_name=job.pair_name,
                               output_stos_path=write_path,
                               elapsed_seconds=time.perf_counter() - started,
                               pid=os.getpid())


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
