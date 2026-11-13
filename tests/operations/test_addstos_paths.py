"""AddStos points the imported .stos at the StosGroup's own filter images.

RC1_Crush, 2026-11-12: a .stos made with StosFile.Create(...).Save() in a scratch folder stores image paths relative
to that folder (../../../storage2/RC1_Crush/TEM/0122/...). AddStos copied it into Brute32 unchanged, so from the group
folder those paths named missing files and Pyre could not open the pair.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from typing import cast

import pytest
from nornir_buildmanager import build
from nornir_buildmanager.volumemanager import (
    BlockNode,
    ChannelNode,
    FilterNode,
    SectionNode,
    VolumeManager,
    XContainerElementWrapper,
)
from nornir_imageregistration.files import stosfile
from nornir_imageregistration.transforms import factory

DOWNSAMPLE = 32


@pytest.fixture
def base() -> Iterator[str]:
    path = tempfile.mkdtemp(prefix='nornir_addstos_paths_')
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _make_volume(base: str) -> str:
    path = os.path.join(base, 'vol')
    volume = cast(XContainerElementWrapper, VolumeManager.Load(path, Create=True))
    _, block = volume.UpdateOrAddChildByAttrib(BlockNode.Create('TEM'), 'Name')
    for number in (122, 123):
        _, section = block.UpdateOrAddChildByAttrib(SectionNode.Create(number), 'Number')
        _, channel = section.UpdateOrAddChildByAttrib(ChannelNode.Create('TEM'), 'Name')
        _, image_filter = channel.UpdateOrAddChildByAttrib(FilterNode.Create('Quantized'), 'Name')
        image_filter.Imageset.GetOrCreateImage(DOWNSAMPLE, image_filter.DefaultImageName('.png'), GenerateData=False)
    block.GetOrCreateStosGroup('Brute32', DOWNSAMPLE)
    VolumeManager.Save(volume)
    return path


def _scratch_stos(base: str) -> str:
    """A .stos saved outside the volume, naming files that do not resolve from the group folder."""
    scratch = os.path.join(base, 'scratch', 'reg')
    os.makedirs(scratch)
    transform = factory.CreateRigidTransform((0, 0), 0, (100, 100), (100, 100))
    stos = stosfile.StosFile.Create(os.path.join(base, 'elsewhere', '0122.png'),
                                    os.path.join(base, 'elsewhere', '0123.png'), transform)
    stos.ControlImageDim = stos.MappedImageDim = [1, 1, 100, 100]
    path = os.path.join(scratch, '123-122.stos')
    stos.Save(path)
    return path


def test_addstos_rewrites_image_paths_to_the_group_filters(base: str) -> None:
    volume_dir = _make_volume(base)
    build.Execute([volume_dir, '-debug', 'AddStos', '-File', _scratch_stos(base), '-StosGroup', 'Brute',
                   '-ControlSection', '122', '-ControlChannel', 'TEM', '-ControlFilter', 'Quantized',
                   '-ControlDownsample', str(DOWNSAMPLE), '-MappedSection', '123', '-MappedChannel', 'TEM',
                   '-MappedFilter', 'Quantized', '-MappedDownsample', str(DOWNSAMPLE), '-Type', 'Brute'])

    volume = VolumeManager.Load(volume_dir)
    transform = volume.find('Block/StosGroup/SectionMappings/Transform')
    assert transform is not None
    imported = stosfile.StosFile.Load(transform.FullPath)
    for section, embedded in ((122, imported.ControlImageFullPath), (123, imported.MappedImageFullPath)):
        image_filter = volume.find(f"Block/Section[@Number='{section}']/Channel/Filter")
        expected = image_filter.Imageset.GetOrPredictImageFullPath(DOWNSAMPLE)
        assert stosfile.paths_refer_to_same_file(embedded, expected), (embedded, expected)
    with open(transform.FullPath) as f:
        assert 'elsewhere' not in f.read()
