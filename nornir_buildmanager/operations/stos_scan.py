"""Shared STOS-group walk and dashboard progress for systematic .stos pipelines.

Stale-file predicates stay pipeline-specific. This module only provides a
stable transform walk and a thin ``iterate_progress`` wrapper so track fields
stay consistent across refine, scale, blend, brute, slice-to-volume, overlays,
and quality scoring.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from nornir_buildmanager.progress import report_iterate


def stos_transform_sort_key(transform: Any) -> tuple[int, int, str]:
    """Stable sort key: mapped section, control section, output basename."""
    mapped = getattr(transform, 'MappedSectionNumber', None)
    control = getattr(transform, 'ControlSectionNumber', None)
    path = getattr(transform, 'FullPath', None) or getattr(transform, 'Path', '') or ''
    return (
        int(mapped) if mapped is not None else 0,
        int(control) if control is not None else 0,
        os.path.basename(str(path)),
    )


def iter_stos_group_transforms(group: Any) -> Iterator[Any]:
    """Yield each Transform under *group* in mapped/control/basename order."""
    items: list[Any] = []
    for mapping in group.SectionMappings:
        items.extend(mapping.Transforms)
    items.sort(key=stos_transform_sort_key)
    yield from items


def report_stos_work_progress(
        track_id: str,
        current: int,
        total: int,
        label: str,
        *,
        depth: int = 0,
        **fields: Any) -> None:
    """Publish dashboard progress for a STOS-file (or STOS-mapping) work track."""
    report_iterate(track_id, current, total, label, depth=depth, **fields)
