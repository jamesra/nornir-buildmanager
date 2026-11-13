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
from nornir_imageregistration.files import stosfile

STOS_TEXT = "control.png\nmapped.png\n0\n0\n1 1 1 1\n1 1 1 1\nRigid2DTransform_double_2_2 vp 3 0 0 0 fp 3 0 0 0\n"


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


def test_rename_filter_moves_folder_without_nesting(base: str) -> None:
    # RC1_Crush 2026-11-11: the filter folder ended up at Quantized/Raw8 because setting a container's Path
    # creates the new directory before shutil.move, which then moves the source inside it.
    volume_dir, volume = _make_volume(base, {1: 'Raw8', 2: 'Raw8', 3: 'Quantized'})
    VolumeManager.Save(volume)
    channel_dir = os.path.join(volume_dir, 'TEM', '0001', 'TEM')
    raw8_dir = os.path.join(channel_dir, 'Raw8')
    assert os.path.isfile(os.path.join(raw8_dir, 'VolumeData.xml'))
    os.makedirs(os.path.join(raw8_dir, 'Images'))
    with open(os.path.join(raw8_dir, 'Images', 'marker.txt'), 'w') as f:
        f.write('x')

    build.Execute([volume_dir, '-debug', 'RenameFilter', '-InputFilter', 'Raw8', '-OutputFilter', 'Quantized'])

    quantized_dir = os.path.join(channel_dir, 'Quantized')
    assert not os.path.exists(raw8_dir)
    assert not os.path.exists(os.path.join(quantized_dir, 'Raw8'))
    assert os.path.isfile(os.path.join(quantized_dir, 'Images', 'marker.txt'))
    filter_node = VolumeManager.Load(volume_dir).find("Block/Section[@Number='1']/Channel/Filter")
    assert (filter_node.Name, filter_node.Path) == ('Quantized', 'Quantized')


def test_rename_filter_renames_manual_overrides(base: str) -> None:
    # RC1_Crush 2026-11-11: 91 Grid16 hand fixes stayed Manual/*_ctrl-TEM_Raw8_map-TEM_Raw8.stos after the rename,
    # so PathToManualTransform (which looks up Manual/<root basename>) no longer found them.
    volume_dir, volume = _make_volume(base, {1: 'Raw8', 2: 'Raw8', 3: 'Raw8'})
    VolumeManager.Save(volume)
    group = cast(StosGroupNode, volume.find('Block/StosGroup'))
    manual_text = STOS_TEXT.replace('vp 3 0 0 0', 'vp 3 0 5 7')
    os.makedirs(group.ManualInputDirectory, exist_ok=True)
    old_manual = os.path.join(group.ManualInputDirectory,
                              StosGroupNode.GenerateStosFilenameFromParts(2, 1, 'TEM', 'Raw8', 'TEM', 'Raw8'))
    with open(old_manual, 'w') as f:
        f.write(manual_text)

    build.Execute([volume_dir, '-debug', 'RenameFilter', '-InputFilter', 'Raw8', '-OutputFilter', 'Quantized'])

    transform = _transforms(volume_dir)[(2, 1)]
    group = cast(StosGroupNode, transform.FindParent('StosGroup'))
    manual = group.PathToManualTransform(transform.FullPath)
    assert manual is not None, os.listdir(group.ManualInputDirectory)
    assert not os.path.exists(old_manual)
    with open(manual) as f:
        assert f.read() == manual_text


def test_existing_manual_override_under_new_name_is_kept(base: str) -> None:
    _, volume = _make_volume(base, {1: 'Quantized', 2: 'Quantized', 3: 'Quantized'})
    group = cast(StosGroupNode, volume.find('Block/StosGroup'))
    transform = next(t for t in group.findall('SectionMappings/Transform') if t.MappedSectionNumber == 2)
    os.makedirs(group.ManualInputDirectory, exist_ok=True)
    names = {kind: StosGroupNode.GenerateStosFilenameFromParts(2, 1, 'TEM', kind, 'TEM', kind)
             for kind in ('Raw8', 'Quantized')}
    for kind, name in names.items():
        with open(os.path.join(group.ManualInputDirectory, name), 'w') as f:
            f.write(kind)

    assert block_ops.RenameStosTransformFilter(transform, 'Raw8', 'Quantized') is not None
    for kind, name in names.items():
        with open(os.path.join(group.ManualInputDirectory, name)) as f:
            assert f.read() == kind


