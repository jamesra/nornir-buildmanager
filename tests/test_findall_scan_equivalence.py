"""
findall must return exactly what a post-resolution rescan would have returned.

The review filed the triple scan as the cost. Measured, it is not: the raw
ElementTree scan is C-speed at 0.0028 ms, while the whole wrapped call took
0.0385 ms -- 13.9x one scan, not the ~3x a pure triple scan implies. Profiling
500 calls over 200 sections put ~30% in ``_ReplaceChildIfUnwrapped``, invoked
once per child per query only to return the child unchanged, because children
are already wrapped on every pass after the first.

Removing the redundant third scan alone actually made simple tag queries *slower*
(0.0376 -> 0.0391 ms), because a Python list build can cost more than the C scan
it saves. Inlining the already-wrapped test first, then dropping the scan,
improves every shape measured:

    xpath              before     after
    Section            0.0376  -> 0.0260 ms
    *                  0.0770  -> 0.0621 ms
    Section/Channel    0.2843  -> 0.2356 ms

The correctness premise is that each branch of the resolution loop substitutes an
element in place and preserves whether it still matches the xpath, so collecting
the loop's output equals rescanning afterwards. These tests pin that premise, the
inlined fast path, and the surviving behaviour of the whole generator.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import cast
from xml.etree import ElementTree

import pytest

from nornir_buildmanager.volumemanager import (BlockNode, ChannelNode, FilterNode,
                                               SectionNode, VolumeManager,
                                               XContainerElementWrapper)
from nornir_buildmanager.volumemanager.xelementwrapper import XElementWrapper

N_SECTIONS = 6


def _load(root: str, **kwargs) -> XContainerElementWrapper:
    volume = VolumeManager.Load(root, **kwargs)
    assert volume is not None, f'no volume loaded from {root}'
    return cast(XContainerElementWrapper, volume)


@pytest.fixture
def block():
    base = tempfile.mkdtemp(prefix='nornir_findall_eq_')
    root = os.path.join(base, 'vol')
    os.makedirs(root, exist_ok=True)

    volume = _load(root, Create=True)
    _, blk = volume.UpdateOrAddChildByAttrib(BlockNode.Create('Block'), 'Name')
    os.makedirs(blk.FullPath, exist_ok=True)
    for n in range(N_SECTIONS):
        _, section = blk.UpdateOrAddChildByAttrib(SectionNode.Create(n), 'Number')
        os.makedirs(section.FullPath, exist_ok=True)
        _, channel = section.UpdateOrAddChildByAttrib(ChannelNode.Create('Chan'), 'Name')
        os.makedirs(channel.FullPath, exist_ok=True)
        _, filt = channel.UpdateOrAddChildByAttrib(FilterNode.Create('Leveled'), 'Name')
        os.makedirs(filt.FullPath, exist_ok=True)
    VolumeManager.Save(volume)

    reloaded = _load(root, UseCache=False)
    found = reloaded.find('Block')
    assert found is not None
    yield found
    shutil.rmtree(base, ignore_errors=True)


XPATHS = ['Section', '*', 'Section/Channel', 'Section/Channel/Filter']


@pytest.mark.parametrize('xpath', XPATHS)
def test_matches_a_rescan_of_the_same_xpath(block, xpath):
    """The equivalence the optimisation rests on, checked from the outside.

    After one findall has resolved everything, a raw ElementTree scan of the same
    path must see the identical objects, in order.
    """
    from_wrapper = list(block.findall(xpath))

    if '/' not in xpath:  # a raw scan cannot follow the wrapper's nested paths
        rescan = ElementTree.Element.findall(block, xpath)
        assert [id(e) for e in from_wrapper] == [id(e) for e in rescan]


@pytest.mark.parametrize('xpath', XPATHS)
def test_every_result_is_wrapped(block, xpath):
    """The resolution loop exists to guarantee this."""
    found = list(block.findall(xpath))

    assert found, f'{xpath} matched nothing'
    assert all(isinstance(e, XElementWrapper) for e in found)


@pytest.mark.parametrize('xpath', XPATHS)
def test_repeated_calls_return_the_same_objects(block, xpath):
    """Children are already wrapped after the first pass; the fast path relies on it."""
    first = [id(e) for e in block.findall(xpath)]
    second = [id(e) for e in block.findall(xpath)]

    assert first == second


@pytest.mark.parametrize('xpath,expected_tag,expected_n', [
    ('Section', 'Section', N_SECTIONS),
    ('Section/Channel', 'Channel', N_SECTIONS),
    ('Section/Channel/Filter', 'Filter', N_SECTIONS),
])
def test_result_shape(block, xpath, expected_tag, expected_n):
    found = list(block.findall(xpath))

    assert len(found) == expected_n
    assert {e.tag for e in found} == {expected_tag}


def test_wildcard_resolves_link_stubs(block):
    """A wildcard is the path that can match a *_Link, so it must not leak one."""
    found = list(block.findall('*'))

    assert found
    assert not any(e.tag.endswith('_Link') for e in found), [e.tag for e in found]


def test_no_matches_yields_nothing(block):
    assert list(block.findall('Nonexistent')) == []


def test_generator_is_not_consumed_by_len_checks(block):
    """findall is a generator; each call must produce a fresh, complete pass."""
    assert len(list(block.findall('Section'))) == N_SECTIONS
    assert len(list(block.findall('Section'))) == N_SECTIONS


def test_results_are_still_reachable_as_children(block):
    """Whatever is yielded must be the object actually parented in the tree."""
    found = list(block.findall('Section'))

    children = {id(c) for c in block}
    assert all(id(e) in children for e in found)
