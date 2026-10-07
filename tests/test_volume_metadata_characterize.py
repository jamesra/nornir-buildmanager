"""
Metadata port stage 0: pin how VolumeData.xml is saved, loaded, and queried today.

These are characterization tests, not specifications. They record what the
current ElementTree-backed VolumeManager does so that later port stages (storage
seam, SQLite shadow write) can prove they changed nothing. When one fails after a
deliberate format change, update the golden values in the same commit and say why.

The fixture volume is built with production node types and saved with production
``VolumeManager.Save``; no TESTINPUTPATH data is needed. CreationDate is pinned so
the written bytes are deterministic. The XPath patterns come from
``.cursor/issue-handoff/metadata-port/xpath-inventory.md`` (umbrella repo).
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Iterable
from typing import Any, cast
from xml.etree import ElementTree

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

import nornir_buildmanager
import nornir_buildmanager.volumemanager as vm

FIXED_DATE = '2020-01-02 03:04:05+00:00'
ROOT_TOKEN = b'{ROOT}'

# sha256[:16] of each VolumeData.xml after ROOT_TOKEN substitution, written by the code at the time of capture.
GOLDEN_SHA = {
    'TEM/0001/TEM/Leveled/Images/VolumeData.xml': '352ce5b1b3049356',
    'TEM/0001/TEM/Leveled/TilePyramid/VolumeData.xml': '5606b8e4adf8d3e9',
    'TEM/0001/TEM/Leveled/Tileset/VolumeData.xml': '1c5295e8416c37c7',
    'TEM/0001/TEM/Leveled/VolumeData.xml': '0573bd20e366b656',
    'TEM/0001/TEM/Raw8/Images/VolumeData.xml': '352ce5b1b3049356',
    'TEM/0001/TEM/Raw8/TilePyramid/VolumeData.xml': '5606b8e4adf8d3e9',
    'TEM/0001/TEM/Raw8/Tileset/VolumeData.xml': '1c5295e8416c37c7',
    'TEM/0001/TEM/Raw8/VolumeData.xml': '995f098f8e285ce3',
    'TEM/0001/TEM/VolumeData.xml': '22274ae237147af8',
    'TEM/0001/VolumeData.xml': '1e89fb24dd067ff4',
    'TEM/0002/TEM/Leveled/Images/VolumeData.xml': '352ce5b1b3049356',
    'TEM/0002/TEM/Leveled/TilePyramid/VolumeData.xml': '5606b8e4adf8d3e9',
    'TEM/0002/TEM/Leveled/Tileset/VolumeData.xml': '1c5295e8416c37c7',
    'TEM/0002/TEM/Leveled/VolumeData.xml': '0573bd20e366b656',
    'TEM/0002/TEM/Raw8/Images/VolumeData.xml': '352ce5b1b3049356',
    'TEM/0002/TEM/Raw8/TilePyramid/VolumeData.xml': '5606b8e4adf8d3e9',
    'TEM/0002/TEM/Raw8/Tileset/VolumeData.xml': '1c5295e8416c37c7',
    'TEM/0002/TEM/Raw8/VolumeData.xml': '995f098f8e285ce3',
    'TEM/0002/TEM/VolumeData.xml': '22274ae237147af8',
    'TEM/0002/VolumeData.xml': '7db6f6e9250db7fe',
    'TEM/StosBrute64/VolumeData.xml': '7d0e664fbf25b4ff',
    'TEM/VolumeData.xml': '8c29f17de9603232',
    'VolumeData.xml': '07297a61a34cb925',
}


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(b'x')


def _build_volume(root: str, sections: Iterable[int] = (1, 2), filters: Iterable[str] = ('Raw8', 'Leveled'),
                  notes: str = 'notes text \u00b5m') -> None:
    """Sections each holding one channel with *filters*, plus one StosGroup and one StosMap, saved sharded."""
    volume = cast(vm.XContainerElementWrapper, vm.VolumeManager.Load(root, Create=True))
    _, block = volume.UpdateOrAddChildByAttrib(vm.BlockNode.Create('TEM'), 'Name')
    for number in sections:
        _, section = block.UpdateOrAddChildByAttrib(vm.SectionNode.Create(number), 'Number')
        _, channel = section.UpdateOrAddChildByAttrib(vm.ChannelNode.Create('TEM'), 'Name')
        for name in ('Stage', 'Prune'):
            _, transform = channel.UpdateOrAddChildByAttrib(vm.TransformNode.Create(name, 'Mosaic'), 'Name')
            _touch(transform.FullPath)
        channel.UpdateOrAddChild(vm.TransformDataNode.Create('Stage.mosaic.data'))
        channel.UpdateOrAddChild(vm.NotesNode.Create(Text=notes))
        for name in filters:
            _, filter_node = channel.UpdateOrAddChildByAttrib(vm.FilterNode.Create(name), 'Name')
            _, pyramid = filter_node.UpdateOrAddChild(vm.TilePyramidNode.Create(NumberOfTiles=1))
            _, level = pyramid.UpdateOrAddChildByAttrib(vm.LevelNode.Create(1), 'Downsample')
            _touch(os.path.join(level.FullPath, '000.png'))
            _, image_set = filter_node.UpdateOrAddChild(vm.ImageSetNode.Create(Type=''))
            _, level = image_set.UpdateOrAddChildByAttrib(vm.LevelNode.Create(1), 'Downsample')
            level.UpdateOrAddChild(vm.ImageNode.Create('image.png'))
            _, histogram = filter_node.UpdateOrAddChild(vm.HistogramNode.Create(Type='Prune'))
            histogram.UpdateOrAddChild(vm.ImageNode.Create('hist.png'))
            histogram.UpdateOrAddChild(vm.DataNode.Create('hist.xml'))
            _, prune = filter_node.UpdateOrAddChild(vm.PruneNode.Create(Type='Prune', Overlap=0.1))
            prune.UpdateOrAddChild(vm.ImageNode.Create('prune.png'))
            _, tileset = filter_node.UpdateOrAddChild(vm.TilesetNode.Create())
            _, level = tileset.UpdateOrAddChildByAttrib(vm.LevelNode.Create(1), 'Downsample')
            _touch(os.path.join(level.FullPath, 'X000_Y000.png'))
    _, group = block.UpdateOrAddChildByAttrib(vm.StosGroupNode.Create('StosBrute64', 64), 'Name')
    _, mappings = group.UpdateOrAddChildByAttrib(vm.SectionMappingsNode.Create(MappedSectionNumber=2),
                                                 'MappedSectionNumber')
    mappings.UpdateOrAddChild(vm.TransformNode.Create('2-1', 'Grid', Path='2-1.stos',
                                                   ControlSectionNumber='1', MappedSectionNumber='2'))
    _, stos_map = block.UpdateOrAddChildByAttrib(vm.StosMapNode.Create('PotentialRegistrationChain'), 'Name')
    stos_map.append(vm.MappingNode.Create(1, [2]))

    for element in volume.iter():
        if 'CreationDate' in element.attrib:
            element.attrib['CreationDate'] = FIXED_DATE
    vm.VolumeManager.Save(volume)


@pytest.fixture
def volume_root(tmp_path) -> str:
    root = os.path.join(str(tmp_path), 'vol')
    _build_volume(root)
    return root


def _load(root: str) -> vm.XContainerElementWrapper:
    volume = vm.VolumeManager.Load(root)
    assert volume is not None
    return cast(vm.XContainerElementWrapper, volume)


def _xml_files(root: str) -> list[str]:
    found = []
    for dirpath, _, filenames in os.walk(root):
        if 'VolumeData.xml' in filenames:
            found.append(os.path.relpath(os.path.join(dirpath, 'VolumeData.xml'), root).replace(os.sep, '/'))
    return sorted(found)


def _snapshot(root: str) -> dict[str, tuple[int, int, bytes]]:
    """relpath -> (inode, mtime_ns, bytes); inode changes on every tmp+replace write."""
    result = {}
    for rel in _xml_files(root):
        path = os.path.join(root, rel)
        stat = os.stat(path)
        with open(path, 'rb') as handle:
            result[rel] = (stat.st_ino, stat.st_mtime_ns, handle.read())
    return result


def _written(before: dict, after: dict) -> set[str]:
    return {rel for rel in after if before.get(rel) != after[rel]}


def _normalized_sha(root: str, raw: bytes) -> str:
    return hashlib.sha256(raw.replace(root.encode('utf-8'), ROOT_TOKEN)).hexdigest()[:16]


def _merged_oracle(dirpath: str) -> ElementTree.Element:
    """Plain-ElementTree view of the volume with every ``*_Link`` replaced by its file's root.

    Independent of the wrapper's link handling; links only occur as direct children
    of a container's root element in today's layout.
    """
    element = ElementTree.parse(os.path.join(dirpath, 'VolumeData.xml')).getroot()
    for index, child in enumerate(list(element)):
        if child.tag.endswith('_Link'):
            element[index] = _merged_oracle(os.path.join(dirpath, child.attrib['Path']))
    return element


def _canonical(element: ElementTree.Element, skip_root_path: bool = True) -> tuple:
    """Tag, ordered attributes, whitespace-normalized text, and children, recursively."""
    attrib = [(k, v) for k, v in element.attrib.items() if not (skip_root_path and k == 'Path')]
    text = (element.text or '').strip() or None
    return element.tag, tuple(attrib), text, tuple(_canonical(child, False) for child in element)


def _ancestry_key(element: Any, parents: dict | None = None) -> tuple:
    """Identity of an element below the Volume root, from wrapper ``Parent`` links or an oracle parent map."""
    chain = []
    node = element
    while True:
        parent = parents.get(node) if parents is not None else node.Parent
        if parent is None:
            break
        chain.append((node.tag, tuple(sorted(node.attrib.items()))))
        node = parent
    return tuple(reversed(chain))


def test_sharded_save_matches_golden_bytes(volume_root):
    snapshot = _snapshot(volume_root)
    actual = {rel: _normalized_sha(volume_root, raw) for rel, (_, _, raw) in snapshot.items()}
    assert actual == GOLDEN_SHA, {rel: snapshot[rel][2].decode() for rel in actual if actual[rel] != GOLDEN_SHA.get(rel)}


def test_volume_root_and_link_stub_bytes(volume_root):
    """Readable pin of the two outermost files; stubs copy every attribute of the linked child."""
    with open(os.path.join(volume_root, 'VolumeData.xml'), 'rb') as handle:
        assert handle.read().replace(volume_root.encode(), ROOT_TOKEN) == (
            b'<Volume Name="vol" Path="{ROOT}" CreationDate="%s" Version="1.0">\n'
            b'  <Block_Link Path="TEM" Name="TEM" CreationDate="%s" Version="1.0" />\n'
            b'</Volume>' % (FIXED_DATE.encode(), FIXED_DATE.encode()))


def test_clean_load_resolve_save_writes_nothing(volume_root):
    before = _snapshot(volume_root)
    volume = _load(volume_root)
    volume.LoadAllLinkedNodes()
    vm.VolumeManager.Save(volume)
    assert _written(before, _snapshot(volume_root)) == set()


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
    assert [_canonical(c, False) for c in new_root] == [_canonical(c, False) for c in reversed(old_root)], rel


def test_attribute_only_save_reverses_child_order(volume_root):
    """Known quirk, pinned rather than fixed (changing it changes saved bytes).

    ``sort()`` orders children descending in memory and ``_Save`` walks
    ``list(self)[::-1]``, so a save that sorted writes ascending order. A save with
    only ``AttributesChanged`` skips the sort and walks the loaded (ascending) order
    reversed, so every attribute-only save flips each container's child order on
    disk; a second one flips it back.
    """
    before = _snapshot(volume_root)
    _force_attribute_only_save(volume_root)
    after = _snapshot(volume_root)
    assert _written(before, after) == set(after) == set(before)
    for rel, (_, _, raw) in after.items():
        _assert_children_reversed(rel, before[rel][2], raw)
        with open(os.path.join(volume_root, rel + '.backup.xml'), 'rb') as handle:
            assert handle.read() == before[rel][2], f'backup of {rel} is not the previous file'
    assert {rel for rel, (_, _, raw) in after.items() if raw != before[rel][2]} == {
        'TEM/VolumeData.xml', 'TEM/0001/TEM/VolumeData.xml', 'TEM/0002/TEM/VolumeData.xml',
        'TEM/0001/TEM/Raw8/VolumeData.xml', 'TEM/0001/TEM/Leveled/VolumeData.xml',
        'TEM/0002/TEM/Raw8/VolumeData.xml', 'TEM/0002/TEM/Leveled/VolumeData.xml'}

    _force_attribute_only_save(volume_root)
    assert {rel: raw for rel, (_, _, raw) in _snapshot(volume_root).items()} == {
        rel: raw for rel, (_, _, raw) in before.items()}


def test_parent_sort_leaves_attribute_only_children_unsorted(volume_root):
    """A child-list change sorts only that container (``sort(recurse=False)``); loaded
    linked children with only attribute changes still flip.

    ``SortKey`` is the tag and the sort is stable, so the sorted save groups by tag
    but still writes same-tag siblings in reverse of their loaded order.
    """
    before = _snapshot(volume_root)
    volume = _load(volume_root)
    channel = _find(volume, "Block/Section[@Number='1']/Channel")
    for filter_node in channel.findall('Filter'):
        filter_node.AttributesChanged = True
    channel.UpdateOrAddChildByAttrib(vm.TransformNode.Create('Grid', 'Mosaic'), 'Name')
    vm.VolumeManager.Save(volume)
    after = _snapshot(volume_root)
    assert _written(before, after) == {'TEM/0001/TEM/VolumeData.xml', 'TEM/0001/TEM/Raw8/VolumeData.xml',
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
        _build_volume(root, sections, filters, notes)
        before = _snapshot(root)
        volume = _load(root)
        volume.LoadAllLinkedNodes()
        assert _canonical(volume) == _canonical(_merged_oracle(root))
        assert not any(e.tag.endswith('_Link') for e in volume.iter())
        assert sorted(s.Number for s in volume.findall('Block/Section')) == sorted(sections)
        assert sorted(f.Name for f in volume.findall('Block/Section/Channel/Filter')) == sorted(filters * len(sections))
        assert {n.text for n in volume.findall('Block/Section/Channel/Notes')} == {notes}
        vm.VolumeManager.Save(volume)
        assert _written(before, _snapshot(root)) == set()


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
    assert _canonical(reloaded) == _canonical(_merged_oracle(volume_root))


# (root xpath from the Volume, query xpath, total matches over all roots). Values stand in for #Variables.
XPATH_CASES = [
    ('', 'Block', 1),
    ('', 'Block/Section', 2),
    ('', 'Block/Section/Channel', 2),
    ('', 'Block/Section/Channel/Filter', 4),
    ('', "Block/Section/Channel/Filter[@Name='Leveled']", 2),
    ('', 'Block/Section/Channel/Filter/TilePyramid', 4),
    ('', 'Block/Section/Channel/Transform', 4),
    ('', 'Block/StosGroup/SectionMappings/Transform', 1),
    ('', "Block/StosGroup[@Name='StosBrute64']/SectionMappings", 1),
    ('', "Block/StosMap[@Name='PotentialRegistrationChain']", 1),
    ('Block', 'Section', 2),
    ('Block', "Section[@Number='2']", 1),
    ('Block', 'Section/Channel', 2),
    ('Block', "StosGroup[@Name='StosBrute64']", 1),
    ('Block/Section', 'Channel', 2),
    ('Block/Section/Channel', 'Filter', 4),
    ('Block/Section/Channel', "Filter[@Name='Raw8']", 2),
    ('Block/Section/Channel', "Filter[@Name='Missing']", 0),
    ('Block/Section/Channel', 'Transform', 4),
    ('Block/Section/Channel', "Transform[@Name='Prune']", 2),
    ('Block/Section/Channel', 'TransformData', 2),
    ('Block/Section/Channel', 'Notes', 2),
    ('Block/Section/Channel', "Filter/TilePyramid/Level[@Downsample='1']", 4),
    ('Block/Section/Channel/Filter', 'TilePyramid', 4),
    ('Block/Section/Channel/Filter', 'Tileset', 4),
    ('Block/Section/Channel/Filter', 'ImageSet', 4),
    ('Block/Section/Channel/Filter', 'ImageSet/Level', 4),
    ('Block/Section/Channel/Filter', 'ImageSet/Level/Image', 4),
    ('Block/Section/Channel/Filter', 'Histogram', 4),
    ('Block/Section/Channel/Filter', 'Histogram/Image', 4),
    ('Block/Section/Channel/Filter', 'Prune', 4),
    ('Block/Section/Channel/Filter', "Prune[@Overlap='0.1']", 4),
    ('Block/Section/Channel/Filter/Histogram', 'Data', 4),
    ('Block/Section/Channel/Filter/Histogram', 'Image', 4),
    ('Block/StosGroup', 'SectionMappings/Transform', 1),
    ('Block/StosGroup', "SectionMappings/Transform[@Type='Grid']", 1),
    ('Block/StosGroup', "SectionMappings[@MappedSectionNumber='2']", 1),
    ('Block/StosMap', 'Mapping', 1),
]


@pytest.mark.parametrize('root_xpath,xpath,expected', XPATH_CASES)
def test_xpath_results_match_merged_tree(volume_root, root_xpath, xpath, expected):
    """The wrapper's link-aware find/findall over sharded files equals plain ElementTree on the merged tree."""
    oracle = _merged_oracle(volume_root)
    parents = {child: parent for parent in oracle.iter() for child in parent}
    volume = _load(volume_root)

    wrapper_roots = list(volume.findall(root_xpath)) if root_xpath else [volume]
    oracle_roots = oracle.findall(root_xpath) if root_xpath else [oracle]
    assert [_ancestry_key(r) for r in wrapper_roots] == [_ancestry_key(r, parents) for r in oracle_roots]

    total = 0
    for wrapper_root, oracle_root in zip(wrapper_roots, oracle_roots):
        expected_keys = [_ancestry_key(e, parents) for e in oracle_root.findall(xpath)]
        assert [_ancestry_key(e) for e in wrapper_root.findall(xpath)] == expected_keys
        first = wrapper_root.find(xpath)
        assert (None if first is None else _ancestry_key(first)) == (expected_keys[0] if expected_keys else None)
        total += len(expected_keys)
    assert total == expected


