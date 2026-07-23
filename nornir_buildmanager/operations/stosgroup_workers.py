"""Picklable workers and bounded pool dispatch for STOS group operations."""

from __future__ import annotations

import collections
import logging
import os
import shutil
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

import nornir_imageregistration
import nornir_imageregistration.transforms
from nornir_imageregistration.files import stosfile

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinearBlendResult:
    """Outcome of linear-blending one STOS file."""

    changed: bool
    inv_corr_before: float
    inv_corr_after: float
    flip_ud: bool
    max_displacement: float
    orientation_flipped: bool


@dataclass(frozen=True)
class ScaleStosResult:
    """Outcome of scaling one STOS file."""

    generated: bool


@dataclass
class StosGroupPoolJob:
    """One parallel STOS group task; context stays on the main process."""

    name: str
    func: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    context: Any = None


def linear_blend_stos_file(input_path: str,
                           output_path: str,
                           min_blend: float | None,
                           travel_limit: float | None,
                           ignore_rotation: bool,
                           reblend_iterations: int,
                           reblend_tolerance: float | None,
                           max_blend: float | None = None) -> LinearBlendResult:
    """Copy, linear-blend, and save one STOS transform file."""
    shutil.copyfile(input_path, output_path)
    loaded_output_stos = stosfile.StosFile.Load(output_path)
    transform_obj = nornir_imageregistration.transforms.LoadTransform(
        loaded_output_stos.Transform)  # type: ignore[arg-type]

    inv_corr_before = 0.0
    flip_ud = False
    max_displacement = 0.0
    inv_corr_after = 0.0

    if isinstance(transform_obj, nornir_imageregistration.IControlPoints):
        inv_corr_before = nornir_imageregistration.transforms.utils.estimate_inverse_map_y_correlation(
            transform_obj)
        rigid_fit = nornir_imageregistration.transforms.converters.ConvertControlPointsToRigidTransformForBlend(
            transform_obj,
            ignore_rotation=ignore_rotation)
        flip_ud = bool(getattr(rigid_fit, 'flip_ud', False))

    blend_kwargs: dict[str, Any] = {
        'min_blend': min_blend,
        'travel_limit': travel_limit,
        'ignore_rotation': ignore_rotation,
        'reblend_iterations': reblend_iterations,
    }
    if reblend_tolerance is not None:
        blend_kwargs['reblend_tolerance'] = reblend_tolerance
    if max_blend is not None:
        blend_kwargs['max_blend'] = max_blend

    transform_changed = loaded_output_stos.BlendWithLinear(**blend_kwargs)

    if isinstance(transform_obj, nornir_imageregistration.IControlPoints):
        output_transform = nornir_imageregistration.transforms.LoadTransform(
            loaded_output_stos.Transform)  # type: ignore[arg-type]
        inv_corr_after = nornir_imageregistration.transforms.utils.estimate_inverse_map_y_correlation(
            output_transform)
        before_targets = nornir_imageregistration.transforms.utils._as_numpy_points(
            transform_obj.TargetPoints)
        after_targets = nornir_imageregistration.transforms.utils._as_numpy_points(
            output_transform.TargetPoints)
        max_displacement = float(np.max(np.linalg.norm(after_targets - before_targets, axis=1)))

    orientation_flipped = (
        abs(inv_corr_before) >=
        nornir_imageregistration.transforms.utils.INVERSE_MAP_Y_CORRELATION_THRESHOLD
        and inv_corr_after != 0.0
        and np.sign(inv_corr_before) != np.sign(inv_corr_after))

    log_msg = (
        f"LinearBlend {output_path}: "
        f"inv_corr {inv_corr_before:.6f} -> {inv_corr_after:.6f}, "
        f"flip_ud={flip_ud}, max_displacement={max_displacement:.3f}, "
        f"changed={transform_changed}")
    if orientation_flipped:
        _logger.warning("%s ORIENTATION_SIGN_FLIP", log_msg)
    else:
        _logger.info(log_msg)

    if transform_changed:
        loaded_output_stos.Save(output_path)

    return LinearBlendResult(
        changed=transform_changed,
        inv_corr_before=inv_corr_before,
        inv_corr_after=inv_corr_after,
        flip_ud=flip_ud,
        max_displacement=max_displacement,
        orientation_flipped=orientation_flipped,
    )


def scale_stos_file(input_stos_path: str,
                    output_stos_path: str,
                    input_downsample: int,
                    output_downsample: int,
                    control_image_path: str,
                    mapped_image_path: str,
                    control_mask_path: str | None,
                    mapped_mask_path: str | None,
                    use_masks: bool | None) -> ScaleStosResult:
    """Scale one STOS file to a new downsample level when metadata differs."""
    input_stos = stosfile.StosFile.Load(input_stos_path)
    if use_masks is None:
        use_masks = input_stos.HasMasks

    if not (input_stos.ControlImagePath == control_image_path and
            input_stos.MappedImagePath == mapped_image_path and
            input_stos.ControlMaskFullPath == control_mask_path and
            input_stos.MappedMaskFullPath == mapped_mask_path and
            output_downsample == input_downsample and
            input_stos.HasMasks == use_masks):
        modified_input_stos = input_stos.ChangeTransformPixelSpacing(
            oldspacing=input_downsample,
            newspacing=output_downsample,
            ControlImageFullPath=control_image_path,
            MappedImageFullPath=mapped_image_path,
            ControlMaskFullPath=control_mask_path,
            MappedMaskFullPath=mapped_mask_path)
        modified_input_stos.Save(output_stos_path)
        return ScaleStosResult(generated=True)

    return ScaleStosResult(generated=False)


def resolve_stos_group_workers(workers: int | None) -> int | None:
    """Return the effective STOS group worker count for the active computation backend."""
    if workers == 1:
        return 1
    if nornir_imageregistration.UsingCupy() and (workers is None or workers > 1):
        if workers is not None and workers > 1:
            _logger.warning(
                "STOS group workers capped to 1 with CuPy backend (requested %s); "
                "multiple CUDA worker processes are unsafe on a single GPU.",
                workers,
            )
        return 1
    return workers


def default_max_in_flight(workers: int | None) -> int:
    """Return the in-flight task cap for STOS group parallel dispatch."""
    workers = resolve_stos_group_workers(workers)
    if workers is not None and workers > 0:
        return workers
    return os.cpu_count() or 4


def run_bounded_stos_jobs(pool: Any,
                        jobs: Sequence[StosGroupPoolJob],
                        *,
                        max_in_flight: int | None = None) -> Iterator[tuple[StosGroupPoolJob, Any]]:
    """Submit STOS jobs with bounded concurrency; yield each completed job and result."""
    if not jobs:
        return

    if max_in_flight is None:
        max_in_flight = default_max_in_flight(None)
    max_in_flight = max(1, max_in_flight)

    if len(jobs) <= 1 or pool is None:
        for job in jobs:
            yield job, job.func(*job.args, **job.kwargs)
        return

    pending: collections.deque[StosGroupPoolJob] = collections.deque(jobs)
    in_flight: collections.deque[tuple[StosGroupPoolJob, Any]] = collections.deque()

    while pending or in_flight:
        while pending and len(in_flight) < max_in_flight:
            job = pending.popleft()
            task = pool.add_task(job.name, job.func, *job.args, **job.kwargs)
            in_flight.append((job, task))
        if not in_flight:
            break
        job, task = in_flight.popleft()
        yield job, task.wait_return()
