"""Tests for VikingXML export, including multi StosGroup support."""

from __future__ import annotations

import xml.etree.ElementTree as ETree

import pytest

from nornir_buildmanager.operations import vikingxml
from nornir_buildmanager.volumemanager.blocknode import BlockNode
from nornir_buildmanager.volumemanager.mappingnode import MappingNode
from nornir_buildmanager.volumemanager.sectionmappingsnode import SectionMappingsNode
from nornir_buildmanager.volumemanager.stosgroupnode import StosGroupNode
from nornir_buildmanager.volumemanager.stosmapnode import StosMapNode
from nornir_buildmanager.volumemanager.transformnode import TransformNode
from nornir_buildmanager.volumemanager.volumenode import VolumeNode


def _build_volume_with_stos_groups(group_names: list[str]) -> VolumeNode:
    """Build a minimal volume with one shared StosMap and one transform per group."""
    volume = VolumeNode.Create(Name="TestVolume", Path="/tmp/test-volume")
    block = BlockNode.Create(Name="TEM", Path="TEM")
    volume.append(block)

    stos_map = StosMapNode.Create(Name="TestMap")
    stos_map.append(MappingNode.Create(5, [4]))
    block.append(stos_map)

    for group_name in group_names:
        group = StosGroupNode.Create(Name=group_name, Downsample=32)
        section_mapping = SectionMappingsNode.Create(MappedSectionNumber=4)
        transform = TransformNode.Create(Name="stos", Type="Grid", Path=f"{group_name}-4-5.stos")
        transform.ControlSectionNumber = 5
        transform.MappedSectionNumber = 4
        section_mapping.append(transform)
        group.append(section_mapping)
        block.append(group)

    return volume


def _parse_stos_group_names(stos_group_name: str | list[str] | None) -> list[str]:
    output_volume = ETree.Element('Volume', {'num_stos': '0'})
    vikingxml.ParseStos(_build_volume_with_stos_groups(['GroupA', 'GroupB']),
                        output_volume,
                        'TestMap',
                        stos_group_name)
    return [node.attrib['GroupName'] for node in output_volume.findall('stos')]


def test_normalize_stos_group_names() -> None:
    assert vikingxml._normalize_stos_group_names(None) == []
    assert vikingxml._normalize_stos_group_names('Grid') == ['Grid']
    assert vikingxml._normalize_stos_group_names(['Grid', 'SliceToVolume1']) == ['Grid', 'SliceToVolume1']
    assert vikingxml._normalize_stos_group_names(['Grid', '', 'SliceToVolume1']) == ['Grid', 'SliceToVolume1']


def test_parse_stos_single_group_string() -> None:
    group_names = _parse_stos_group_names('GroupA')
    assert group_names == ['GroupA']


def test_parse_stos_multiple_groups() -> None:
    group_names = _parse_stos_group_names(['GroupA', 'GroupB'])
    assert group_names == ['GroupA', 'GroupB']


def test_parse_stos_sets_num_stos_for_multiple_groups() -> None:
    output_volume = ETree.Element('Volume', {'num_stos': '0'})
    vikingxml.ParseStos(_build_volume_with_stos_groups(['GroupA', 'GroupB']),
                        output_volume,
                        'TestMap',
                        ['GroupA', 'GroupB'])

    assert output_volume.attrib['num_stos'] == '2'
    assert len(output_volume.findall('stos')) == 2


def test_parse_stos_skips_missing_group() -> None:
    output_volume = ETree.Element('Volume', {'num_stos': '0'})
    vikingxml.ParseStos(_build_volume_with_stos_groups(['GroupA']),
                        output_volume,
                        'TestMap',
                        ['GroupA', 'MissingGroup'])

    assert output_volume.attrib['num_stos'] == '1'
    assert [node.attrib['GroupName'] for node in output_volume.findall('stos')] == ['GroupA']


@pytest.mark.parametrize('stos_group_name', [None, []])
def test_parse_stos_without_groups(stos_group_name: str | list[str] | None) -> None:
    output_volume = ETree.Element('Volume', {'num_stos': '0'})
    vikingxml.ParseStos(_build_volume_with_stos_groups(['GroupA']),
                        output_volume,
                        'TestMap',
                        stos_group_name)

    assert output_volume.attrib['num_stos'] == '0'
    assert output_volume.findall('stos') == []
