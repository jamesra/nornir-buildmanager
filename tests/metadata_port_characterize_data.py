"""
Golden tables and shared helpers for ``test_volume_metadata_characterize.py`` (metadata port stage 0).

Kept apart so the test module reads as a list of behaviors. The XPath table pins every
distinct pattern in ``.cursor/issue-handoff/metadata-port/xpath-inventory.md`` (umbrella
repo); ``pipeline_patterns`` and ``source_patterns`` re-derive the inventory from
``Pipelines.xml`` and the ``find``/``findall`` literals so a new pattern fails the
completeness test until it gets a case here.
"""

from __future__ import annotations

import ast
import hashlib
import os
import pathlib
import re
from collections.abc import Iterable
from typing import Any, cast
from xml.etree import ElementTree

import nornir_buildmanager
import nornir_buildmanager.volumemanager as vm
from nornir_buildmanager.metadatautils import GetOrCreateHistogramNodeHelper

FIXED_DATE = '2020-01-02 03:04:05+00:00'
ROOT_TOKEN = b'{ROOT}'

# sha256[:16] of each VolumeData.xml after ROOT_TOKEN substitution, written by the code at the time of capture.
GOLDEN_SHA = {
    'TEM/0001/TEM/Leveled/Images/VolumeData.xml': '352ce5b1b3049356',
    'TEM/0001/TEM/Leveled/TilePyramid/VolumeData.xml': '5606b8e4adf8d3e9',
    'TEM/0001/TEM/Leveled/Tileset/VolumeData.xml': '1c5295e8416c37c7',
    'TEM/0001/TEM/Leveled/VolumeData.xml': 'ef38de97a0c18ff6',
    'TEM/0001/TEM/Raw8/Images/VolumeData.xml': '352ce5b1b3049356',
    'TEM/0001/TEM/Raw8/TilePyramid/VolumeData.xml': '5606b8e4adf8d3e9',
    'TEM/0001/TEM/Raw8/Tileset/VolumeData.xml': '1c5295e8416c37c7',
    'TEM/0001/TEM/Raw8/VolumeData.xml': 'a4199b2267e1aa4d',
    'TEM/0001/TEM/VolumeData.xml': '22274ae237147af8',
    'TEM/0001/VolumeData.xml': '1e89fb24dd067ff4',
    'TEM/0002/TEM/Leveled/Images/VolumeData.xml': '352ce5b1b3049356',
    'TEM/0002/TEM/Leveled/TilePyramid/VolumeData.xml': '5606b8e4adf8d3e9',
    'TEM/0002/TEM/Leveled/Tileset/VolumeData.xml': '1c5295e8416c37c7',
    'TEM/0002/TEM/Leveled/VolumeData.xml': 'ef38de97a0c18ff6',
    'TEM/0002/TEM/Raw8/Images/VolumeData.xml': '352ce5b1b3049356',
    'TEM/0002/TEM/Raw8/TilePyramid/VolumeData.xml': '5606b8e4adf8d3e9',
    'TEM/0002/TEM/Raw8/Tileset/VolumeData.xml': '1c5295e8416c37c7',
    'TEM/0002/TEM/Raw8/VolumeData.xml': 'a4199b2267e1aa4d',
    'TEM/0002/TEM/VolumeData.xml': '22274ae237147af8',
    'TEM/0002/VolumeData.xml': '7db6f6e9250db7fe',
    'TEM/StosBrute64/VolumeData.xml': '7d0e664fbf25b4ff',
    'TEM/VolumeData.xml': '8c29f17de9603232',
    'VolumeData.xml': '07297a61a34cb925',
}

C = 'Block/Section/Channel'
F = f'{C}/Filter'
SM = 'Block/StosGroup/SectionMappings'

