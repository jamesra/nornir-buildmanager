"""Thin wrappers for dashboard ``iterate_progress`` MQTT tracks.

Build stages should prefer this helper over calling ``publish_run_event``
directly so track fields stay consistent across Import, Assemble, and block
STOS operations.
"""
from __future__ import annotations

from typing import Any

from nornir_shared.mqtt_telemetry import publish_run_event


def report_iterate(
    track_id: str,
    current: int,
    total: int,
    label: str,
    depth: int = 0,
    **fields: Any,
) -> None:
    """Publish an ``iterate_progress`` event for a named progress track.

    Parameters
    ----------
    track_id:
        Stable key stored in the dashboard ``progress_tracks`` map.
    current:
        Units completed so far (0 … total).
    total:
        Total units for this track; callers should skip publish when ``total`` is 0.
    label:
        Human-readable label shown next to the progress bar.
    depth:
        Nesting depth (0 = outermost / preferred sidebar track).
    **fields:
        Optional extras such as ``section`` or ``element``.
    """
    if total <= 0:
        return
    publish_run_event(
        "iterate_progress",
        current=current,
        total=total,
        depth=depth,
        track_id=track_id,
        label=label,
        **fields,
    )


def report_iterate_complete(track_id: str, total: int) -> None:
    """Publish ``iterate_progress_complete`` so the dashboard removes a track."""
    publish_run_event(
        "iterate_progress_complete",
        track_id=str(track_id),
        total=int(total),
    )
