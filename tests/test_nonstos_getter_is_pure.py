"""
Reading BlockNode.NonStosSectionNumbers must not modify the block.

The getter used to rewrite the child's text into canonical order and set
``_AttributesChanged`` mid-read, under a comment describing it as a temporary
migration for legacy metadata that "can be deleted after running an align on
each legacy volume". Measured before the change, with the stored text varied:

    stored '3,1,2'    -> value [1, 2, 3], block dirty after read: True
    stored '1,2,3'    -> value [1, 2, 3], block dirty after read: False
    stored '1, 2, 3'  -> value [1, 2, 3], block dirty after read: True

So merely reading a legacy block dirtied it, and stray whitespace was enough to
trigger it too. Since the block then saves, a query rewrote the volume.

Removing the rewrite is safe because nothing depends on the stored ordering:

- the getter sorts as it parses, so the returned set is canonical either way;
- the setter already writes the canonical form, so anything that actually
  changes the value normalises it on the way out;
- the only consumer that reads the raw node text rather than the property,
  ``StosMapNode.IsValid`` at ``stosmapnode.py:211``, parses it with
  ``ListFromAttribute`` and uses it purely for membership.

Legacy volumes therefore keep their unsorted text on disk until something
genuinely writes the value, which is the correct time to normalise it.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import cast

import pytest

from nornir_buildmanager.volumemanager import (BlockNode, VolumeManager,
                                               XContainerElementWrapper)

CANONICAL = '1,2,3'
EXPECTED = frozenset([1, 2, 3])


def _load(root: str, **kwargs) -> XContainerElementWrapper:
    volume = VolumeManager.Load(root, **kwargs)
    assert volume is not None, f'no volume loaded from {root}'
    return cast(XContainerElementWrapper, volume)


@pytest.fixture
def make_block():
    """Builds a volume, forces the stored text, and reloads it clean."""
    base = tempfile.mkdtemp(prefix='nornir_nonstos_')
    created = {}

    def build(stored_text: str):
        root = os.path.join(base, f'vol{len(created)}')
        os.makedirs(root, exist_ok=True)
        volume = _load(root, Create=True)
        _, block = volume.UpdateOrAddChildByAttrib(BlockNode.Create('Block'), 'Name')
        os.makedirs(block.FullPath, exist_ok=True)
        block.NonStosSectionNumbers = [1, 2, 3]
        VolumeManager.Save(volume)

        # Rewrite on disk to whatever legacy form this test wants.
        for dirpath, _, files in os.walk(root):
            if 'VolumeData.xml' not in files:
                continue
            path = os.path.join(dirpath, 'VolumeData.xml')
            with open(path, encoding='utf-8') as handle:
                body = handle.read()
            if f'>{CANONICAL}<' in body:
                with open(path, 'w', encoding='utf-8') as handle:
                    handle.write(body.replace(f'>{CANONICAL}<', f'>{stored_text}<'))
                created[root] = path
                break
        else:
            pytest.fail('did not find the stored NonStosSectionNumbers text')

        reloaded = _load(root, UseCache=False)
        block = reloaded.find('Block')
        assert block is not None
        assert block.ElementHasChangesToSave is False, 'fixture must start clean'
        return reloaded, block, created[root]

    yield build
    shutil.rmtree(base, ignore_errors=True)


def _stored_text(path: str) -> str:
    with open(path, encoding='utf-8') as handle:
        body = handle.read()
    start = body.find('<NonStosSectionNumbers')
    assert start >= 0, 'node missing from disk'
    start = body.index('>', start) + 1
    return body[start:body.index('</NonStosSectionNumbers>', start)]


# The legacy forms that used to trigger the mid-read rewrite.
STORED_FORMS = ['3,1,2', '1, 2, 3', '2,3,1', CANONICAL]


@pytest.mark.parametrize('stored', STORED_FORMS)
def test_read_does_not_dirty_the_block(make_block, stored):
    _, block, _ = make_block(stored)

    block.NonStosSectionNumbers

    assert block.ElementHasChangesToSave is False


@pytest.mark.parametrize('stored', STORED_FORMS)
def test_read_returns_the_sorted_set(make_block, stored):
    """Ordering of the returned value never depended on the stored text."""
    _, block, _ = make_block(stored)

    assert block.NonStosSectionNumbers == EXPECTED


@pytest.mark.parametrize('stored', STORED_FORMS)
def test_read_then_save_leaves_the_stored_text_alone(make_block, stored):
    """A query must not rewrite the volume."""
    volume, block, path = make_block(stored)

    block.NonStosSectionNumbers
    VolumeManager.Save(volume)

    assert _stored_text(path) == stored


def test_repeated_reads_are_stable(make_block):
    _, block, _ = make_block('3,1,2')

    first = block.NonStosSectionNumbers
    second = block.NonStosSectionNumbers

    assert first == second == EXPECTED
    assert block.ElementHasChangesToSave is False


# --- writing still canonicalises ----------------------------------------------

def test_setter_writes_canonical_text(make_block):
    """Normalising on write is what makes dropping the read-time fix safe."""
    volume, block, path = make_block('3,1,2')

    block.NonStosSectionNumbers = [3, 1, 2]
    VolumeManager.Save(volume)

    assert _stored_text(path) == CANONICAL


def test_mark_damaged_canonicalises_legacy_text(make_block):
    """The mutating helpers go through the setter, so they normalise too."""
    volume, block, path = make_block('3,1,2')

    block.MarkSectionsAsDamaged([4])
    VolumeManager.Save(volume)

    assert _stored_text(path) == '1,2,3,4'
    assert block.NonStosSectionNumbers == frozenset([1, 2, 3, 4])


def test_mark_undamaged_removes_from_legacy_text(make_block):
    volume, block, path = make_block('3,1,2')

    block.MarkSectionsAsUndamaged([2])
    VolumeManager.Save(volume)

    assert _stored_text(path) == '1,3'
    assert block.NonStosSectionNumbers == frozenset([1, 3])
