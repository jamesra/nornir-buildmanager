"""
``find`` and ``findall`` must agree about persisting what link resolution repaired.

The review filed this as "a read-only query writes the volume to disk: ``findall``
calls ``self.Save()`` while resolving links, sibling ``find`` deliberately does not"
and treated ``findall`` as the offender. Measuring it inverts that.

Resolving links is not itself a change. Swapping a ``*_Link`` stub for the loaded
element leaves every dirty flag clear (``_ReplaceChildElementInPlace`` does not mark
the container, and the wrap path explicitly clears ``_AttributesChanged``), so on a
healthy volume the guard is False and nothing is written:

    findall("Block")  xml rewritten? False   dir mtime changed? False
    find("Block")     xml rewritten? False   dir mtime changed? False

The flag is only set when resolution *removed* something -- a stub whose target file
is gone, or a child cleaned as invalid. Measured with Block1's directory deleted:

    findall  rewrote the xml, stale stub gone from disk, tree clean afterwards
    find     wrote nothing,   stale stub still on disk, tree left DIRTY

So ``findall`` was right and ``find`` was dropping the repair: it left disk holding a
stub that cannot load, and left an in-memory tree that no longer matched disk with
nothing responsible for flushing it. The next run retried the same failed load, and
any later unrelated Save would have written the removal at an arbitrary moment.

``find`` now carries the same guard, which is what the commented-out block at that
spot (``# TODO: This does not belong here, but I need to save validation information
updates.``) was reaching for.

Writing here does not disturb directory-modification-time validation.
``XResourceElementWrapper.NeedsValidation`` raises rather than using directory mtime
for containers with ``SaveAsLinkedElement`` set, and ``LevelNode`` sets it False
precisely so its own save cannot bump the directory it is watching.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from typing import cast
from xml.etree import ElementTree

import pytest

from nornir_buildmanager.volumemanager import (BlockNode, VolumeManager,
                                               XContainerElementWrapper)

BLOCK_NAMES = ['Block0', 'Block1', 'Block2']
VICTIM = 'Block1'


def _load(root: str, **kwargs) -> XContainerElementWrapper:
    volume = VolumeManager.Load(root, **kwargs)
    assert volume is not None, f'no volume loaded from {root}'
    return cast(XContainerElementWrapper, volume)


def _build(root: str):
    """A volume whose blocks are each their own linked container."""
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    volume = _load(root, Create=True)
    for name in BLOCK_NAMES:
        _, block = volume.UpdateOrAddChildByAttrib(BlockNode.Create(name), 'Name')
        os.makedirs(block.FullPath, exist_ok=True)
    VolumeManager.Save(volume)


def _snapshot(root: str) -> dict:
    xml_path = os.path.join(root, 'VolumeData.xml')
    with open(xml_path, 'rb') as handle:
        raw = handle.read()
    return {'mtime': os.path.getmtime(xml_path),
            'hash': hashlib.sha256(raw).hexdigest(),
            'dir_mtime': os.path.getmtime(root),
            'text': raw.decode('utf-8')}


@pytest.fixture
def volume_root():
    base = tempfile.mkdtemp(prefix='nornir_link_persist_')
    yield os.path.join(base, 'vol')
    shutil.rmtree(base, ignore_errors=True)


# Both accessors resolve links, so every guarantee below must hold for each.
ACCESSORS = [
    pytest.param(lambda volume: list(volume.findall('Block')), id='findall'),
    pytest.param(lambda volume: volume.find('Block'), id='find'),
]


def _prepare(root: str, break_victim: bool):
    _build(root)
    if break_victim:
        shutil.rmtree(os.path.join(root, VICTIM), ignore_errors=True)
    # Filesystem timestamps here are coarse; make any rewrite unambiguous.
    time.sleep(1.1)


# --- a healthy volume is not written by a read --------------------------------

@pytest.mark.parametrize('accessor', ACCESSORS)
def test_healthy_volume_is_not_rewritten(volume_root, accessor):
    _prepare(volume_root, break_victim=False)
    volume = _load(volume_root, UseCache=False)
    before = _snapshot(volume_root)

    accessor(volume)

    after = _snapshot(volume_root)
    assert after['mtime'] == before['mtime']
    assert after['hash'] == before['hash']


@pytest.mark.parametrize('accessor', ACCESSORS)
def test_healthy_volume_directory_mtime_is_untouched(volume_root, accessor):
    """Directory mtime feeds validation, so a read must not bump it."""
    _prepare(volume_root, break_victim=False)
    volume = _load(volume_root, UseCache=False)
    before = _snapshot(volume_root)

    accessor(volume)

    assert _snapshot(volume_root)['dir_mtime'] == before['dir_mtime']


@pytest.mark.parametrize('accessor', ACCESSORS)
def test_resolving_links_does_not_dirty_a_healthy_volume(volume_root, accessor):
    """The guard's premise: link resolution alone is not a change."""
    _prepare(volume_root, break_victim=False)
    volume = _load(volume_root, UseCache=False)

    accessor(volume)

    assert volume.ElementHasChangesToSave is False


