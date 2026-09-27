"""SAM2 / SA-1B trainer formatting for SegmentationTraining export."""

from typing import Any

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
from nornir_buildmanager.operations.segmentationtraining.sam2.write import (
    append_manifest,
    replace_manifest_rows,
    sa1b_annotation,
    write_gallery,
    write_overlay,
    write_sa1b_json,
)
from nornir_buildmanager.progress import report_iterate_complete

__all__ = [
    "WriteGallery",
    "append_manifest",
    "replace_manifest_rows",
    "sa1b_annotation",
    "write_gallery",
    "write_overlay",
    "write_sa1b_json",
]


def WriteGallery(OutputPath: str | None = None, Repair: bool = False, **kwargs: Any) -> None:
    """Rebuild `{Output}/annotation_crops.sqlite` from crops, masks, and ignore.json.

    ``-Repair`` skips the rebuild when no crop was deleted or written. The
    rebuild rereads every crop JSON, which dominates an unchanged repair.
    """
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
