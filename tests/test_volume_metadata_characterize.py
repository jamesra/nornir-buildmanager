"""
Metadata port stage 0: pin how VolumeData.xml is saved, loaded, and queried today.

These are characterization tests, not specifications. They record what the
current ElementTree-backed VolumeManager does so that later port stages (storage
seam, SQLite shadow write) can prove they changed nothing. When one fails after a
deliberate format change, update the golden values in the same commit and say why.

The fixture volume is built with production node types and saved with production
``VolumeManager.Save``; no TESTINPUTPATH data is needed. CreationDate is pinned so
the written bytes are deterministic. The XPath patterns come from
``.cursor/issue-handoff/metadata-port/xpath-inventory.md`` (umbrella repo). Golden
tables and the fixture builder live in ``metadata_port_characterize_data``.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any, cast
from xml.etree import ElementTree

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

import nornir_buildmanager.volumemanager as vm

from .metadata_port_characterize_data import (
    FIXED_DATE,
    GOLDEN_SHA,
    INDIRECT_PATTERNS,
    ROOT_TOKEN,
    XPATH_CASES,
    ancestry_key,
    build_volume,
    canonical,
    merged_oracle,
    normalized_sha,
    pipeline_patterns,
    pipeline_xpaths,
    snapshot,
    source_patterns,
    written,
)


@pytest.fixture
def volume_root(tmp_path) -> str:
    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root)
    return root


@pytest.fixture(scope='module')
def query_volume_root(tmp_path_factory) -> str:
    """Read-only fixture for the XPath table; module scoped because no XPath test writes."""
    root = os.path.join(str(tmp_path_factory.mktemp('query')), 'vol')
    build_volume(root, extras=True)
    return root


def _load(root: str) -> vm.XContainerElementWrapper:
    volume = vm.VolumeManager.Load(root)
    assert volume is not None
    return cast(vm.XContainerElementWrapper, volume)


def test_sharded_save_matches_golden_bytes(volume_root):
    files = snapshot(volume_root)
    actual = {rel: normalized_sha(volume_root, raw) for rel, (_, _, raw) in files.items()}
    assert actual == GOLDEN_SHA, {rel: files[rel][2].decode() for rel in actual if actual[rel] != GOLDEN_SHA.get(rel)}


def test_volume_root_and_link_stub_bytes(volume_root):
    """Readable pin of the two outermost files; stubs copy every attribute of the linked child."""
    with open(os.path.join(volume_root, 'VolumeData.xml'), 'rb') as handle:
        assert handle.read().replace(volume_root.encode(), ROOT_TOKEN) == (
            b'<Volume Name="vol" Path="{ROOT}" CreationDate="%s" Version="1.0">\n'
            b'  <Block_Link Path="TEM" Name="TEM" CreationDate="%s" Version="1.0" />\n'
            b'</Volume>' % (FIXED_DATE.encode(), FIXED_DATE.encode()))


def test_clean_load_resolve_save_writes_nothing(volume_root):
    before = snapshot(volume_root)
    volume = _load(volume_root)
    volume.LoadAllLinkedNodes()
    vm.VolumeManager.Save(volume)
    assert written(before, snapshot(volume_root)) == set()


def _force_attribute_only_save(root: str) -> None:
    volume = _load(root)
    volume.LoadAllLinkedNodes()
    for element in volume.iter():
        if isinstance(element, vm.XContainerElementWrapper):
            element.AttributesChanged = True
    vm.VolumeManager.Save(volume)


def _assert_children_reversed(rel: str, old_raw: bytes, new_raw: bytes) -> None:
    old_root = ElementTree.fromstring(old_raw)
    new_root = ElementTree.fromstring(new_raw)
    assert new_root.attrib == old_root.attrib, rel
    assert [canonical(c, False) for c in new_root] == [canonical(c, False) for c in reversed(old_root)], rel


def test_attribute_only_save_reverses_child_order(volume_root):
    """Known quirk, pinned rather than fixed (changing it changes saved bytes).

    ``sort()`` orders children descending in memory and ``_Save`` walks
    ``list(self)[::-1]``, so a save that sorted writes ascending order. A save with
    only ``AttributesChanged`` skips the sort and walks the loaded (ascending) order
    reversed, so every attribute-only save flips each container's child order on
    disk; a second one flips it back.
    """
    before = snapshot(volume_root)
    _force_attribute_only_save(volume_root)
    after = snapshot(volume_root)
    assert written(before, after) == set(after) == set(before)
    for rel, (_, _, raw) in after.items():
        _assert_children_reversed(rel, before[rel][2], raw)
        with open(os.path.join(volume_root, rel + '.backup.xml'), 'rb') as handle:
            assert handle.read() == before[rel][2], f'backup of {rel} is not the previous file'
    assert {rel for rel, (_, _, raw) in after.items() if raw != before[rel][2]} == {
        'TEM/VolumeData.xml', 'TEM/0001/TEM/VolumeData.xml', 'TEM/0002/TEM/VolumeData.xml',
        'TEM/0001/TEM/Raw8/VolumeData.xml', 'TEM/0001/TEM/Leveled/VolumeData.xml',
        'TEM/0002/TEM/Raw8/VolumeData.xml', 'TEM/0002/TEM/Leveled/VolumeData.xml'}

    _force_attribute_only_save(volume_root)
    assert {rel: raw for rel, (_, _, raw) in snapshot(volume_root).items()} == {
        rel: raw for rel, (_, _, raw) in before.items()}


def test_parent_sort_leaves_attribute_only_children_unsorted(volume_root):
    """A child-list change sorts only that container (``sort(recurse=False)``); loaded
    linked children with only attribute changes still flip.

    ``SortKey`` is the tag and the sort is stable, so the sorted save groups by tag
    but still writes same-tag siblings in reverse of their loaded order.
    """
    before = snapshot(volume_root)
    volume = _load(volume_root)
    channel = _find(volume, "Block/Section[@Number='1']/Channel")
    for filter_node in channel.findall('Filter'):
        filter_node.AttributesChanged = True
    channel.UpdateOrAddChildByAttrib(vm.TransformNode.Create('Grid', 'Mosaic'), 'Name')
    vm.VolumeManager.Save(volume)
    after = snapshot(volume_root)
    assert written(before, after) == {'TEM/0001/TEM/VolumeData.xml', 'TEM/0001/TEM/Raw8/VolumeData.xml',
                                       'TEM/0001/TEM/Leveled/VolumeData.xml'}
    for rel in ('TEM/0001/TEM/Raw8/VolumeData.xml', 'TEM/0001/TEM/Leveled/VolumeData.xml'):
        _assert_children_reversed(rel, before[rel][2], after[rel][2])
    tags = [(c.tag, c.get('Name')) for c in ElementTree.fromstring(after['TEM/0001/TEM/VolumeData.xml'][2])]
    assert tags == [('Filter_Link', 'Raw8'), ('Filter_Link', 'Leveled'), ('Notes', None), ('Transform', 'Grid'),
                    ('Transform', 'Stage'), ('Transform', 'Prune'), ('TransformData', None)]


@settings(max_examples=12, deadline=None)  # each example writes a few dozen files
@given(sections=st.lists(st.integers(1, 9999), min_size=1, max_size=4, unique=True),
       filters=st.lists(st.sampled_from(['Raw8', 'Leveled', 'Mask', 'Blob_Leveled']), max_size=3, unique=True),
       notes=st.text(st.characters(blacklist_categories=('Cs', 'Cc', 'Cn')), min_size=1, max_size=40))
@example(sections=[1, 2], filters=['Raw8', 'Leveled'], notes='notes text \u00b5m')
@example(sections=[3], filters=[], notes=' <a & "b"> ')
def test_round_trip_property(sections, filters, notes):
    """Any small volume loads back to its merged files, keeps note text exactly, and re-saves nothing."""
    with tempfile.TemporaryDirectory(dir=os.environ.get('TESTOUTPUTPATH') or None) as base:
        root = os.path.join(base, 'vol')
        build_volume(root, sections, filters, notes)
        before = snapshot(root)
        volume = _load(root)
        volume.LoadAllLinkedNodes()
        assert canonical(volume) == canonical(merged_oracle(root))
        assert not any(e.tag.endswith('_Link') for e in volume.iter())
        assert sorted(s.Number for s in volume.findall('Block/Section')) == sorted(sections)
        assert sorted(f.Name for f in volume.findall('Block/Section/Channel/Filter')) == sorted(filters * len(sections))
        assert {n.text for n in volume.findall('Block/Section/Channel/Notes')} == {notes}
        vm.VolumeManager.Save(volume)
        assert written(before, snapshot(root)) == set()


def test_single_file_round_trip(volume_root, tmp_path):
    volume = _load(volume_root)
    volume.LoadAllLinkedNodes()
    single_root = os.path.join(str(tmp_path), 'single')
    single_xml = os.path.join(single_root, 'VolumeData.xml')
    vm.VolumeManager.SaveSingleFile(volume, single_xml)
    with open(single_xml, 'rb') as handle:
        first = handle.read()
    vm.VolumeManager.SaveSingleFile(volume, single_xml)
    with open(single_xml, 'rb') as handle:
        assert handle.read() == first
    assert b'_Link' not in first
    assert not first.startswith(b'<?xml')

    reloaded = _load(single_root)
    assert reloaded.attrib['Path'] == single_root
    reloaded.LoadAllLinkedNodes()
    assert canonical(reloaded) == canonical(merged_oracle(volume_root))


@pytest.mark.parametrize('pattern,root_xpath,xpath,expected', XPATH_CASES,
                         ids=[f'{root or "Volume"}|{xpath}' for _, root, xpath, _ in XPATH_CASES])
def test_xpath_results_match_merged_tree(query_volume_root, pattern, root_xpath, xpath, expected):
    """The wrapper's link-aware find/findall over sharded files equals plain ElementTree on the merged tree.

    ``find`` runs on its own fresh load: after ``findall`` the links are already
    resolved, which would hide ``find``'s link handling.
    """
    assert re.fullmatch('.+'.join(map(re.escape, pattern.split('#v'))), xpath), (pattern, xpath)
    oracle = merged_oracle(query_volume_root)
    parents = {child: parent for parent in oracle.iter() for child in parent}

    def roots(volume: Any) -> list:
        return list(volume.findall(root_xpath)) if root_xpath else [volume]

    findall_roots = roots(_load(query_volume_root))
    find_roots = roots(_load(query_volume_root))
    oracle_roots = oracle.findall(root_xpath) if root_xpath else [oracle]
    assert [ancestry_key(r) for r in findall_roots] == [ancestry_key(r, parents) for r in oracle_roots]

    total = 0
    for findall_root, find_root, oracle_root in zip(findall_roots, find_roots, oracle_roots):
        expected_keys = [ancestry_key(e, parents) for e in oracle_root.findall(xpath)]
        first = find_root.find(xpath)
        assert (None if first is None else ancestry_key(first)) == (expected_keys[0] if expected_keys else None)
        assert [ancestry_key(e) for e in findall_root.findall(xpath)] == expected_keys
        total += len(expected_keys)
    assert total == expected


SIMPLE_XPATH = re.compile(r"^[A-Za-z]\w*(\[@\w+='[^'/]*'\])?(/[A-Za-z]\w*(\[@\w+='[^'/]*'\])?)*$")


def test_pipeline_xpaths_use_only_simple_steps():
    """Every Pipelines.xml XPath is tag or tag[@attr='value'] steps joined by '/'.

    The inventory relies on this: no wildcards, '//', positional or text predicates,
    and no '/' inside values (``__ElementLinkNameFromXPath`` splits on '/').
    """
    xpaths = pipeline_xpaths()
    assert len(xpaths) > 40
    assert sorted(x for x in xpaths if not SIMPLE_XPATH.match(x)) == []


def test_every_inventoried_pattern_has_a_golden_case():
    """A pattern added to Pipelines.xml or a find literal fails here until XPATH_CASES pins it."""
    covered = {case[0] for case in XPATH_CASES}
    inventoried = pipeline_patterns() | source_patterns() | set(INDIRECT_PATTERNS)
    assert sorted(inventoried - covered) == []
    assert sorted(covered - inventoried) == []


def test_get_child_by_attrib_formats_floats_with_g(query_volume_root):
    """Float values become ``%g`` text, so 1.0 matches Downsample="1"; the string '1.0' does not."""
    pyramid = _find(_load(query_volume_root), "Block/Section[@Number='1']/Channel/Filter[@Name='Raw8']/TilePyramid")
    level = _find(pyramid, 'Level')
    assert pyramid.GetChildByAttrib('Level', 'Downsample', 1.0) is level
    assert pyramid.GetChildByAttrib('Level', 'Downsample', '1') is level
    assert pyramid.GetChildByAttrib('Level', 'Downsample', '1.0') is None
    assert list(pyramid.GetChildrenByAttrib('Level', 'Downsample', 1.0)) == [level]
    assert list(pyramid.GetChildrenByAttrib('Level', 'Downsample', '1')) == [level]
    assert list(pyramid.GetChildrenByAttrib('Level', 'Downsample', '1.0')) == []


def test_update_or_add_child_by_attrib_returns_existing_match(query_volume_root):
    """Matches on Name by default; an existing match is returned rather than duplicated."""
    channel = _find(_load(query_volume_root), "Block/Section[@Number='1']/Channel")
    existing = _find(channel, "Transform[@Name='Stage']")
    added, node = channel.UpdateOrAddChildByAttrib(vm.TransformNode.Create('Stage', 'Mosaic'))
    assert not added and node is existing
    added, node = channel.UpdateOrAddChildByAttrib(vm.FilterNode.Create('Leveled'), 'Name')
    assert not added and node is _find(channel, "Filter[@Name='Leveled']")
    assert len(list(channel.findall('Transform'))) == 3 and len(list(channel.findall('Filter'))) == 2


def test_multi_attribute_update_or_add_is_rejected(query_volume_root):
    """``UpdateOrAddChildByAttrib`` joins several names with ' and ', which ElementTree XPath rejects.

    No live caller passes more than one name; pinned so a SQL query layer does not
    silently start accepting it.
    """
    block = _find(_load(query_volume_root), 'Block')
    with pytest.raises(SyntaxError, match='invalid predicate'):
        block.UpdateOrAddChildByAttrib(vm.SectionNode.Create(1), ['Number', 'Name'])


def _raw_tags(element: ElementTree.Element) -> list[str]:
    return [child.tag for child in list(element)]


def _find(node: ElementTree.Element, xpath: str) -> Any:
    """Wrapper ``find`` that must match; typed Any because wrapper attributes are dynamic."""
    found = node.find(xpath)
    assert found is not None, xpath
    return found


def test_links_resolve_lazily_and_only_where_matched(volume_root):
    before = snapshot(volume_root)
    volume = _load(volume_root)
    assert _raw_tags(volume) == ['Block_Link']

    block = _find(volume, 'Block')
    assert _raw_tags(volume) == ['Block']
    assert _raw_tags(block) == ['Section_Link', 'Section_Link', 'StosGroup_Link', 'StosMap']

    section = _find(block, "Section[@Number='2']")
    assert _raw_tags(block) == ['Section_Link', 'Section', 'StosGroup_Link', 'StosMap']
    assert _raw_tags(section) == ['Channel_Link']

    list(block.findall('Section'))
    assert _raw_tags(block) == ['Section', 'Section', 'StosGroup_Link', 'StosMap']

    assert not any(getattr(e, 'ElementHasChangesToSave', False) for e in volume.iter())
    assert written(before, snapshot(volume_root)) == set()


def test_linked_attribute_change_rewrites_child_and_parent_stub(volume_root):
    volume = _load(volume_root)
    section = _find(volume, "Block/Section[@Number='1']")
    block = section.Parent
    section.Name = 'Renamed'
    assert section.AttributesChanged
    assert not block.ChildrenChanged and not block.ElementHasChangesToSave

    before = snapshot(volume_root)
    vm.VolumeManager.Save(volume)
    assert written(before, snapshot(volume_root)) == {'TEM/VolumeData.xml', 'TEM/0001/VolumeData.xml'}
    assert not section.ElementHasChangesToSave
    stub = _find(ElementTree.parse(os.path.join(volume_root, 'TEM', 'VolumeData.xml')).getroot(),
                 "Section_Link[@Number='1']")
    assert stub.attrib['Name'] == 'Renamed'


def test_child_list_change_rewrites_only_that_container(volume_root):
    volume = _load(volume_root)
    channel = _find(volume, "Block/Section[@Number='2']/Channel")
    channel.UpdateOrAddChildByAttrib(vm.TransformNode.Create('Grid', 'Mosaic'), 'Name')
    assert channel.ChildrenChanged and not channel.Parent.ElementHasChangesToSave

    before = snapshot(volume_root)
    vm.VolumeManager.Save(volume)
    assert written(before, snapshot(volume_root)) == {'TEM/0002/TEM/VolumeData.xml'}
    assert not channel.ChildrenChanged


def test_embedded_child_change_dirties_its_container(volume_root):
    """Non-linked children (Transform, Level) are serialized into their container's file."""
    volume = _load(volume_root)
    transform = _find(volume, "Block/Section[@Number='1']/Channel/Transform[@Name='Stage']")
    channel = transform.Parent
    transform.ControlSectionNumber = 5
    assert not channel.AttributesChanged and not channel.ChildrenChanged
    assert channel.ElementHasChangesToSave

    before = snapshot(volume_root)
    vm.VolumeManager.Save(volume)
    assert written(before, snapshot(volume_root)) == {'TEM/0001/TEM/VolumeData.xml'}
    assert not transform.AttributesChanged