def _add_assembled_image(volume: XContainerElementWrapper, section: int, downsample: int) -> str:
    image_filter = cast(FilterNode, volume.find(f"Block/Section[@Number='{section}']/Channel/Filter"))
    image = image_filter.Imageset.GetOrCreateImage(downsample, image_filter.DefaultImageName('.png'),
                                                   GenerateData=False)
    os.makedirs(os.path.dirname(image.FullPath), exist_ok=True)
    with open(image.FullPath, 'w') as f:
        f.write(f'image {section}')
    return image.FullPath


def _write_stos(path: str, control: str, mapped: str) -> None:
    """A .stos naming *control* and *mapped* (relative to its folder) with masks."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    lines = [line for line in STOS_TEXT.split('\n') if line]
    lines[0], lines[1] = os.path.relpath(control, folder), os.path.relpath(mapped, folder)
    mask = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(control)))),
                        'Mask', 'Images', '016', 'mask.png')
    lines[7:7] = ['two_user_supplied_masks:', os.path.relpath(mask, folder), os.path.relpath(mask, folder)]
    with open(path, 'w') as f:
        f.write('\n'.join(lines))


def test_rename_filter_renames_assembled_images_and_every_stos_reference(base: str) -> None:
    # RC1_Crush 2026-11-13: after Raw8 -> Quantized the assembled images kept the Raw8 name, and .stos files in
    # Grid8/SliceToVolume1 named levels that were never assembled (no image node to fix them from).
    volume_dir, volume = _make_volume(base, {1: 'Raw8', 2: 'Raw8', 3: 'Raw8'})
    images = {s: _add_assembled_image(volume, s, 16) for s in (1, 2)}
    VolumeManager.Save(volume)
    group = cast(StosGroupNode, volume.find('Block/StosGroup'))
    level8 = {s: os.path.join(os.path.dirname(os.path.dirname(p)), '008', os.path.basename(p))
              for s, p in images.items()}
    for folder, paths in (('', images), ('Manual', images), ('Automatic', level8)):
        _write_stos(os.path.join(group.FullPath, folder, 'extra_2-1.stos'), paths[1], paths[2])
    transform_line = STOS_TEXT.strip().split('\n')[-1]

    build.Execute([volume_dir, '-debug', 'RenameFilter', '-InputFilter', 'Raw8', '-OutputFilter', 'Quantized'])

    volume = VolumeManager.Load(volume_dir)
    for section in (1, 2):
        image_filter = volume.find(f"Block/Section[@Number='{section}']/Channel/Filter")
        image = image_filter.Imageset.GetImage(16)
        assert image.Path == f'{section:04d}_TEM_Quantized.png'
        with open(image.FullPath) as f:
            assert f.read() == f'image {section}'
    for folder in ('', 'Manual', 'Automatic'):
        path = os.path.join(group.FullPath, folder, 'extra_2-1.stos')
        with open(path) as f:
            text = f.read()
        assert 'Raw8' not in text, (path, text)
        assert transform_line in text
        stos = stosfile.StosFile.Load(path)
        level = '008' if folder == 'Automatic' else '016'
        assert os.path.normpath(stos.ControlImageFullPath).endswith(
            os.path.join('Quantized', 'Images', level, '0001_TEM_Quantized.png'))
        assert os.path.normpath(stos.ControlMaskFullPath).endswith(os.path.join('Mask', 'Images', '016', 'mask.png'))


def test_stos_line_of_a_section_that_kept_the_old_filter_is_unchanged(base: str) -> None:
    channel_dir = os.path.join(base, 'TEM', '0001', 'TEM')
    os.makedirs(os.path.join(channel_dir, 'Raw8'))
    line = os.path.join(channel_dir, 'Raw8', 'Images', '016', '0001_TEM_Raw8.png')
    assert block_ops._renamed_filter_image_path(line, 'Raw8', 'Quantized') is None
    other = os.path.join(base, 'TEM', '0002', 'TEM', 'Raw8', 'Images', '016', '0002_TEM_Raw8.png')
    assert block_ops._renamed_filter_image_path(other, 'Raw8', 'Quantized') == os.path.join(
        base, 'TEM', '0002', 'TEM', 'Quantized', 'Images', '016', '0002_TEM_Quantized.png')
    mask = os.path.join(base, 'TEM', '0002', 'TEM', 'Mask', 'Images', '016', '0002_TEM_Mask.png')
    assert block_ops._renamed_filter_image_path(mask, 'Raw8', 'Quantized') is None


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
