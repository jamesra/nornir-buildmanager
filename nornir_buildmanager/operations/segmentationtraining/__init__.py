"""Reusable SegmentationTraining ingest, crop, and mask operations.

Pipeline, catalog, and cleanup are imported on attribute access so a mask-only
import does not load the export pipeline.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "CleanupAnnotationCrops",
    "ExportSectionCrops",
    "IngestGeometries",
    "RepairAnnotationOverlays",
    "ScoreAnnotationCrops",
    "apply_ignore_moves",
    "export_section_crops",
    "rebuild_catalog",
    "repair_overlays",
    "resolve_max_texture",
    "upsert_catalog",
]

_EXPORTS = {
    "CleanupAnnotationCrops": "cleanup",
    "ExportSectionCrops": "pipeline",
    "IngestGeometries": "pipeline",
    "RepairAnnotationOverlays": "pipeline",
    "ScoreAnnotationCrops": "score",
    "apply_ignore_moves": "catalog",
    "export_section_crops": "pipeline",
    "rebuild_catalog": "catalog",
    "repair_overlays": "pipeline",
    "resolve_max_texture": "pipeline",
    "upsert_catalog": "catalog",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(
        f"nornir_buildmanager.operations.segmentationtraining.{module_name}"
    )
    value = getattr(module, name)
    globals()[name] = value
    return value