# --- a repair is persisted -----------------------------------------------------

@pytest.mark.parametrize('accessor', ACCESSORS)
def test_stale_stub_is_removed_from_disk(volume_root, accessor):
    """find used to leave the unloadable stub on disk."""
    _prepare(volume_root, break_victim=True)
    volume = _load(volume_root, UseCache=False)
    assert VICTIM in _snapshot(volume_root)['text']

    accessor(volume)

    assert VICTIM not in _snapshot(volume_root)['text']


@pytest.mark.parametrize('accessor', ACCESSORS)
def test_tree_is_not_left_dirty_after_a_repair(volume_root, accessor):
    """A dirty tree with nothing responsible for flushing it is the real hazard."""
    _prepare(volume_root, break_victim=True)
    volume = _load(volume_root, UseCache=False)

    accessor(volume)

    assert volume.ElementHasChangesToSave is False


@pytest.mark.parametrize('accessor', ACCESSORS)
def test_surviving_blocks_are_kept(volume_root, accessor):
    """Persisting the repair must not drop the healthy siblings."""
    _prepare(volume_root, break_victim=True)
    volume = _load(volume_root, UseCache=False)

    accessor(volume)

    text = _snapshot(volume_root)['text']
    for name in BLOCK_NAMES:
        if name != VICTIM:
            assert name in text, f'{name} was lost'


@pytest.mark.parametrize('accessor', ACCESSORS)
def test_a_second_read_finds_nothing_left_to_repair(volume_root, accessor):
    """Once persisted, the next run must not retry the same failed load."""
    _prepare(volume_root, break_victim=True)
    accessor(_load(volume_root, UseCache=False))
    settled = _snapshot(volume_root)
    time.sleep(1.1)

    accessor(_load(volume_root, UseCache=False))

    after = _snapshot(volume_root)
    assert after['hash'] == settled['hash']
    assert after['mtime'] == settled['mtime'], 'second read rewrote an already-clean volume'


# --- the two accessors agree ---------------------------------------------------

def _persisted_block_names(root: str) -> list[str]:
    """Block names surviving in the saved xml, ignoring the volume's own path."""
    tree = ElementTree.parse(os.path.join(root, 'VolumeData.xml'))
    return sorted(child.attrib['Name'] for child in tree.getroot())


def test_find_and_findall_leave_the_same_volume_on_disk(volume_root):
    """The inconsistency the finding identified, resolved toward persisting.

    Compares the surviving children rather than the raw text, since the two
    volumes live at different paths and carry that in their own attributes.
    """
    root_findall = volume_root + '_findall'
    root_find = volume_root + '_find'

    _prepare(root_findall, break_victim=True)
    list(_load(root_findall, UseCache=False).findall('Block'))

    _prepare(root_find, break_victim=True)
    _load(root_find, UseCache=False).find('Block')

    assert _persisted_block_names(root_findall) == _persisted_block_names(root_find)
    assert VICTIM not in _persisted_block_names(root_find)