# (inventoried pattern, root xpath from the Volume, query xpath, total matches over all roots) on the
# query fixture (``build_volume(..., extras=True)``: 2 sections x 2 filters). '#v' marks a #Variable or
# format placeholder in the pattern; the query substitutes a value present (or absent) in the fixture.
XPATH_CASES = [
    # Pipelines.xml Iterate / Select, rooted at the Volume
    ('Block', '', 'Block', 1),
    ('Block/Section', '', 'Block/Section', 2),
    ('Block/Section/Channel', '', C, 2),
    ("Block/Section/Channel/Filter[@Name='#v']", '', f"{F}[@Name='Leveled']", 2),
    ('Block/StosGroup/SectionMappings/Transform', '', f'{SM}/Transform', 1),
    ("Block/StosGroup[@Name='StosBrute64']/SectionMappings", '', "Block/StosGroup[@Name='StosBrute64']/SectionMappings", 1),
    ("Block/StosGroup[@Name='#v']", '', "Block/StosGroup[@Name='StosBrute64']", 1),
    ("Block/StosMap[@Name='#v']", '', "Block/StosMap[@Name='PotentialRegistrationChain']", 1),
    # Pipelines.xml, relative to a bound node (Root= or nesting)
    ('Section', 'Block', 'Section', 2),
    ('Channel', 'Block/Section', 'Channel', 2),
    ('Filter', C, 'Filter', 4),
    ("Filter[@Name='#v']", C, "Filter[@Name='Raw8']", 2),
    ("Filter[@Name='#v']", C, "Filter[@Name='Missing']", 0),
    ("Filter[@Name='#v']/TilePyramid", C, "Filter[@Name='Leveled']/TilePyramid", 2),
    ('Transform', C, 'Transform', 6),
    ('Transform', SM, 'Transform', 1),
    ("Transform[@Name='#v']", C, "Transform[@Name='Prune']", 2),
    ("Transform[@Name='Translated_#v']", C, "Transform[@Name='Translated_Prune']", 2),
    ('TransformData', C, 'TransformData', 2),
    ('Image', C, 'Image', 2),
    ('Data', C, 'Data', 2),
    ('Data', F, 'Data', 4),
    ('TilePyramid', F, 'TilePyramid', 4),
    ('Tileset', F, 'Tileset', 4),
    ('ImageSet', F, 'ImageSet', 4),
    ('ImageSet/Level', F, 'ImageSet/Level', 4),
    ('ImageSet/Level/Image', F, 'ImageSet/Level/Image', 4),
    ('Image', f'{F}/ImageSet/Level', 'Image', 4),
    ('Histogram', F, 'Histogram', 4),
    ('Histogram/Image', F, 'Histogram/Image', 4),
    ('Image', f'{F}/Histogram', 'Image', 4),
    ('Data', f'{F}/Histogram', 'Data', 4),
    ('Prune', F, 'Prune', 4),
    ("Prune[@Overlap='#v']", F, "Prune[@Overlap='0.1']", 4),
    ('Image', f'{F}/Prune', 'Image', 4),
    ('Data', f'{F}/Prune', 'Data', 4),
    ("StosGroup[@Name='#v']", 'Block', "StosGroup[@Name='StosBrute64']", 1),
    ("StosGroup[@Name='#v#v']", 'Block', "StosGroup[@Name='StosBrute64']", 1),
    ("StosMap[@Name='#v']", 'Block', "StosMap[@Name='PotentialRegistrationChain']", 1),
    ('SectionMappings', 'Block/StosGroup', 'SectionMappings', 1),
    ('SectionMappings/Transform', 'Block/StosGroup', 'SectionMappings/Transform', 1),
    ("SectionMappings/Transform[@Type='#v']", 'Block/StosGroup', "SectionMappings/Transform[@Type='Grid']", 1),
    ('Mapping', 'Block/StosMap', 'Mapping', 1),
    # Pipelines.xml reporting ColumnXPaths, rooted at their RowXPath (Section for ImageReport, SectionMappings
    # for StosReport); test_report_columns_are_pinned_under_their_row_context checks the pairing
    ("Channel/Filter[@Name='#v']", 'Block/Section', "Channel/Filter[@Name='Raw8']", 2),
    ('Channel/TransformData', 'Block/Section', 'Channel/TransformData', 2),
    ('Channel/Notes', 'Block/Section', 'Channel/Notes', 2),
    ('Channel/Data', 'Block/Section', 'Channel/Data', 2),
    ("Channel/Filter[@Name='#v']/Histogram/Image", 'Block/Section', "Channel/Filter[@Name='Leveled']/Histogram/Image", 2),
    ("Channel/Filter[@Name='#v']/Prune/Image", 'Block/Section', "Channel/Filter[@Name='Raw8']/Prune/Image", 2),
    ('Image', SM, 'Image', 1),
    ('Histogram/Image', SM, 'Histogram/Image', 1),
    # operations/ and volumemanager/ literals; tile.py concatenates the checksum into the Histogram predicate
    ("Histogram[@InputTransformChecksum='#v']", F, "Histogram[@InputTransformChecksum='hist-Leveled']", 2),
    ("Histogram[@InputTransformChecksum='#v']", F, "Histogram[@InputTransformChecksum='stale']", 0),
    ('Block/Section/Channel/Scale', '', f'{C}/Scale', 2),
    ('Section/Channel', 'Block', 'Section/Channel', 2),
    ("Section[@Number='#v']", 'Block', "Section[@Number='2']", 1),
    ('StosGroup', 'Block', 'StosGroup', 1),
    ('StosMap', 'Block', 'StosMap', 1),
    ('NonStosSectionNumbers', 'Block', 'NonStosSectionNumbers', 1),
    ('Scale', C, 'Scale', 2),
    ('Notes', C, 'Notes', 2),
    ('Level', f'{F}/TilePyramid', 'Level', 4),
    ('AutoLevelHint', f'{F}/Histogram', 'AutoLevelHint', 4),
    ("Mapping[@Control='#v']", 'Block/StosMap', "Mapping[@Control='1']", 1),
    ("Mapping[@Control='#v']", 'Block/StosMap', "Mapping[@Control='2']", 0),
    ("*[@Path='#v']", 'Block', "*[@Path='0002']", 1),
    ("*[@Path='#v']", C, "*[@Path='Raw8']", 2),
    ("*[@Path='#v']", 'Block', "*[@Path='9999']", 0),
    # Built at run time outside a find literal (see INDIRECT_PATTERNS)
    ('Filter/TilePyramid/Level', C, 'Filter/TilePyramid/Level', 4),
    ("Filter/TilePyramid/Level[@Downsample='#v']", C, "Filter/TilePyramid/Level[@Downsample='1']", 4),
    ("Level[@Downsample='#v']", f'{F}/TilePyramid', "Level[@Downsample='1']", 4),
    ("Level[@Downsample='#v']", f'{F}/TilePyramid', "Level[@Downsample='1.0']", 0),
    ("Image[@InputTransformChecksum='#v']", SM, "Image[@InputTransformChecksum='abc123']", 1),
    ("SectionMappings[@MappedSectionNumber='#v']", 'Block/StosGroup', "SectionMappings[@MappedSectionNumber='2']", 1),
    ("SectionMappings[@MappedSectionNumber='#v']/Transform[@ControlSectionNumber='#v']", 'Block/StosGroup',
     "SectionMappings[@MappedSectionNumber='2']/Transform[@ControlSectionNumber='1']", 1),
    ("Transform[@ControlSectionNumber='#v']", SM, "Transform[@ControlSectionNumber='1']", 1),
    ("Transform[@MappedSectionNumber='#v']", SM, "Transform[@MappedSectionNumber='2']", 1),
    ("Block[@Name='#v']", '', "Block[@Name='TEM']", 1),
    ("Channel[@Name='#v']", 'Block/Section', "Channel[@Name='TEM']", 2),
]

