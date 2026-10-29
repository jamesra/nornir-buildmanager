"""Characterize the buildmanager environment flags that share the on/off truthy rule.

``NORNIR_LOG_PIPELINE_PROGRESS``, ``NORNIR_SKIP_MOSAIC_PLOTS`` and the two metadata-port
switches all accept ``1``/``true``/``yes``/``on`` after stripping whitespace and ignoring
case; everything else, including unset, ``y`` and ``2``, is off. Each site is pinned here
over the same token table so the rule cannot drift at one of them.
"""

from __future__ import annotations

import nornir_buildmanager.pipelinemanager as pm
import nornir_imageregistration.headless as ir_headless
import pytest
from nornir_buildmanager.metadata import feature_flags
from nornir_buildmanager.operations import diagnostics

TOKENS: list[tuple[str | None, bool]] = [
    ('1', True), ('true', True), ('yes', True), ('on', True),
    ('TRUE', True), (' Yes ', True), ('ON', True), ('\tOn\n', True),
    ('0', False), ('false', False), ('no', False), ('off', False),
    ('', False), (None, False), ('y', False), ('2', False), ('enabled', False),
]

SKIP_MOSAIC_PLOTS_ENV = 'NORNIR_SKIP_MOSAIC_PLOTS'


def _set_flag(monkeypatch: pytest.MonkeyPatch, name: str, value: str | None) -> None:
    """Set *name* to *value*, or remove it when *value* is None."""
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize('value, expected', TOKENS)
def test_pipeline_progress_flag(monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool) -> None:
    _set_flag(monkeypatch, pm._PIPELINE_PROGRESS_ENV, value)
    assert pm._should_log_pipeline_progress() is expected


@pytest.mark.parametrize('value, expected', TOKENS)
def test_shadow_sqlite_flag(monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool) -> None:
    _set_flag(monkeypatch, feature_flags.SHADOW_SQLITE_ENV, value)
    assert feature_flags.shadow_sqlite_enabled() is expected


@pytest.mark.parametrize('value, expected', TOKENS)
def test_read_sqlite_flag(monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool) -> None:
    _set_flag(monkeypatch, feature_flags.READ_SQLITE_ENV, value)
    assert feature_flags.read_sqlite_enabled() is expected


class _TransformLookupReached(Exception):
    """Raised by the stub channel when PlotMosaicOverlaps gets past its skip checks."""


class _StubChannel:
    """Channel node whose transform lookup proves the plot was not skipped."""

    FullPath = 'stub-channel'

    def GetTransform(self, transform_name: str) -> None:
        raise _TransformLookupReached(transform_name)


@pytest.mark.parametrize('value, skipped', TOKENS)
def test_skip_mosaic_plots_flag(monkeypatch: pytest.MonkeyPatch, value: str | None, skipped: bool) -> None:
    monkeypatch.setattr(ir_headless, 'is_headless', lambda: False)
    _set_flag(monkeypatch, SKIP_MOSAIC_PLOTS_ENV, value)
    channel = _StubChannel()
    if skipped:
        assert diagnostics.PlotMosaicOverlaps(channel, 'Grid', 'overlaps') is None
    else:
        with pytest.raises(_TransformLookupReached):
            diagnostics.PlotMosaicOverlaps(channel, 'Grid', 'overlaps')


def test_headless_skips_mosaic_plots_before_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ir_headless, 'is_headless', lambda: True)
    monkeypatch.delenv(SKIP_MOSAIC_PLOTS_ENV, raising=False)
    assert diagnostics.PlotMosaicOverlaps(_StubChannel(), 'Grid', 'overlaps') is None
