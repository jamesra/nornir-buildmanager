"""Per-pair stages save their output container on a time checkpoint, flushed by the pipeline.

Grid refine runs once per section mapping, and each run used to rewrite the whole output
StosGroup ``VolumeData.xml``. That file grows with every pair (srv RC3 Grid32, 2026-11-02).
"""
from __future__ import annotations

import pytest
from nornir_buildmanager import pipelinemanager
from nornir_buildmanager.volumemanager import save_throttle
from nornir_buildmanager.volumemanager.save_throttle import SaveCheckpoint


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Container:
    """Stands in for a container: has Save. Children reach it through Parent."""

    def __init__(self) -> None:
        self.saves = 0

    def Save(self, tabLevel=None) -> None:
        self.saves += 1


class _Child:
    def __init__(self, parent: _Container) -> None:
        self.Parent = parent


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(save_throttle, '_clock', fake)
    monkeypatch.setattr(save_throttle, '_throttled', {})
    monkeypatch.setenv(save_throttle.SAVE_INTERVAL_ENV, '30')
    return fake


class TestSaveCheckpoint:

    def test_due_once_per_interval(self):
        clock = _Clock()
        checkpoint = SaveCheckpoint(interval_s=30, clock=clock)
        due = []
        for i in range(100):
            clock.now += 4
            if checkpoint.changed():
                checkpoint.saved()
                due.append(i)

        assert due == [7, 15, 23, 31, 39, 47, 55, 63, 71, 79, 87, 95]
        assert checkpoint.pending

    def test_zero_interval_is_always_due(self):
        checkpoint = SaveCheckpoint(interval_s=0, clock=_Clock())

        assert all(checkpoint.changed() for _ in range(3))

    @pytest.mark.parametrize('raw, expected', [
        (None, 30.0), ('', 30.0), ('10', 10.0), ('0', 0.0), ('2.5', 2.5), ('-1', 30.0), ('soon', 30.0),
    ])
    def test_interval_from_environment(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv(save_throttle.SAVE_INTERVAL_ENV, raising=False)
        else:
            monkeypatch.setenv(save_throttle.SAVE_INTERVAL_ENV, raw)

        assert save_throttle.save_interval_seconds() == expected


class TestPipelineSaves:
    """``PipelineManager._SaveNodes`` is the save step every stage yield goes through."""

    def test_unthrottled_containers_save_every_time(self, clock):
        group = _Container()
        for _ in range(5):
            pipelinemanager.PipelineManager._SaveNodes(iter([_Child(group)]))

        assert group.saves == 5

    def test_throttled_container_saves_on_the_checkpoint_and_at_flush(self, clock):
        group = _Container()
        save_throttle.throttle_saves(group)
        for _ in range(20):  # 20 pairs, 4 s each
            clock.now += 4
            save_throttle.throttle_saves(group)  # every refine call registers again
            pipelinemanager.PipelineManager._SaveNodes(iter([_Child(group)]))

        assert group.saves == 2, 'saved at 32 s and 64 s'

        pipelinemanager.PipelineManager._FlushThrottledSaves()
        assert group.saves == 3, 'the pairs after the last checkpoint are saved at the flush'

    def test_flush_ends_throttling(self, clock):
        group = _Container()
        save_throttle.throttle_saves(group)
        pipelinemanager.PipelineManager._FlushThrottledSaves()

        pipelinemanager.PipelineManager._SaveNodes(_Child(group))
        assert group.saves == 1

    def test_flush_without_pending_changes_does_not_save(self, clock):
        group = _Container()
        save_throttle.throttle_saves(group)

        pipelinemanager.PipelineManager._FlushThrottledSaves()

        assert group.saves == 0

    def test_zero_interval_restores_saving_every_pair(self, clock, monkeypatch):
        monkeypatch.setenv(save_throttle.SAVE_INTERVAL_ENV, '0')
        group = _Container()
        save_throttle.throttle_saves(group)
        for _ in range(4):
            pipelinemanager.PipelineManager._SaveNodes(iter([_Child(group)]))

        assert group.saves == 4

    def test_other_containers_are_not_throttled(self, clock):
        group, block = _Container(), _Container()
        save_throttle.throttle_saves(group)
        pipelinemanager.PipelineManager._SaveNodes(iter([_Child(group), block]))

        assert (group.saves, block.saves) == (0, 1)
