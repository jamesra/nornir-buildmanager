"""Tests for VikingXML export, including Version 2 nested Sections / StosGroup."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ETree

import pytest

from nornir_buildmanager.operations import vikingxml
from nornir_buildmanager.volumemanager.blocknode import BlockNode
from nornir_buildmanager.volumemanager.mappingnode import MappingNode
from nornir_buildmanager.volumemanager.sectionmappingsnode import SectionMappingsNode
from nornir_buildmanager.volumemanager.sectionnode import SectionNode
from nornir_buildmanager.volumemanager.stosgroupnode import StosGroupNode
from nornir_buildmanager.volumemanager.stosmapnode import StosMapNode
from nornir_buildmanager.volumemanager.transformnode import TransformNode
from nornir_buildmanager.volumemanager.volumenode import VolumeNode


def _build_volume_with_stos_groups(
        group_names: list[str],
        volume_path: str = "/tmp/test-volume",
) -> VolumeNode:
    """Build a minimal volume with one shared StosMap and one transform per group."""
    volume = VolumeNode.Create(Name="TestVolume", Path=volume_path)
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


def _write_pending_stos_files(volume: VolumeNode, group_name: str) -> list[str]:
    """Create on-disk .stos files for StosMap-selected transforms; return their full paths."""
    block = volume.find('Block')
    assert block is not None
    group = block.GetChildByAttrib('StosGroup', 'Name', group_name)
    assert group is not None
    written: list[str] = []
    for section_mapping in group.findall('SectionMappings'):
        for transform in section_mapping.findall('Transform'):
            os.makedirs(os.path.dirname(transform.FullPath), exist_ok=True)
            with open(transform.FullPath, 'w', encoding='utf-8') as handle:
                handle.write(f'stos:{transform.Path}\n')
            written.append(transform.FullPath)
    return written


def _parse_stos(stos_group_name: str | list[str] | None,
                group_names: list[str] | None = None) -> ETree.Element:
    if group_names is None:
        group_names = ['GroupA', 'GroupB']
    output_volume = ETree.Element('Volume', {'Version': '2', 'num_stos': '0'})
    vikingxml.ParseStos(_build_volume_with_stos_groups(group_names),
                        output_volume,
                        'TestMap',
                        stos_group_name)
    return output_volume


def _stos_group_names(output_volume: ETree.Element) -> list[str]:
    return [node.attrib['Name'] for node in output_volume.findall('StosGroup')]


def _nested_stos(output_volume: ETree.Element) -> list[ETree.Element]:
    return list(output_volume.findall('StosGroup/stos'))


def test_normalize_stos_group_names() -> None:
    assert vikingxml._normalize_stos_group_names(None) == []
    assert vikingxml._normalize_stos_group_names('Grid') == ['Grid']
    assert vikingxml._normalize_stos_group_names(['Grid', 'SliceToVolume1']) == ['Grid', 'SliceToVolume1']
    assert vikingxml._normalize_stos_group_names(['Grid', '', 'SliceToVolume1']) == ['Grid', 'SliceToVolume1']


def test_parse_stos_single_group_string() -> None:
    output = _parse_stos('GroupA')
    assert _stos_group_names(output) == ['GroupA']
    assert len(_nested_stos(output)) == 1
    assert 'GroupName' not in _nested_stos(output)[0].attrib


def test_parse_stos_multiple_groups() -> None:
    output = _parse_stos(['GroupA', 'GroupB'])
    assert _stos_group_names(output) == ['GroupA', 'GroupB']
    assert len(output.findall('StosGroup')) == 2
    assert len(_nested_stos(output)) == 2


def test_parse_stos_sets_num_stos_for_multiple_groups() -> None:
    output = _parse_stos(['GroupA', 'GroupB'])
    assert output.attrib['num_stos'] == '2'
    assert len(_nested_stos(output)) == 2


def test_parse_stos_skips_missing_group() -> None:
    output = _parse_stos(['GroupA', 'MissingGroup'], group_names=['GroupA'])
    assert output.attrib['num_stos'] == '1'
    assert _stos_group_names(output) == ['GroupA']


@pytest.mark.parametrize('stos_group_name', [None, []])
def test_parse_stos_without_groups(stos_group_name: str | list[str] | None) -> None:
    output = _parse_stos(stos_group_name, group_names=['GroupA'])
    assert output.attrib['num_stos'] == '0'
    assert output.findall('StosGroup') == []
    assert _nested_stos(output) == []


def test_parse_stos_creates_zip_with_map_filtered_members() -> None:
    temp_dir = tempfile.mkdtemp(prefix="nornir-vikingxml-")
    try:
        volume = _build_volume_with_stos_groups(['GroupA'], volume_path=temp_dir)
        block = volume.find('Block')
        assert block is not None
        group = block.GetChildByAttrib('StosGroup', 'Name', 'GroupA')
        assert group is not None

        written = _write_pending_stos_files(volume, 'GroupA')
        assert len(written) == 1

        off_map = os.path.join(group.FullPath, 'off-map.stos')
        with open(off_map, 'w', encoding='utf-8') as handle:
            handle.write('off-map\n')

        output = ETree.Element('Volume', {'num_stos': '0'})
        vikingxml.ParseStos(volume, output, 'TestMap', 'GroupA')
        group_elem = output.find('StosGroup')
        assert group_elem is not None
        assert group_elem.attrib['Name'] == 'GroupA'
        assert group_elem.attrib['zip'] == os.path.join(block.Path, 'GroupA.zip')

        zip_path = os.path.join(block.FullPath, 'GroupA.zip')
        assert os.path.isfile(zip_path)

        expected_members = {node.attrib['path'] for node in group_elem.findall('stos')}
        assert expected_members == {'GroupA-4-5.stos'}
        assert all('/' not in name and '\\' not in name for name in expected_members)
        with zipfile.ZipFile(zip_path, 'r') as archive:
            actual = {name.replace('\\', '/') for name in archive.namelist()}
        assert actual == expected_members
        assert all('/' not in name for name in actual)
        assert not any(name.endswith('off-map.stos') for name in actual)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_parse_stos_omits_zip_when_sources_missing() -> None:
    output = _parse_stos('GroupA', group_names=['GroupA'])
    group = output.find('StosGroup')
    assert group is not None
    assert 'zip' not in group.attrib


def test_parse_stos_skips_zip_rewrite_when_fresh() -> None:
    temp_dir = tempfile.mkdtemp(prefix="nornir-vikingxml-")
    try:
        volume = _build_volume_with_stos_groups(['GroupA'], volume_path=temp_dir)
        block = volume.find('Block')
        assert block is not None
        _write_pending_stos_files(volume, 'GroupA')

        output = ETree.Element('Volume', {'num_stos': '0'})
        vikingxml.ParseStos(volume, output, 'TestMap', 'GroupA')
        zip_path = os.path.join(block.FullPath, 'GroupA.zip')
        assert os.path.isfile(zip_path)
        first_mtime = os.path.getmtime(zip_path)

        time.sleep(0.05)
        output2 = ETree.Element('Volume', {'num_stos': '0'})
        vikingxml.ParseStos(volume, output2, 'TestMap', 'GroupA')
        group = output2.find('StosGroup')
        assert group is not None
        assert group.attrib['zip'] == os.path.join(block.Path, 'GroupA.zip')
        assert os.path.getmtime(zip_path) == first_mtime
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_parse_stos_rewrites_zip_when_stale() -> None:
    temp_dir = tempfile.mkdtemp(prefix="nornir-vikingxml-")
    try:
        volume = _build_volume_with_stos_groups(['GroupA'], volume_path=temp_dir)
        block = volume.find('Block')
        assert block is not None
        written = _write_pending_stos_files(volume, 'GroupA')

        output = ETree.Element('Volume', {'num_stos': '0'})
        vikingxml.ParseStos(volume, output, 'TestMap', 'GroupA')
        zip_path = os.path.join(block.FullPath, 'GroupA.zip')
        first_mtime = os.path.getmtime(zip_path)

        time.sleep(0.05)
        newer = first_mtime + 5.0
        os.utime(written[0], (newer, newer))

        output2 = ETree.Element('Volume', {'num_stos': '0'})
        vikingxml.ParseStos(volume, output2, 'TestMap', 'GroupA')
        group = output2.find('StosGroup')
        assert group is not None
        assert group.attrib['zip'] == os.path.join(block.Path, 'GroupA.zip')
        assert os.path.getmtime(zip_path) > first_mtime
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_create_vikingxml_version_2_sections_wrapper() -> None:
    temp_dir = tempfile.mkdtemp(prefix="nornir-vikingxml-")
    try:
        volume = VolumeNode.Create(Name="TestVolume", Path=temp_dir)
        block = BlockNode.Create(Name="TEM", Path="TEM")
        volume.append(block)
        section = SectionNode.Create(1, "0001", "0001")
        block.append(section)
        os.makedirs(section.FullPath, exist_ok=True)

        vikingxml.CreateVikingXML(VolumeNode=volume, OutputFile="Test")
        xml_path = os.path.join(temp_dir, "Test.VikingXML")
        assert os.path.isfile(xml_path)

        root = ETree.parse(xml_path).getroot()
        assert root.attrib.get('Version') == '2'
        assert root.find('Sections') is not None
        assert root.find('Section') is None
        assert len(root.findall('Sections/Section')) == 1
        assert root.attrib.get('num_sections') == '1'

        with open(xml_path, encoding='utf-8') as handle:
            text = handle.read()
        assert '\n' in text
        assert '  <Sections>' in text or '\t<Sections>' in text
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