# Patterns assembled where the source scan cannot see a literal: pattern -> where it is built.
INDIRECT_PATTERNS = {
    'Filter/TilePyramid/Level': 'operations/diagnostics.py (Downsample is None)',
    "Filter/TilePyramid/Level[@Downsample='#v']": 'operations/diagnostics.py',
    "Level[@Downsample='#v']": 'XElementWrapper.GetChildByAttrib (%g for floats) via PyramidLevelHandler',
    "Image[@InputTransformChecksum='#v']": 'operations/block.py SelectBestRegistrationChain template',
    "SectionMappings[@MappedSectionNumber='#v']": 'UpdateOrAddChildByAttrib(..., MappedSectionNumber)',
    "SectionMappings[@MappedSectionNumber='#v']/Transform[@ControlSectionNumber='#v']":
        'operations/block.py TransformXPathTemplate (call site commented out)',
    "Transform[@ControlSectionNumber='#v']": 'pipelinemanager_iterate_filters.py point lookup',
    "Transform[@MappedSectionNumber='#v']": 'pipelinemanager_iterate_filters.py point lookup',
    "Block[@Name='#v']": 'pipelinemanager_iterate_filters.py literal lookup (GetChildByAttrib)',
    "Channel[@Name='#v']": 'pipelinemanager_iterate_filters.py literal lookup (GetChildByAttrib)',
    "Filter[@Name='#v']": 'pipelinemanager_iterate_filters.py literal lookup (GetChildByAttrib)',
}