SIMPLE_XPATH = re.compile(r"^[A-Za-z]\w*(\[@\w+='[^'/]*'\])?(/[A-Za-z]\w*(\[@\w+='[^'/]*'\])?)*$")


def test_pipeline_xpaths_use_only_simple_steps():
    """Every Pipelines.xml XPath is tag or tag[@attr='value'] steps joined by '/'.

    The inventory relies on this: no wildcards, '//', positional or text predicates,
    and no '/' inside values (``__ElementLinkNameFromXPath`` splits on '/').
    """
    config = os.path.join(os.path.dirname(nornir_buildmanager.__file__), 'config', 'Pipelines.xml')
    tree = ElementTree.parse(config)
    xpaths = {e.attrib['XPath'] for e in tree.iter() if 'XPath' in e.attrib}
    xpaths |= {e.attrib['RowXPath'] for e in tree.iter() if 'RowXPath' in e.attrib}
    for e in tree.iter():
        xpaths |= set(filter(None, e.attrib.get('ColumnXPaths', '').split(',')))
    assert len(xpaths) > 40
    assert sorted(x for x in xpaths if not SIMPLE_XPATH.match(x)) == []


def _raw_tags(element: ElementTree.Element) -> list[str]:
    return [child.tag for child in list(element)]


def _find(node: ElementTree.Element, xpath: str) -> Any:
    """Wrapper ``find`` that must match; typed Any because wrapper attributes are dynamic."""
    found = node.find(xpath)
    assert found is not None, xpath
    return found


