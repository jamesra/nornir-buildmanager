"""
EnumerateTransformDependents must not search with findall(None).

The recursive call passed four arguments to a five-parameter signature, so
``child_element_name`` fell back to its ``None`` default and the recursion called
``parent_node.findall(None)``. That is not a silent mismatch, it raises::

    TypeError: argument of type 'NoneType' is not a container or iterable
      xelementwrapper.py:963 in __ElementLinkNameFromXPath -> if '\\\\' in xpath

Measured before the change, the damage was wider than the recursive call. Of the
two call sites in ``operations/migration.py``:

- line 192, inside ``MigrateTransforms_1p2_to_1p3``, passes ``recursive=True``
  and **no** ``child_element_name``, so the very first ``findall`` raised and the
  loop updating dependent transforms never ran at all;
- line 267 passes an explicit ``'StosGroup/SectionMappings/Transform'`` with
  ``recursive=False``, so it never recursed and never hit it.

The default is now ``'*'`` rather than ``None``, and the recursion forwards it.
Every child is searched rather than transforms alone because six node types mix
in ``InputTransformHandler`` and can therefore declare a dependency --
TransformNode, ImageNode, ImageSetBaseNode, TilesetNode, HistogramBase and
TransformDataNode -- so restricting to ``Transform`` would silently miss the
others. The existing attribute checks do the real selecting.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import cast

import pytest

from nornir_buildmanager.volumemanager import (BlockNode, SectionNode, VolumeManager,
                                               XContainerElementWrapper)
from nornir_buildmanager.volumemanager.inputtransformhandler import InputTransformHandler
from nornir_buildmanager.volumemanager.xelementwrapper import XElementWrapper

CHECKSUM = 'abc123'
TYPE_NAME = 'Grid'


def _load(root: str, **kwargs) -> XContainerElementWrapper:
    volume = VolumeManager.Load(root, **kwargs)
    assert volume is not None, f'no volume loaded from {root}'
    return cast(XContainerElementWrapper, volume)


def _dependent(tag: str, name: str, checksum: str = CHECKSUM,
               type_name: str | None = TYPE_NAME) -> XElementWrapper:
    attrib = {'Name': name, 'InputTransformChecksum': checksum}
    if type_name is not None:
        attrib['InputTransformType'] = type_name
    return XElementWrapper(tag, attrib=attrib)


@pytest.fixture
def block():
    """A block with dependents at depth 0, 1 and 2, of assorted tags."""
    base = tempfile.mkdtemp(prefix='nornir_enum_dep_')
    root = os.path.join(base, 'vol')
    os.makedirs(root, exist_ok=True)

    volume = _load(root, Create=True)
    _, blk = volume.UpdateOrAddChildByAttrib(BlockNode.Create('Block'), 'Name')
    os.makedirs(blk.FullPath, exist_ok=True)

    blk.append(_dependent('Transform', 'shallow'))

    _, section = blk.UpdateOrAddChildByAttrib(SectionNode.Create(1), 'Number')
    os.makedirs(section.FullPath, exist_ok=True)
    section.append(_dependent('Transform', 'nested'))
    section.append(_dependent('Image', 'nested_image'))

    holder = XElementWrapper('Holder', attrib={'Name': 'deep'})
    section.append(holder)
    holder.append(_dependent('Tileset', 'deepest'))

    # Decoys that must never be yielded.
    blk.append(_dependent('Transform', 'other_checksum', checksum='zzz'))
    blk.append(_dependent('Transform', 'other_type', type_name='Rigid'))
    blk.append(XElementWrapper('Transform', attrib={'Name': 'no_input_attrs'}))

    yield blk
    shutil.rmtree(base, ignore_errors=True)


def _names(found) -> list[str]:
    return sorted(element.attrib['Name'] for element in found)


def _enumerate(parent, **kwargs) -> list:
    return list(InputTransformHandler.EnumerateTransformDependents(
        parent, CHECKSUM, TYPE_NAME, **kwargs))


# --- the crash ----------------------------------------------------------------

def test_recursive_without_child_name_does_not_raise(block):
    """This is exactly how migration.py:192 calls it."""
    _enumerate(block, recursive=True)


def test_recursive_with_explicit_child_name_does_not_raise(block):
    """The recursion used to drop the argument even when one was supplied."""
    _enumerate(block, recursive=True, child_element_name='*')


# --- it finds the right dependents --------------------------------------------

def test_non_recursive_finds_only_immediate_children(block):
    assert _names(_enumerate(block, recursive=False)) == ['shallow']


def test_recursive_finds_nested_dependents(block):
    found = _names(_enumerate(block, recursive=True))

    assert found == ['deepest', 'nested', 'nested_image', 'shallow']


def test_recursive_finds_dependents_that_are_not_transforms(block):
    """Six node types mix in InputTransformHandler, not just TransformNode."""
    found = _names(_enumerate(block, recursive=True))

    assert 'nested_image' in found, 'an ImageNode dependent was missed'
    assert 'deepest' in found, 'a TilesetNode dependent two levels down was missed'


@pytest.mark.parametrize('decoy', ['other_checksum', 'other_type', 'no_input_attrs'])
def test_non_matching_children_are_skipped(block, decoy):
    assert decoy not in _names(_enumerate(block, recursive=True))


def test_explicit_child_name_still_restricts_the_search(block):
    """The migration.py:267 style of call must keep working."""
    found = _names(_enumerate(block, recursive=False, child_element_name='Transform'))

    assert found == ['shallow']


def test_none_parent_yields_nothing(block):
    assert _enumerate(None, recursive=True) == []
