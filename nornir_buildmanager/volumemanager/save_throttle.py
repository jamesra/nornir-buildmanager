"""Save some containers on a time checkpoint instead of after every pipeline step.

The pipeline saves whatever a stage returns or yields, and an ``Iterate`` runs the
stage once per element. Grid refine runs once per section mapping and changes the same
output StosGroup each time, so every pair rewrote that group's ``VolumeData.xml``.
That file grows with every pair, so the stage's save work grew with the square of the
pair count; on the NAS it was about a third of a Grid32 refine pair (srv, RC3,
2026-11-02).

A stage calls :func:`throttle_saves` on such a container. The pipeline's save step
(``PipelineManager._SaveNodes``) then asks :func:`save_due` before saving it, which is
true once ``NORNIR_METADATA_SAVE_INTERVAL_S`` seconds (default 30) have passed since its
last save. The pipeline calls :func:`flush` when an ``Iterate`` finishes and when it
exits, saving anything still pending.

If the process dies, at most one interval of metadata is lost. The files the stage
wrote are on disk, and the next run's checks redo work its metadata does not record
(for refine, a missing ``InputTransformChecksum`` means rebuild). ``0`` saves every time,
as before.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Any

SAVE_INTERVAL_ENV = 'NORNIR_METADATA_SAVE_INTERVAL_S'
DEFAULT_SAVE_INTERVAL_S = 30.0


def save_interval_seconds() -> float:
    """Seconds between checkpoint saves; a missing, invalid, or negative value gives the default."""
    raw = os.environ.get(SAVE_INTERVAL_ENV, '').strip()
    if not raw:
        return DEFAULT_SAVE_INTERVAL_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SAVE_INTERVAL_S
    return value if value >= 0 else DEFAULT_SAVE_INTERVAL_S


class SaveCheckpoint:
    """When one container was last saved and whether it has unsaved changes."""

    interval_s: float
    pending: bool
    _clock: Callable[[], float]
    _last_save: float

    def __init__(self, interval_s: float | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.interval_s = save_interval_seconds() if interval_s is None else interval_s
        self._clock = clock
        self._last_save = clock()
        self.pending = False

    def changed(self) -> bool:
        """Record an unsaved change; return True when it is time to save (then call :meth:`saved`)."""
        self.pending = True
        return self.interval_s <= 0 or self._clock() - self._last_save >= self.interval_s

    def saved(self) -> None:
        """Record that the pending changes were saved."""
        self._last_save = self._clock()
        self.pending = False


_lock = threading.Lock()
# id(container) -> (container, checkpoint). The container is held so its id stays valid.
_throttled: dict[int, tuple[Any, SaveCheckpoint]] = {}
_clock: Callable[[], float] = time.monotonic


def throttle_saves(container: Any) -> None:
    """Save *container* on the checkpoint from now until the next :func:`flush`."""
    with _lock:
        if id(container) not in _throttled:
            _throttled[id(container)] = (container, SaveCheckpoint(clock=_clock))


def save_target(node: Any) -> Any:
    """The container ``VolumeManager.Save(node)`` writes: *node* or its nearest ancestor with ``Save``."""
    while node is not None and not hasattr(node, 'Save'):
        node = getattr(node, 'Parent', None)
    return node


def save_due(node: Any) -> bool:
    """True when saving *node* now is due; a throttled container that is not due is marked pending."""
    target = save_target(node)
    with _lock:
        entry = _throttled.get(id(target))
    if entry is None or entry[0] is not target:
        return True
    return entry[1].changed()


def mark_saved(node: Any) -> None:
    """Record that *node*'s container was just saved."""
    target = save_target(node)
    with _lock:
        entry = _throttled.get(id(target))
    if entry is not None and entry[0] is target:
        entry[1].saved()


def flush(save: Callable[[Any], None]) -> None:
    """Save every throttled container with pending changes, then stop throttling them all."""
    with _lock:
        entries = list(_throttled.values())
        _throttled.clear()
    for container, checkpoint in entries:
        if checkpoint.pending:
            save(container)
