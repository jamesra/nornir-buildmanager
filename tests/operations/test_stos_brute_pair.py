"""StosBrutePair: one section pair through StosBruteAll's path, and the StosMap tree update it sidesteps.

RC1 (2026-11-22): 306-307 had to be brute-registered on its own; AlignSections drops hand-added mappings.
"""

from __future__ import annotations

import logging
from unittest import mock
from xml.etree import ElementTree

import nornir_buildmanager.operations.block as block_mod
import nornir_imageregistration.settings
import pytest
from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations import stos_brute_pairs

_BRUTE_FORCE = nornir_imageregistration.settings.SliceToSliceMethod.BruteForce
_PIPELINES_XML = block_mod.__file__.replace('operations', 'config').replace('block.py', 'Pipelines.xml')


def _block(sections: dict[int, object]) -> mock.Mock:
    block_node = mock.Mock()
    block_node.Name = 'TEM'
    block_node.GetSection.side_effect = sections.get
    block_node.GetOrCreateStosGroup.return_value = (False, mock.Mock())
    return block_node


def _section() -> mock.Mock:
    section = mock.Mock()
    section.MatchChannelFilterPattern.return_value = [mock.Mock()]
    return section


def _run(block_node: mock.Mock, control: int = 307, mapped: int = 306, **kwargs) -> tuple[list, dict]:
    seen: dict[str, object] = {}

    def _fake_pairs(Parameters, pair_tasks, mapping_count, stos_group_node, Method, Logger, executor, **kw):
        seen.update(tasks=pair_tasks, method=Method, executor=executor, kwargs=kw, group=stos_group_node)
        yield stos_group_node

    with mock.patch.object(block_mod, '_stos_brute_pairs', side_effect=_fake_pairs), \
            mock.patch.object(block_mod, 'brute_stos_needs_replacement', return_value=True), \
            mock.patch.object(block_mod.os, 'makedirs'):
        out = list(block_mod.StosBrutePair({'Downsample': '64'}, block_node, control, mapped, 'Blob',
                                           mock.Mock(), **kwargs))
    return out, seen


def test_pair_runs_brute_force_with_flip_check_and_masks_in_process() -> None:
    block_node = _block({307: _section(), 306: _section()})
    out, seen = _run(block_node, OutputGroup='StosBrute64', Method='LogPolar', NoFlipCheck=True)
    block_node.GetOrCreateStosGroup.assert_called_once_with('StosBrute64', downsample=64)
    assert out == [seen['group']]
    assert seen['method'] is _BRUTE_FORCE
    assert isinstance(seen['executor'], stos_brute_pairs.InProcessPairExecutor)
    assert seen['kwargs']['NoFlipCheck'] is False
    assert [(t.mapped_section, t.needs_work) for t in seen['tasks']] == [(306, True)]


def test_pair_enumerates_the_control_sections_filters() -> None:
    control, mapped = _section(), _section()
    block_node = _block({307: control, 306: mapped})
    _run(block_node)
    control.MatchChannelFilterPattern.assert_called_with('*', 'Blob')
    mapped.MatchChannelFilterPattern.assert_called_with('*', 'Blob')


@pytest.mark.parametrize('sections, control, mapped', [
    ({307: 1}, 307, 306),
    ({306: 1}, 307, 306),
    ({307: 1, 306: 1}, 307, 307),
])
def test_pair_with_missing_or_equal_sections_is_an_error(sections: dict, control: int, mapped: int) -> None:
    block_node = _block({k: _section() for k in sections})
    with pytest.raises(NornirUserException):
        _run(block_node, control, mapped)
    block_node.GetOrCreateStosGroup.assert_not_called()


def test_pair_without_matching_filters_is_an_error() -> None:
    empty = mock.Mock()
    empty.MatchChannelFilterPattern.return_value = []
    with pytest.raises(NornirUserException, match='no filter'):
        _run(_block({307: _section(), 306: empty}))


def test_pipeline_passes_arguments_to_stos_brute_pair() -> None:
    tree = ElementTree.parse(_PIPELINES_XML)
    pipeline = tree.getroot().find("Pipeline[@Name='StosBrutePair']")
    assert pipeline is not None
    flags = {a.get('flag'): a for a in pipeline.find('Arguments')}
    assert set(flags) == {'-Control', '-Mapped', '-Downsample', '-Filters', '-OutputGroup'}
    call = pipeline.find('PythonCall')
    assert call.get('Function') == 'block.StosBrutePair'
    assert call.get('OutputGroup') == '#OutputGroup#StosBruteDownsample'
    assert call.find("Parameters/Entry[@Name='Downsample']").get('Value') == '#StosBruteDownsample'


class _Map:
    """Minimal StosMap: mappings by control, removal by control."""

    def __init__(self, mappings: dict[int, set[int]]) -> None:
        self._mappings = [_Mapping(c, m) for c, m in mappings.items()]

    @property
    def Mappings(self):
        yield from self._mappings

    def GetMappingsForControl(self, control: int):
        return (m for m in self._mappings if m.Control == control)

    def RemoveMapping(self, control: int) -> None:
        self._mappings = [m for m in self._mappings if m.Control != control]

    def remove(self, mapping) -> None:
        self._mappings.remove(mapping)

    def append(self, mapping) -> None:
        self._mappings.append(mapping)


class _Mapping:
    def __init__(self, control: int, mapped: set[int]) -> None:
        self.Control = control
        self.Mapped = set(mapped)

    def AddMapping(self, section: int) -> None:
        self.Mapped.add(section)

    def RemoveMapping(self, section: int) -> None:
        self.Mapped.discard(section)


def _tree(children: dict[int, list[int]]) -> mock.Mock:
    nodes = {}
    for control, mapped in children.items():
        node = mock.Mock()
        node.SectionNumber = control
        node.Children = [mock.Mock(SectionNumber=s) for s in mapped]
        node.ChildSectionNumbers = list(mapped)
        nodes[control] = node
    rt = mock.Mock()
    rt.Nodes = nodes
    return rt


def test_tree_update_reports_removing_a_hand_added_mapping_as_modified() -> None:
    stos_map = _Map({305: {304, 306}})
    modified = block_mod.UpdateStosMapWithRegistrationTree(stos_map, _tree({305: [304]}), False,
                                                           logging.getLogger('test'))
    assert modified is True
    assert [(m.Control, m.Mapped) for m in stos_map.Mappings] == [(305, {304})]