_PLACEHOLDER = re.compile(r"#\w+|%\(\w+\)[sdg]|%[sdg]|\{[^}]*\}")
_PREDICATE = re.compile(r"\[[^\]]*\]")
_FIND_METHODS = frozenset({'find', 'findall', 'iterfind'})
_PACKAGE_DIR = pathlib.Path(nornir_buildmanager.__file__).parent


def normalize_pattern(xpath: str) -> str:
    """Replace ``#Variable`` tokens and printf / f-string placeholders with ``#v``."""
    return _PLACEHOLDER.sub('#v', xpath)


def pipeline_xpaths() -> set[str]:
    """Raw XPath, RowXPath, and ColumnXPaths values in the packaged ``Pipelines.xml``."""
    tree = ElementTree.parse(_PACKAGE_DIR / 'config' / 'Pipelines.xml')
    xpaths = {e.attrib['XPath'] for e in tree.iter() if 'XPath' in e.attrib}
    xpaths |= {e.attrib['RowXPath'] for e in tree.iter() if 'RowXPath' in e.attrib}
    for e in tree.iter():
        xpaths |= set(filter(None, e.attrib.get('ColumnXPaths', '').split(',')))
    return xpaths


def pipeline_patterns() -> set[str]:
    return {normalize_pattern(x) for x in pipeline_xpaths()}


def root_context(xpath: str) -> str:
    """Tag path of *xpath* with predicates dropped; which instance a predicate picks does not change the context."""
    return _PREDICATE.sub('', xpath)


def report_column_patterns() -> set[tuple[str, str]]:
    """(row context, column pattern) for every reporting ``ColumnXPaths`` entry in ``Pipelines.xml``.

    ``GenerateTableReport`` runs each column ``findall`` below every ``RowXPath`` match of the
    pipeline's ``ReportingElement`` Select, so that Select joined to ``RowXPath`` is the context.
    """
    found = set()
    for pipeline in ElementTree.parse(_PACKAGE_DIR / 'config' / 'Pipelines.xml').iter('Pipeline'):
        reporting = None
        for element in pipeline.iter():
            if element.tag == 'Select' and element.get('VariableName') == 'ReportingElement':
                reporting = element.attrib['XPath']
            if 'RowXPath' not in element.attrib:
                continue
            if reporting is None:
                raise ValueError(f"Pipeline {pipeline.get('Name')} reports rows without a ReportingElement Select")
            context = root_context(f"{reporting}/{element.attrib['RowXPath']}")
            found |= {(context, normalize_pattern(column))
                      for column in element.attrib.get('ColumnXPaths', '').split(',') if column}
    return found