def test_links_resolve_lazily_and_only_where_matched(volume_root):
    before = _snapshot(volume_root)
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
    assert _written(before, _snapshot(volume_root)) == set()


def test_linked_attribute_change_rewrites_child_and_parent_stub(volume_root):
    volume = _load(volume_root)
    section = _find(volume, "Block/Section[@Number='1']")
    block = section.Parent
    section.Name = 'Renamed'
    assert section.AttributesChanged
    assert not block.ChildrenChanged and not block.ElementHasChangesToSave

    before = _snapshot(volume_root)
    vm.VolumeManager.Save(volume)
    assert _written(before, _snapshot(volume_root)) == {'TEM/VolumeData.xml', 'TEM/0001/VolumeData.xml'}
    assert not section.ElementHasChangesToSave
    stub = _find(ElementTree.parse(os.path.join(volume_root, 'TEM', 'VolumeData.xml')).getroot(),
                 "Section_Link[@Number='1']")
    assert stub.attrib['Name'] == 'Renamed'


def test_child_list_change_rewrites_only_that_container(volume_root):
    volume = _load(volume_root)
    channel = _find(volume, "Block/Section[@Number='2']/Channel")
    channel.UpdateOrAddChildByAttrib(vm.TransformNode.Create('Grid', 'Mosaic'), 'Name')
    assert channel.ChildrenChanged and not channel.Parent.ElementHasChangesToSave

    before = _snapshot(volume_root)
    vm.VolumeManager.Save(volume)
    assert _written(before, _snapshot(volume_root)) == {'TEM/0002/TEM/VolumeData.xml'}
    assert not channel.ChildrenChanged


def test_embedded_child_change_dirties_its_container(volume_root):
    """Non-linked children (Transform, Level) are serialized into their container's file."""
    volume = _load(volume_root)
    transform = _find(volume, "Block/Section[@Number='1']/Channel/Transform[@Name='Stage']")
    channel = transform.Parent
    transform.ControlSectionNumber = 5
    assert not channel.AttributesChanged and not channel.ChildrenChanged
    assert channel.ElementHasChangesToSave

    before = _snapshot(volume_root)
    vm.VolumeManager.Save(volume)
    assert _written(before, _snapshot(volume_root)) == {'TEM/0001/TEM/VolumeData.xml'}
    assert not transform.AttributesChanged
