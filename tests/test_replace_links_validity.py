"""
The multi-link branch of ``_replace_links`` must validate and clean the element the
validity task actually belongs to.

``xcontainerelementwrapper.py:281-287`` read:

    for clean_task in concurrent.futures.as_completed(clean_tasks):
        IsValid = clean_task.result()

        if IsValid:
            loaded_elements.append(wrapped_loaded_element)
        else:
            wrapped_loaded_element.CleanIfInvalid()

Two defects, and they interact:

* ``wrapped_loaded_element`` is never rebound here. It holds whatever the *previous*
  loop left, so the results list got that one element repeated once per task, and a
  clean would have hit it instead of the invalid one. (#49)
* ``IsValid()`` returns ``tuple[bool, str]``. Testing the tuple is always truthy, so
  the ``else`` never ran and invalid linked containers were never cleaned. (#50)

The second masks the first: because the clean branch was dead, the wrong-element
clean never fired. Measured with elements A (valid), B (invalid), C (valid):

    SHIPPED:          returned [C, C, C]; clean calls {A:0, B:0, C:0}
    tuple test fixed: returned [C, C];    clean calls {A:0, B:0, C:1}   <-- cleans valid C
    both fixed:       returned [A, C];    clean calls {A:0, B:1, C:0}

So repairing the tuple test alone would have started deleting *valid* containers,
and ``Clean`` removes files from disk, not just tree nodes. They have to be fixed
together.

Cleaning invalid containers here is the intended behaviour, not new behaviour: the
single-link path (``_replace_link``, same file, ~line 205) has always called
``CleanIfInvalid``, and ``_replace_links`` delegates to it when there is exactly one
link. Only the 2-or-more path silently skipped it.

Elements whose ``NeedsValidation`` is False were also dropped from the returned list
entirely, since only the validity loop appended.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import cast

import pytest

from nornir_buildmanager.volumemanager import (BlockNode, VolumeManager,
                                               XContainerElementWrapper)


def _load(root: str, **kwargs) -> XContainerElementWrapper:
    """VolumeManager.Load is typed as optional; every use here requires a volume."""
    volume = VolumeManager.Load(root, **kwargs)
    assert volume is not None, f'no volume loaded from {root}'
    # The volume root is a container; Load is annotated with the wider base type.
    return cast(XContainerElementWrapper, volume)


def _build_volume(root: str, block_names: list[str]):
    """A volume whose blocks are each saved as their own linked container."""
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)

    volume = _load(root, Create=True)
    for name in block_names:
        _, block = volume.UpdateOrAddChildByAttrib(BlockNode.Create(name), 'Name')
        os.makedirs(block.FullPath, exist_ok=True)
    VolumeManager.Save(volume)
    return volume


@pytest.fixture
def volume_root():
    root = os.path.join(tempfile.mkdtemp(prefix='nornir_replace_links_'), 'vol')
    yield root
    shutil.rmtree(os.path.dirname(root), ignore_errors=True)


class _Recorder:
    """Controls which blocks report valid, and records every clean."""

    def __init__(self, monkeypatch, invalid_names: set[str]):
        self.invalid_names = invalid_names
        self.cleaned: list[str] = []

        recorder = self

        def is_valid(block_self):
            name = block_self.attrib.get('Name')
            if name in recorder.invalid_names:
                return False, f'{name} marked invalid by test'
            return True, ''

        def clean(block_self, reason=None):
            recorder.cleaned.append(block_self.attrib.get('Name'))
            # Do not touch the filesystem; the tree removal is what callers observe.
            parent = block_self.Parent
            if parent is not None:
                try:
                    parent.remove(block_self)
                except Exception:
                    pass

        monkeypatch.setattr(BlockNode, 'IsValid', is_valid, raising=False)
        monkeypatch.setattr(BlockNode, 'Clean', clean, raising=False)


def _resolve_links(root: str, monkeypatch, invalid_names: set[str]):
    """Load the volume and run the real multi-link _replace_links over its links."""
    recorder = _Recorder(monkeypatch, invalid_names)

    volume = _load(root, UseCache=False)
    assert volume is not None
    link_nodes = [child for child in list(volume) if child.tag.endswith('_Link')]
    assert len(link_nodes) >= 2, 'need the multi-link branch, not the single-link one'

    loaded = volume._replace_links(link_nodes)
    return volume, loaded, recorder, link_nodes


def _names(elements) -> list[str]:
    return sorted(e.attrib.get('Name') for e in elements if e is not None)


# --- #50: invalid containers must actually be cleaned --------------------------

def test_invalid_container_is_cleaned(volume_root, monkeypatch):
    _build_volume(volume_root, ['Block0', 'Block1', 'Block2'])

    _, _, recorder, _ = _resolve_links(volume_root, monkeypatch, {'Block1'})

    assert recorder.cleaned == ['Block1'], recorder.cleaned


def test_invalid_container_is_not_returned(volume_root, monkeypatch):
    _build_volume(volume_root, ['Block0', 'Block1', 'Block2'])

    _, loaded, _, _ = _resolve_links(volume_root, monkeypatch, {'Block1'})

    assert 'Block1' not in _names(loaded)


# --- #49: the right element must be the one validated and cleaned -------------

def test_valid_containers_are_never_cleaned(volume_root, monkeypatch):
    """Repairing only the tuple test cleaned a valid container instead."""
    _build_volume(volume_root, ['Block0', 'Block1', 'Block2'])

    _, _, recorder, _ = _resolve_links(volume_root, monkeypatch, {'Block1'})

    assert 'Block0' not in recorder.cleaned
    assert 'Block2' not in recorder.cleaned


def test_every_valid_container_is_returned_once(volume_root, monkeypatch):
    """The results list used to be one element repeated once per task."""
    _build_volume(volume_root, ['Block0', 'Block1', 'Block2'])

    _, loaded, _, _ = _resolve_links(volume_root, monkeypatch, {'Block1'})

    assert _names(loaded) == ['Block0', 'Block2']


def test_all_valid_returns_all_of_them(volume_root, monkeypatch):
    _build_volume(volume_root, ['Block0', 'Block1', 'Block2'])

    _, loaded, recorder, _ = _resolve_links(volume_root, monkeypatch, set())

    assert _names(loaded) == ['Block0', 'Block1', 'Block2']
    assert recorder.cleaned == []


def test_no_duplicates_in_the_result(volume_root, monkeypatch):
    _build_volume(volume_root, ['Block0', 'Block1', 'Block2', 'Block3'])

    _, loaded, _, _ = _resolve_links(volume_root, monkeypatch, set())

    names = [e.attrib.get('Name') for e in loaded if e is not None]
    assert len(names) == len(set(names)), names


def test_several_invalid_containers_are_each_cleaned(volume_root, monkeypatch):
    _build_volume(volume_root, ['Block0', 'Block1', 'Block2', 'Block3'])

    _, loaded, recorder, _ = _resolve_links(
        volume_root, monkeypatch, {'Block1', 'Block3'})

    assert sorted(recorder.cleaned) == ['Block1', 'Block3']
    assert _names(loaded) == ['Block0', 'Block2']


# --- error reporting names the right file --------------------------------------

def test_io_error_names_the_missing_path(volume_root, monkeypatch, capsys):
    """The submit loop rebound `fullpath`, so messages named the last path."""
    _build_volume(volume_root, ['Block0', 'Block1', 'Block2'])
    shutil.rmtree(os.path.join(volume_root, 'Block1'), ignore_errors=True)

    _resolve_links(volume_root, monkeypatch, set())

    output = capsys.readouterr()
    combined = output.out + output.err
    assert 'Block1' in combined, combined


# --- the single-link path already behaved this way -----------------------------

def test_single_link_path_also_cleans(volume_root, monkeypatch):
    """Pins the consistency argument: one link has always cleaned when invalid."""
    _build_volume(volume_root, ['Block0'])

    recorder = _Recorder(monkeypatch, {'Block0'})
    volume = _load(volume_root, UseCache=False)
    assert volume is not None
    link_nodes = [child for child in list(volume) if child.tag.endswith('_Link')]
    assert len(link_nodes) == 1

    loaded = volume._replace_links(link_nodes)

    assert recorder.cleaned == ['Block0']
    assert _names(loaded) == []
