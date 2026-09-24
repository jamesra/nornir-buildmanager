"""Reusable SegmentationTraining ingest, crop, and mask operations."""

from nornir_buildmanager.operations.segmentationtraining.catalog import (
    apply_ignore_moves,
    rebuild_catalog,
    upsert_catalog,
)
from nornir_buildmanager.operations.segmentationtraining.cleanup import CleanupAnnotationCrops
from nornir_buildmanager.operations.segmentationtraining.pipeline import (
    ExportSectionCrops,
    IngestGeometries,
    RepairAnnotationOverlays,
    export_section_crops,
    repair_overlays,
    resolve_max_texture,
)
from nornir_buildmanager.operations.segmentationtraining.score import ScoreAnnotationCrops

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