def _literal_xpath(node: ast.expr) -> str | None:
    """Text of a string XPath argument with concatenated or f-string values as ``#v``; None if not built from literals."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        return ''.join(str(v.value) if isinstance(v, ast.Constant) else '#v' for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _literal_xpath(node.left)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_xpath(node.left), _literal_xpath(node.right)
        if left is None and right is None:
            return None
        return ('#v' if left is None else left) + ('#v' if right is None else right)
    return None


def source_patterns() -> set[str]:
    """XPaths passed to ``find``/``findall``/``iterfind`` in ``operations/`` and ``volumemanager/``.

    Parsed rather than grepped so calls split across lines or built by ``+`` are seen. An
    argument that is a bare variable is invisible here and belongs in ``INDIRECT_PATTERNS``.
    The leading capital or ``*`` skips ``str.find`` calls.
    """
    found = set()
    for subdir in ('operations', 'volumemanager'):
        for path in (_PACKAGE_DIR / subdir).rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _FIND_METHODS and node.args):
                    xpath = _literal_xpath(node.args[0])
                    if xpath and (xpath[0].isupper() or xpath[0] == '*'):
                        found.add(normalize_pattern(xpath))
    return found


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(b'x')


def _add_query_extras(block: Any) -> None:
    """Nodes only some inventoried queries reach; kept out of the golden-bytes fixture."""
    block.MarkSectionsAsDamaged([2])
    for channel in block.findall('Section/Channel'):
        channel.SetScale(2.0)
        channel.UpdateOrAddChild(vm.ImageNode.Create('channel.png'))
        channel.UpdateOrAddChild(vm.DataNode.Create('channel.data.xml'))
        _, transform = channel.UpdateOrAddChildByAttrib(vm.TransformNode.Create('Translated_Prune', 'Mosaic'), 'Name')
        _touch(transform.FullPath)
        for filter_node in channel.findall('Filter'):
            filter_node.UpdateOrAddChild(vm.DataNode.Create('filter.data.xml'))
            histogram = filter_node.find('Histogram')
            histogram.GetOrCreateAutoLevelHint()
            histogram.InputTransformChecksum = f'hist-{filter_node.Name}'
            filter_node.find('Prune').UpdateOrAddChild(vm.DataNode.Create('prune.data.xml'))
    mappings = block.find('StosGroup/SectionMappings')
    mappings.UpdateOrAddChild(vm.ImageNode.Create('2-1.png', InputTransformChecksum='abc123'))
    GetOrCreateHistogramNodeHelper(mappings, 'warpHistogram_2-1.xml', 'warpHistogram_2-1.png', Type='WarpHistogram_Grid')


def build_volume(root: str, sections: Iterable[int] = (1, 2), filters: Iterable[str] = ('Raw8', 'Leveled'),
                 notes: str = 'notes text \u00b5m', extras: bool = False) -> None:
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
    if extras:
        _add_query_extras(block)

    for element in volume.iter():
        if 'CreationDate' in element.attrib:
            element.attrib['CreationDate'] = FIXED_DATE
    vm.VolumeManager.Save(volume)


def xml_files(root: str) -> list[str]:
    found = []
    for dirpath, _, filenames in os.walk(root):
        if 'VolumeData.xml' in filenames:
            found.append(os.path.relpath(os.path.join(dirpath, 'VolumeData.xml'), root).replace(os.sep, '/'))
    return sorted(found)


def snapshot(root: str) -> dict[str, tuple[int, int, bytes]]:
    """relpath -> (inode, mtime_ns, bytes); inode changes on every tmp+replace write."""
    result = {}
    for rel in xml_files(root):
        path = os.path.join(root, rel)
        stat = os.stat(path)
        with open(path, 'rb') as handle:
            result[rel] = (stat.st_ino, stat.st_mtime_ns, handle.read())
    return result


def written(before: dict, after: dict) -> set[str]:
    return {rel for rel in after if before.get(rel) != after[rel]}


def normalized_sha(root: str, raw: bytes) -> str:
    return hashlib.sha256(raw.replace(root.encode('utf-8'), ROOT_TOKEN)).hexdigest()[:16]


def merged_oracle(dirpath: str) -> ElementTree.Element:
    """Plain-ElementTree view of the volume with every ``*_Link`` replaced by its file's root.

    Independent of the wrapper's link handling; links only occur as direct children
    of a container's root element in today's layout.
    """
    element = ElementTree.parse(os.path.join(dirpath, 'VolumeData.xml')).getroot()
    for index, child in enumerate(list(element)):
        if child.tag.endswith('_Link'):
            element[index] = merged_oracle(os.path.join(dirpath, child.attrib['Path']))
    return element


def canonical(element: ElementTree.Element, skip_root_path: bool = True) -> tuple:
    """Tag, ordered attributes, whitespace-normalized text, and children, recursively."""
    attrib = [(k, v) for k, v in element.attrib.items() if not (skip_root_path and k == 'Path')]
    text = (element.text or '').strip() or None
    return element.tag, tuple(attrib), text, tuple(canonical(child, False) for child in element)


def ancestry_key(element: Any, parents: dict | None = None) -> tuple:
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
