"""SAM2 / SA-1B trainer formatting for SegmentationTraining export."""

from typing import Any

from nornir_shared import prettyoutput

from nornir_buildmanager.operations.segmentationtraining.catalog import (
    apply_ignore_moves,
    rebuild_catalog,
)
from nornir_buildmanager.operations.segmentationtraining.sam2.write import (
    append_manifest,
    replace_manifest_rows,
    sa1b_annotation,
    write_gallery,
    write_overlay,
    write_sa1b_json,
)

__all__ = [
    "WriteGallery",
    "append_manifest",
    "replace_manifest_rows",
    "sa1b_annotation",
    "write_gallery",
    "write_overlay",
    "write_sa1b_json",
]


def WriteGallery(OutputPath: str | None = None, **kwargs: Any) -> None:
    """Rebuild `{Output}/annotation_crops.sqlite` from crops, masks, and ignore.json."""
    del kwargs
    if not OutputPath:
        return None
    apply_ignore_moves(OutputPath)
    count = rebuild_catalog(OutputPath)
    prettyoutput.Log(f"WriteGallery: cataloged {count} location(s)")
    return None
