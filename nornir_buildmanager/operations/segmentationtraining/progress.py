"""Dashboard track ids and throttled reporters for ExportAnnotationCrops."""

from __future__ import annotations

import time
from typing import Any

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


class IterateProgressReporter:
    """Throttled determinate ``report_iterate`` track with configurable depth."""

    _track_id: str
    _total: int
    _label: str
    _depth: int
    _min_interval_s: float
    _step: int
    _last_published: int
    _last_time: float
    _started: bool
    _completed: bool
    _section: int | str | None

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
        self._track_id = track_id
        self._total = max(0, int(total))
        self._label = label
        self._depth = depth
        self._min_interval_s = min_interval_s
        self._step = max(1, self._total // 100) if self._total else 1
        self._last_published = -1
        self._last_time = 0.0
        self._started = False
        self._completed = False
        self._section = section

    def start(self) -> None:
        """Publish the initial 0/N progress event."""
        if self._total <= 0 or self._started:
            return
        self._started = True
        self._publish(0)

    def update(self, current: int, **fields: Any) -> None:
        """Publish progress when enough items or time have elapsed."""
        if self._total <= 0 or self._completed:
            return
        if not self._started:
            self.start()
        current = min(max(0, int(current)), self._total)
        now = time.time()
        if (
            current >= self._total
            or self._last_published < 0
            or current - self._last_published >= self._step
            or now - self._last_time >= self._min_interval_s
        ):
            self._publish(current, **fields)

    def complete(self) -> None:
        """Publish final progress then remove the track."""
        if self._completed or self._total <= 0:
            return
        if not self._started:
            self.start()
        self._completed = True
        self._publish(self._total)
        report_iterate_complete(self._track_id, self._total)

    def _publish(self, current: int, **fields: Any) -> None:
        self._last_published = current
        self._last_time = time.time()
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
