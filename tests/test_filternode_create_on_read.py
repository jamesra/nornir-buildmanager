"""
FilterNode.TilePyramid and .Imageset create on read. That is deliberate.

The review filed this as a bug: "create-on-read: TilePyramid and Imageset
properties append a new child, marking the filter dirty during a query". The
mutation is real and is worse than described -- measured on a filter with no
children, reading either property appends the child, dirties the filter, and a
later save writes both an empty node *and a new directory*:

    read filter.Imageset     -> <ImageSet> in    .../Leveled/VolumeData.xml
                                <ImageSet> in    .../Leveled/Images/VolumeData.xml
    read filter.TilePyramid  -> <TilePyramid> in .../Leveled/VolumeData.xml
                                <TilePyramid> in .../Leveled/TilePyramid/VolumeData.xml
    read filter.HasImageset  -> nothing written
    read filter.Tileset      -> nothing written

But it cannot simply be removed, because the write pipeline depends on it.
``operations/tile.py`` builds an output filter with ``GetOrCreateFilter`` and
then calls, unguarded::

    OutputFilterNode.Imageset.SetTransform(transform_node)      # tile.py:1296
    OutputMaskFilterNode.Imageset.SetTransform(transform_node)  # tile.py:1297

A freshly created output filter has no ImageSet, so the property creating one is
the only reason that line works. The same file relies on it again at 1419/1423
via ``BuildImagePyramid(OutputFilterNode.Imageset, ...)``. Making the property
return None would turn core image-pyramid generation into an AttributeError.

Meanwhile the callers that must not mutate already guard, which is the
convention throughout the pipeline -- ``diagnostics.py:165,169``,
``reporting.py:881``, and ``tile.py:1285,1290`` all test ``Has*`` first.

So the contract is pinned here rather than changed. These tests exist so that a
future attempt to make the properties non-creating fails loudly and has to
migrate the ~74 non-test call sites deliberately, rather than discovering the
breakage inside a production build.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import cast

import pytest

from nornir_buildmanager.volumemanager import (BlockNode, ChannelNode, FilterNode,
                                               SectionNode, VolumeManager,
                                               XContainerElementWrapper)


def _load(root: str, **kwargs) -> XContainerElementWrapper:
    volume = VolumeManager.Load(root, **kwargs)
    assert volume is not None, f'no volume loaded from {root}'
    return cast(XContainerElementWrapper, volume)


@pytest.fixture
def filter_node():
    """A filter with no children, reloaded from disk so it starts clean.

    Building the volume leaves every new node dirty, which would mask the very
    flag these tests are about, so the volume is saved and reloaded first.
    """
    base = tempfile.mkdtemp(prefix='nornir_filter_cor_')
    root = os.path.join(base, 'vol')
    os.makedirs(root, exist_ok=True)

    volume = _load(root, Create=True)
    _, block = volume.UpdateOrAddChildByAttrib(BlockNode.Create('Block'), 'Name')
    _, section = block.UpdateOrAddChildByAttrib(SectionNode.Create(1), 'Number')
    _, channel = section.UpdateOrAddChildByAttrib(ChannelNode.Create('Chan'), 'Name')
    _, filt = channel.UpdateOrAddChildByAttrib(FilterNode.Create('Leveled'), 'Name')
    os.makedirs(filt.FullPath, exist_ok=True)
    VolumeManager.Save(volume)

    reloaded = _load(root, UseCache=False)
    filt = reloaded.find('Block').find('Section').find('Channel').find('Filter')
    assert filt is not None
    assert filt.ElementHasChangesToSave is False, 'fixture must start clean'

    yield filt
    shutil.rmtree(base, ignore_errors=True)


# --- the non-mutating accessors stay non-mutating -----------------------------

@pytest.mark.parametrize('name', ['HasImageset', 'HasTilePyramid', 'HasTileset'])
def test_has_accessors_do_not_mutate(filter_node, name):
    """These are what a query is supposed to use."""
    assert getattr(filter_node, name) is False
    assert list(filter_node) == []
    assert filter_node.ElementHasChangesToSave is False


def test_tileset_does_not_create(filter_node):
    """Tileset is the sibling that already behaves as a plain query."""
    assert filter_node.Tileset is None
    assert list(filter_node) == []
    assert filter_node.ElementHasChangesToSave is False


# --- the creating properties keep creating ------------------------------------

@pytest.mark.parametrize('name,tag', [('Imageset', 'ImageSet'),
                                      ('TilePyramid', 'TilePyramid')])
def test_property_creates_when_missing(filter_node, name, tag):
    """Load-bearing: tile.py builds output filters by reading these."""
    assert getattr(filter_node, f'Has{name}') is False

    created = getattr(filter_node, name)

    assert created is not None
    assert [c.tag for c in filter_node] == [tag]
    assert getattr(filter_node, f'Has{name}') is True


@pytest.mark.parametrize('name', ['Imageset', 'TilePyramid'])
def test_property_is_stable_across_reads(filter_node, name):
    """A second read must return the same node, not append another."""
    first = getattr(filter_node, name)
    second = getattr(filter_node, name)

    assert first is second
    assert len(list(filter_node)) == 1


@pytest.mark.parametrize('name', ['Imageset', 'TilePyramid'])
def test_creating_read_marks_the_filter_dirty(filter_node, name):
    """Documents the cost of the contract: the invented node does get saved."""
    assert filter_node.ElementHasChangesToSave is False

    getattr(filter_node, name)

    assert filter_node.ElementHasChangesToSave is True


# --- explicit creation is available where mutation is the intent --------------

def test_get_or_create_imageset_reports_creation(filter_node):
    """Mirrors GetOrCreateTilePyramid, which previously had no counterpart."""
    created, imageset = filter_node.GetOrCreateImageset()

    assert created is True
    assert imageset is not None


def test_get_or_create_imageset_reports_reuse(filter_node):
    filter_node.GetOrCreateImageset()

    created, imageset = filter_node.GetOrCreateImageset()

    assert created is False
    assert imageset is filter_node.Imageset


def test_get_or_create_tile_pyramid_reports_creation(filter_node):
    created, pyramid = filter_node.GetOrCreateTilePyramid()

    assert created is True
    assert pyramid is not None
    assert filter_node.GetOrCreateTilePyramid()[0] is False


@pytest.mark.parametrize('name,method', [('Imageset', 'GetOrCreateImageset'),
                                         ('TilePyramid', 'GetOrCreateTilePyramid')])
def test_property_and_explicit_method_agree(filter_node, name, method):
    """The property must stay a thin alias, so the two cannot drift apart."""
    from_property = getattr(filter_node, name)

    created, from_method = getattr(filter_node, method)()

    assert created is False
    assert from_method is from_property
