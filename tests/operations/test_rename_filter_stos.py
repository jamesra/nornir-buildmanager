"""RenameFilter points stos transforms at the renamed filter.

RC1_Crush, 2026-11-11: every stos transform recorded ControlFilterName/MappedFilterName="Raw8". After
RenameFilter Raw8 -> Quantized (or a re-import into Quantized) those transforms named a missing filter, and the
next validating stage would delete every unlocked one (SectionMappingsNode.CleanTransformsIfInvalid).
"""
from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from typing import cast

import pytest
from nornir_buildmanager import build
from nornir_buildmanager.operations import block as block_ops
from nornir_buildmanager.volumemanager import (
    BlockNode,
    ChannelNode,
    FilterNode,
    SectionNode,
    StosGroupNode,
    TransformNode,
    VolumeManager,
    XContainerElementWrapper,
)

STOS_TEXT = "control.png\nmapped.png\n0\n0\n1 1 1 1\n1 1 1 1\n\nRigid2DTransform_double_2_2 vp 3 0 0 0 fp 3 0 0 0\n"


def _section(block: BlockNode, number: int, filter_name: str) -> None:
    _, section = block.UpdateOrAddChildByAttrib(SectionNode.Create(number), 'Number')
    _, channel = section.UpdateOrAddChildByAttrib(ChannelNode.Create('TEM'), 'Name')
    channel.UpdateOrAddChildByAttrib(FilterNode.Create(filter_name), 'Name')


def _transform(group: StosGroupNode, mapped: int, control: int, locked: bool) -> TransformNode:
    _, mapping = group.GetOrCreateSectionMapping(mapped)
    path = StosGroupNode.GenerateStosFilenameFromParts(mapped, control, 'TEM', 'Raw8', 'TEM', 'Raw8')
    node = TransformNode.Create(str(control), 'Grid', path, {
        'ControlSectionNumber': str(control), 'MappedSectionNumber': str(mapped),
        'ControlChannelName': 'TEM', 'ControlFilterName': 'Raw8',
        'MappedChannelName': 'TEM', 'MappedFilterName': 'Raw8'})
    mapping.append(node)
    os.makedirs(os.path.dirname(node.FullPath), exist_ok=True)
    with open(node.FullPath, 'w') as f:
        f.write(STOS_TEXT)
    node.Locked = locked
    return node


def _make_volume(base: str, filters: dict[int, str]) -> tuple[str, XContainerElementWrapper]:
    path = os.path.join(base, 'vol')
    volume = cast(XContainerElementWrapper, VolumeManager.Load(path, Create=True))
    _, block = volume.UpdateOrAddChildByAttrib(BlockNode.Create('TEM'), 'Name')
    for number, filter_name in filters.items():
        _section(block, number, filter_name)
    _, group = block.GetOrCreateStosGroup('Grid16', 16)
    _transform(group, 2, 1, locked=True)
    _transform(group, 3, 2, locked=False)
    return path, volume


@pytest.fixture
def base() -> Iterator[str]:
    path = tempfile.mkdtemp(prefix='nornir_rename_filter_stos_')
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _transforms(volume_dir: str) -> dict[tuple[int, int], TransformNode]:
    volume = VolumeManager.Load(volume_dir)
    group = volume.find('Block/StosGroup')
    return {(int(t.MappedSectionNumber), int(t.ControlSectionNumber)): t
            for t in group.findall('SectionMappings/Transform')}


def test_rename_filter_updates_transforms_and_files(base: str) -> None:
    # Sections 1 and 2 hold the crushed filter as Raw8; section 3 was re-imported straight into Quantized.
    volume_dir, volume = _make_volume(base, {1: 'Raw8', 2: 'Raw8', 3: 'Quantized'})
    VolumeManager.Save(volume)

    build.Execute([volume_dir, '-debug', 'RenameFilter', '-InputFilter', 'Raw8', '-OutputFilter', 'Quantized'])

    transforms = _transforms(volume_dir)
    assert set(transforms) == {(2, 1), (3, 2)}
    for t in transforms.values():
        assert (t.ControlFilterName, t.MappedFilterName) == ('Quantized', 'Quantized')
        assert t.Path == StosGroupNode.GenerateStosFilenameFromParts(
            t.MappedSectionNumber, t.ControlSectionNumber, 'TEM', 'Quantized', 'TEM', 'Quantized')
        assert os.path.exists(t.FullPath)
    assert transforms[(2, 1)].Locked
    assert not transforms[(3, 2)].Locked

    # The unlocked transform now survives the validation that used to delete it.
    volume = VolumeManager.Load(volume_dir)
    mapping = volume.find('Block/StosGroup/SectionMappings[@MappedSectionNumber="3"]')
    cleaned, reason = mapping.CleanTransformsIfInvalid()
    assert not cleaned, reason


def test_section_that_still_has_the_old_filter_keeps_its_name(base: str) -> None:
    # Section 1 still holds Raw8 (legacy); section 2 has only Quantized.
    _, volume = _make_volume(base, {1: 'Raw8', 2: 'Quantized', 3: 'Raw8'})
    group = volume.find('Block/StosGroup')
    transform = next(t for t in group.findall('SectionMappings/Transform') if t.MappedSectionNumber == 2)
    old_path = transform.Path

    assert block_ops.RenameStosTransformFilter(transform, 'Raw8', 'Quantized') is not None
    assert transform.ControlFilterName == 'Raw8'
    assert transform.MappedFilterName == 'Quantized'
    assert transform.Path != old_path and os.path.exists(transform.FullPath)

    # Nothing left to change: a second call is a no-op.
    assert block_ops.RenameStosTransformFilter(transform, 'Raw8', 'Quantized') is None
