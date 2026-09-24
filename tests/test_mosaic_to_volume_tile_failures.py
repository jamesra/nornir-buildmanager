"""
A tile that fails to compose must not survive in the saved mosaic.

``MosaicToVolume`` composes each tile's mosaic-to-section transform with the
slice-to-volume transform. The per-tile results were collected under a bare
``except:`` that logged "Skipping %s" and then did nothing else.

It did not actually skip. ``ImageToTransform[imagename]`` still held the original
*section-space* transform, so a failure left that tile untouched while its
neighbours moved to volume space. The caller's ``if len(ImageToTransform) > 0``
guard could never notice, because no entry was ever removed, so the mixed-space
mosaic was written and stamped with a fresh checksum -- valid as far as every
later run is concerned, and therefore never rebuilt.

The failed tile is now removed and the batch raises, so nothing is written.
"""

from __future__ import annotations

import logging

import pytest

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.block import _gather_volume_space_tiles

SECTION_SPACE = 'section-space transform'


class _Task:
    """Stands in for a pool task carrying one tile's composition result."""

    def __init__(self, imagename: str, result=None, error: BaseException | None = None):
        self.imagename = imagename
        self._result = result
        self._error = error

    def wait_return(self):
        if self._error is not None:
            raise self._error
        return self._result


class _Mosaic:
    def __init__(self, tilenames):
        # Every tile starts out holding its original section-space transform.
        self.ImageToTransform = {name: SECTION_SPACE for name in tilenames}


def _gather(mosaic, tasks, logger=None):
    return _gather_volume_space_tiles(mosaic, tasks,
                                      mosaic_path='volume.mosaic',
                                      Logger=logger or logging.getLogger(__name__))


def test_all_tiles_composed_are_moved_to_volume_space():
    mosaic = _Mosaic(['a.png', 'b.png'])
    tasks = [_Task('a.png', result='volume a'), _Task('b.png', result='volume b')]

    _gather(mosaic, tasks)

    assert mosaic.ImageToTransform == {'a.png': 'volume a', 'b.png': 'volume b'}


def test_a_failed_tile_does_not_keep_its_section_space_transform():
    """The core defect: the failed tile silently stayed in section space."""
    mosaic = _Mosaic(['a.png', 'bad.png'])
    tasks = [_Task('a.png', result='volume a'),
             _Task('bad.png', error=ValueError('Unexpected transform types'))]

    with pytest.raises(NornirUserException):
        _gather(mosaic, tasks)

    assert 'bad.png' not in mosaic.ImageToTransform
    assert SECTION_SPACE not in mosaic.ImageToTransform.values()


def test_a_failed_tile_aborts_before_anything_is_saved():
    mosaic = _Mosaic(['a.png', 'bad.png'])
    tasks = [_Task('a.png', result='volume a'),
             _Task('bad.png', error=ValueError('Unexpected transform types'))]

    with pytest.raises(NornirUserException) as caught:
        _gather(mosaic, tasks)

    text = str(caught.value)
    assert '1 of 2 tiles' in text
    assert 'bad.png' in text
    assert 'volume.mosaic' in text


def test_every_failed_tile_is_named():
    mosaic = _Mosaic(['a.png', 'b.png', 'c.png'])
    tasks = [_Task('a.png', error=ValueError('boom')),
             _Task('b.png', result='volume b'),
             _Task('c.png', error=ValueError('boom'))]

    with pytest.raises(NornirUserException) as caught:
        _gather(mosaic, tasks)

    text = str(caught.value)
    assert 'a.png' in text and 'c.png' in text
    assert '2 of 3 tiles' in text


def test_all_tiles_failing_leaves_an_empty_mosaic():
    mosaic = _Mosaic(['a.png', 'b.png'])
    tasks = [_Task('a.png', error=ValueError('boom')),
             _Task('b.png', error=ValueError('boom'))]

    with pytest.raises(NornirUserException):
        _gather(mosaic, tasks)

    assert mosaic.ImageToTransform == {}


def test_failures_are_logged_at_error_with_the_cause(caplog):
    mosaic = _Mosaic(['bad.png'])
    tasks = [_Task('bad.png', error=ValueError('Unexpected transform types'))]

    with caplog.at_level(logging.ERROR):
        with pytest.raises(NornirUserException):
            _gather(mosaic, tasks)

    records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(records) == 1
    assert 'bad.png' in records[0].getMessage()
    # The original exception used to be discarded entirely.
    assert 'Unexpected transform types' in records[0].getMessage()


def test_keyboard_interrupt_is_not_swallowed():
    """The bare except also caught BaseException, so Ctrl-C looked like a bad tile."""
    mosaic = _Mosaic(['a.png'])
    tasks = [_Task('a.png', error=KeyboardInterrupt())]

    with pytest.raises(KeyboardInterrupt):
        _gather(mosaic, tasks)


def test_no_tiles_is_not_an_error():
    mosaic = _Mosaic([])

    _gather(mosaic, [])

    assert mosaic.ImageToTransform == {}
