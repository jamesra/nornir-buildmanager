"""Dashboard track ids and throttled reporters for ExportAnnotationCrops."""

from __future__ import annotations

from typing import Any

from nornir_shared.prettyoutput import ThrottledProgressReporter

from nornir_buildmanager.progress import report_iterate, report_iterate_complete

INGEST_TRACK_ID = "export_annotation:ingest"
SECTIONS_TRACK_ID = "export_annotation:sections"
MASKS_TRACK_ID = "export_annotation:masks"
STITCH_TRACK_ID = "export_annotation:stitch"
STRIPS_TRACK_ID = "export_annotation:strips"
GALLERY_TRACK_ID = "export_annotation:gallery"

INGEST_LABEL = "ExportAnnotationCrops ingest"
SECTIONS_LABEL = "ExportAnnotationCrops sections"
MASKS_LABEL = "ExportAnnotationCrops annotations"
STITCH_LABEL = "ExportAnnotationCrops stitch"
STRIPS_LABEL = "ExportAnnotationCrops strips"
GALLERY_LABEL = "ExportAnnotationCrops gallery"


class IterateProgressReporter(ThrottledProgressReporter):
    """Throttled determinate ``report_iterate`` track with configurable depth.

    Unlike the base class, ``complete`` on a never-started reporter starts it
    first, so the dashboard still sees 0/N then N/N.
    """

    _label: str
    _depth: int

    def __init__(
        self,
        track_id: str,
        total: int,
        *,
        label: str,
        depth: int = 0,
        min_interval_s: float = 0.25,
        section: int | str | None = None,
    ) -> None:
        super().__init__(track_id, total, min_interval_s=min_interval_s, section=section)
        self._label = label
        self._depth = depth

    def complete(self) -> None:
        """Start if needed, publish final progress, then remove the track."""
        self.start()
        super().complete()

    def _emit(self, current: int, **fields: Any) -> None:
        extra = dict(fields)
        if self._section is not None and "section" not in extra:
            extra["section"] = self._section
        report_iterate(
            self._track_id,
            current,
            self._total,
            self._label,
            depth=self._depth,
            **extra,
        )

    def _emit_complete(self) -> None:
        report_iterate_complete(self._track_id, self._total)
