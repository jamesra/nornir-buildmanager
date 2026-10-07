"""SAM2 / SA-1B trainer formatting for SegmentationTraining export.

Catalog and shared logging load only when ``WriteGallery`` runs, so importing
``sam2.write`` does not pull the export pipeline.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "WriteGallery",
    "append_manifest",
    "replace_manifest_rows",
    "sa1b_annotation",
    "write_gallery",
    "write_overlay",
    "write_sa1b_json",
]

_WRITE_EXPORTS = (
    "append_manifest",
    "replace_manifest_rows",
    "sa1b_annotation",
    "write_gallery",
    "write_overlay",
    "write_sa1b_json",
)


def __getattr__(name: str) -> Any:
    if name not in _WRITE_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    write = importlib.import_module(
        "nornir_buildmanager.operations.segmentationtraining.sam2.write"
    )
    value = getattr(write, name)
    globals()[name] = value
    return value


def WriteGallery(OutputPath: str | None = None, Repair: bool = False, **kwargs: Any) -> None:
    """Rebuild `{Output}/annotation_crops.sqlite` from crops, masks, and ignore.json.

    ``-Repair`` skips the rebuild when no crop was deleted or written. The
    rebuild rereads every crop JSON, which dominates an unchanged repair.
    """
    from nornir_shared import prettyoutput

    from nornir_buildmanager.operations.segmentationtraining.catalog import (
        apply_ignore_moves,
        rebuild_catalog,
        take_catalog_dirty,
    )
    from nornir_buildmanager.operations.segmentationtraining.ingest import (
        load_cache_meta_sections,
    )
    from nornir_buildmanager.operations.segmentationtraining.progress import SECTIONS_TRACK_ID
    from nornir_buildmanager.progress import report_iterate_complete

    del kwargs
    if not OutputPath:
        return None
    if Repair and not take_catalog_dirty():
        prettyoutput.Log("WriteGallery: repair left the catalog unchanged")
        return None
    apply_ignore_moves(OutputPath)
    count = rebuild_catalog(OutputPath)
    prettyoutput.Log(f"WriteGallery: cataloged {count} location(s)")
    sections = load_cache_meta_sections(OutputPath)
    report_iterate_complete(SECTIONS_TRACK_ID, len(sections))
    return None
