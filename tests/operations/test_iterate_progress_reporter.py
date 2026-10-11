"""Characterization tests for the throttled ``IterateProgressReporter``.

A small reference model of the throttle (start / update / complete) is compared
against the real reporter on Hypothesis-generated call sequences, with
``time.time`` pinned per call so the time-based throttle is deterministic.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any
from unittest import mock

from hypothesis import example, given, settings
from hypothesis import strategies as st
from nornir_buildmanager.operations.segmentationtraining import progress

_TRACK_ID = "unit:track"
_LABEL = "Unit track"
_DEPTH = 2


@dataclass
class _Op:
    """One call on the reporter at a fixed wall-clock time."""

    kind: str
    at: float
    current: int = 0
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Model:
    """Reference model of the progress throttle and the events it emits."""

    total: int
    min_interval_s: float
    section: int | str | None
    started: bool = False
    completed: bool = False
    last_published: int = -1
    last_time: float = 0.0
    events: list[tuple[Any, ...]] = field(default_factory=list)

    @property
    def step(self) -> int:
        """Item step that forces a publish (1% of total, at least 1)."""
        return max(1, self.total // 100) if self.total > 0 else 1

    def _pub(self, current: int, at: float, fields: dict[str, Any]) -> None:
        self.last_published = current
        self.last_time = at
        extra = dict(fields)
        if self.section is not None and "section" not in extra:
            extra["section"] = self.section
        self.events.append(("progress", current, self.total, sorted(extra.items())))

    def start(self, at: float) -> None:
        """Model ``start``."""
        if self.total <= 0 or self.started:
            return
        self.started = True
        self._pub(0, at, {})

    def update(self, op: _Op) -> None:
        """Model ``update``."""
        if self.total <= 0 or self.completed:
            return
        if not self.started:
            self.start(op.at)
        current = min(max(0, op.current), self.total)
        if (current >= self.total
                or self.last_published < 0
                or current - self.last_published >= self.step
                or op.at - self.last_time >= self.min_interval_s):
            self._pub(current, op.at, op.fields)

    def complete(self, at: float) -> None:
        """Model ``complete`` (starts first when never started)."""
        if self.completed or self.total <= 0:
            return
        if not self.started:
            self.start(at)
        self.completed = True
        self._pub(self.total, at, {})
        self.events.append(("complete", self.total))


def _run_real(total: int, min_interval_s: float, section: int | str | None,
              ops: list[_Op]) -> list[tuple[Any, ...]]:
    """Drive a real reporter and return the recorded publish events."""
    events: list[tuple[Any, ...]] = []
    clock = [0.0]

    def fake_iterate(track_id: str, current: int, total_: int, label: str,
                     depth: int = 0, **extra: Any) -> None:
        assert track_id == _TRACK_ID
        assert label == _LABEL
        assert depth == _DEPTH
        events.append(("progress", current, total_, sorted(extra.items())))

    def fake_complete(track_id: str, total_: int) -> None:
        assert track_id == _TRACK_ID
        events.append(("complete", total_))

    with mock.patch.object(progress, "report_iterate", side_effect=fake_iterate), \
            mock.patch.object(progress, "report_iterate_complete", side_effect=fake_complete), \
            mock.patch("time.time", side_effect=lambda: clock[0]):
        reporter = progress.IterateProgressReporter(
            _TRACK_ID, total, label=_LABEL, depth=_DEPTH,
            min_interval_s=min_interval_s, section=section)
        for op in ops:
            clock[0] = op.at
            if op.kind == "start":
                reporter.start()
            elif op.kind == "update":
                reporter.update(op.current, **op.fields)
            else:
                reporter.complete()
    return events


def _run_model(total: int, min_interval_s: float, section: int | str | None,
               ops: list[_Op]) -> list[tuple[Any, ...]]:
    """Drive the reference model and return its events."""
    model = _Model(total=max(0, total), min_interval_s=min_interval_s, section=section)
    for op in ops:
        if op.kind == "start":
            model.start(op.at)
        elif op.kind == "update":
            model.update(op)
        else:
            model.complete(op.at)
    return model.events


_FIELDS = st.sampled_from([{}, {"element": "e1"}, {"section": 3}, {"element": "e2", "section": "s"}])


@st.composite
def _op_sequences(draw: st.DrawFn) -> list[_Op]:
    """Generate call sequences with non-decreasing timestamps."""
    n = draw(st.integers(min_value=0, max_value=40))
    at = 1000.0
    ops: list[_Op] = []
    for _ in range(n):
        at += draw(st.sampled_from([0.0, 0.01, 0.1, 0.3, 2.0]))
        kind = draw(st.sampled_from(["start", "update", "update", "update", "complete"]))
        ops.append(_Op(kind=kind, at=at,
                       current=draw(st.integers(min_value=-5, max_value=1100)),
                       fields=draw(_FIELDS)))
    return ops


class TestIterateProgressReporterThrottle(unittest.TestCase):
    """Pin the publish sequence of ``IterateProgressReporter``."""

    @settings(max_examples=300, deadline=None)
    @given(
        total=st.integers(min_value=-3, max_value=1000),
        min_interval_s=st.sampled_from([0.0, 0.25, 1.0]),
        section=st.sampled_from([None, 12]),
        ops=_op_sequences(),
    )
    @example(total=0, min_interval_s=0.25, section=None,
             ops=[_Op("update", 1.0, 1), _Op("complete", 2.0)])
    @example(total=10, min_interval_s=0.25, section=None, ops=[_Op("complete", 1.0)])
    def test_matches_reference_model(self, total: int, min_interval_s: float,
                                     section: int | str | None, ops: list[_Op]) -> None:
        """Publish events match the reference throttle model."""
        self.assertEqual(_run_model(total, min_interval_s, section, ops),
                         _run_real(total, min_interval_s, section, ops))

    def test_complete_before_start_starts_first(self) -> None:
        """``complete`` on a never-started reporter publishes 0, total, then complete."""
        self.assertEqual([
            ("progress", 0, 5, []),
            ("progress", 5, 5, []),
            ("complete", 5),
        ], _run_real(5, 0.25, None, [_Op("complete", 1.0)]))

    def test_update_fields_and_section_merge(self) -> None:
        """Update fields are forwarded; the reporter section fills in unless overridden."""
        events = _run_real(200, 10.0, 4, [
            _Op("update", 1.0, 2, {"element": "a"}),
            _Op("update", 1.0, 4, {"section": 9}),
        ])
        self.assertEqual([
            ("progress", 0, 200, [("section", 4)]),
            ("progress", 2, 200, [("element", "a"), ("section", 4)]),
            ("progress", 4, 200, [("section", 9)]),
        ], events)


if __name__ == "__main__":
    unittest.main()
