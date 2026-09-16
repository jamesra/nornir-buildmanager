"""
Which child replacements count as an edit, and which do not.

``_ReplaceChildElementInPlace`` swaps via ``self[i] = new`` rather than
append/remove, so it never sets ``_ChildrenChanged``. The review filed that as a
bug on two grounds. They come apart under measurement.

**The shared helper must not set the flag.** Every live caller substitutes a node
for its own loaded or wrapped equivalent -- ``_replace_link`` and
``_replace_links`` swap a ``*_Link`` stub for the container it points at
(``xcontainerelementwrapper.py:202,274``), and ``_ReplaceChildIfUnwrapped`` swaps
a raw Element for its wrapper (``xelementwrapper.py:809``, which then explicitly
clears ``_AttributesChanged`` for the same reason). None of those change what
belongs on disk. Setting the flag there was tried, and it breaks reads: six tests
in ``test_link_resolution_persistence.py`` fail, all of them the guarantees that a
healthy volume is not rewritten and its directory mtimes are not bumped by a
query. That would reintroduce exactly the harm class behind the neighbouring
findings on read-time writes.

**The structural caller should set it.** ``ReplaceChildWithLink`` genuinely
restructures the parent: the container's content stops being part of the parent's
XML and becomes a stub pointing elsewhere. That has to be persisted. It has no
callers today anywhere in nornir-buildmanager, so it was a latent trap rather
than an active bug, and correcting it costs nothing.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import cast
from xml.etree import ElementTree

import pytest

from nornir_buildmanager.volumemanager import (BlockNode, VolumeManager,
                                               XContainerElementWrapper,
                                               XElementWrapper)

BLOCK_NAMES = ['Block0', 'Block1']


def test_wrap_preserves_mixed_child_order_and_serialization():
    attrib = {'CreationDate': '2026-01-01 00:00:00', 'Version': '1.0'}
    raw = ElementTree.Element('Root', attrib)
    item = ElementTree.SubElement(raw, 'Item', {**attrib, 'Name': 'raw'})
    ElementTree.SubElement(item, 'Leaf', attrib)
    link = ElementTree.SubElement(raw, 'Block_Link', {**attrib, 'Path': 'Block0'})
    existing = XElementWrapper('Existing', attrib={**attrib, 'Name': 'wrapped'})
    ElementTree.Element.append(raw, existing)
    expected = ElementTree.tostring(raw)

    wrapped = XElementWrapper.wrap(raw)

    assert ElementTree.tostring(wrapped) == expected
    assert [child.tag for child in wrapped] == ['Item', 'Block_Link', 'Existing']
    assert isinstance(wrapped[0], XElementWrapper)
    wrapped_item = cast(XElementWrapper, wrapped[0])
    assert wrapped_item.Parent is wrapped
    assert wrapped_item.AttributesChanged is False
    assert isinstance(wrapped_item[0], XElementWrapper)
    wrapped_leaf = cast(XElementWrapper, wrapped_item[0])
    assert wrapped_leaf.Parent is wrapped_item
    assert wrapped[1] is link
    assert wrapped[2] is existing


def _load(root: str, **kwargs) -> XContainerElementWrapper:
    volume = VolumeManager.Load(root, **kwargs)
    assert volume is not None, f'no volume loaded from {root}'
    return cast(XContainerElementWrapper, volume)


@pytest.fixture
def volume():
    """A saved volume reloaded from disk, so it starts clean."""
    base = tempfile.mkdtemp(prefix='nornir_replace_flags_')
    root = os.path.join(base, 'vol')
    os.makedirs(root, exist_ok=True)

    built = _load(root, Create=True)
    for name in BLOCK_NAMES:
        _, block = built.UpdateOrAddChildByAttrib(BlockNode.Create(name), 'Name')
        os.makedirs(block.FullPath, exist_ok=True)
    VolumeManager.Save(built)

    reloaded = _load(root, UseCache=False)
    assert reloaded.ElementHasChangesToSave is False, 'fixture must start clean'

    yield reloaded
    shutil.rmtree(base, ignore_errors=True)


# --- swapping a stub for its loaded content is not an edit --------------------

def test_resolving_links_does_not_dirty_the_parent(volume):
    """The property the read paths depend on."""
    links = [child for child in list(volume) if child.tag.endswith('_Link')]
    assert links, 'fixture should have unresolved link stubs'

    volume._replace_links(links)

    assert volume.ChildrenChanged is False
    assert volume.ElementHasChangesToSave is False


def test_resolving_links_still_swaps_the_children_in(volume):
    """Not dirtying must not mean not replacing."""
    links = [child for child in list(volume) if child.tag.endswith('_Link')]

    volume._replace_links(links)

    tags = [child.tag for child in volume]
    assert not any(tag.endswith('_Link') for tag in tags), tags
    assert sorted(tags) == ['Block', 'Block']


# --- converting a container to a link stub is an edit -------------------------

def test_replace_child_with_link_marks_the_parent_dirty(volume):
    """The container's content leaves the parent's XML, so it must be saved."""
    links = [child for child in list(volume) if child.tag.endswith('_Link')]
    volume._replace_links(links)
    assert volume.ChildrenChanged is False

    volume.ReplaceChildWithLink(volume.find('Block'))

    assert volume.ChildrenChanged is True
    assert volume.ElementHasChangesToSave is True


def test_replace_child_with_link_substitutes_a_stub(volume):
    volume._replace_links([c for c in list(volume) if c.tag.endswith('_Link')])
    target = volume.find('Block')
    name = target.attrib['Name']

    volume.ReplaceChildWithLink(target)

    stubs = [c for c in volume if c.tag == 'Block_Link']
    assert len(stubs) == 1
    assert stubs[0].attrib['Name'] == name


def test_replace_child_with_link_ignores_a_non_child(volume):
    """The early return must not dirty the parent either."""
    stranger = BlockNode.Create('NotMine')
    assert volume.ChildrenChanged is False

    volume.ReplaceChildWithLink(stranger)

    assert volume.ChildrenChanged is False


def test_replace_child_with_link_ignores_non_containers(volume):
    """Only container children have a separate file to point at."""
    volume._replace_links([c for c in list(volume) if c.tag.endswith('_Link')])

    volume.ReplaceChildWithLink(object())

    assert volume.ChildrenChanged is False


# --- append and remove are unchanged ------------------------------------------

def test_append_still_dirties(volume):
    volume.append(BlockNode.Create('Added'))

    assert volume.ChildrenChanged is True


def test_remove_still_dirties(volume):
    volume._replace_links([c for c in list(volume) if c.tag.endswith('_Link')])
    assert volume.ChildrenChanged is False

    volume.remove(volume.find('Block'))

    assert volume.ChildrenChanged is True
