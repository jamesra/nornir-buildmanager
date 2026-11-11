"""AdjustAssembledContrast: Leveled images from a crushed filter's assembled images.

James, 2026-11-10: crushed sections build their mosaic on Quantized and assemble it; Leveled then holds only
assembled 8-bit images mapped from the import's display range (CrushDisplayMin/Max), so TEMAlign reads crushed and
legacy sections alike. The tests run the AdjustAssembledContrast pipeline, which selects the levels.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from typing import cast

import numpy as np
import pytest
from nornir_buildmanager import build
from nornir_buildmanager.volumemanager import (
    BlockNode,
    ChannelNode,
    FilterNode,
    ImageNode,
    SectionNode,
    VolumeManager,
    XContainerElementWrapper,
)
from nornir_imageregistration import noise_crush
from nornir_imageregistration.noise_crush import CrushParams
from PIL import Image

PARAMS = CrushParams(k=2.5, step=100, sigma=40.0, offset=0, bits=6, source_dtype='uint16')


def _add_assembled(quantized: FilterNode, downsample: int, codes: np.ndarray) -> ImageNode:
    imageset = quantized.Imageset
    image = imageset.GetOrCreateImage(downsample, quantized.DefaultImageName('.png'), GenerateData=False)
    os.makedirs(os.path.dirname(image.FullPath), exist_ok=True)
    noise_crush.save_crushed_png(image.FullPath, codes, PARAMS)
    return image


@pytest.fixture
def volume_dir() -> Iterator[str]:
    base = tempfile.mkdtemp(prefix='nornir_autolevel_assembled_')
    path = os.path.join(base, 'vol')
    volume = cast(XContainerElementWrapper, VolumeManager.Load(path, Create=True))
    _, block = volume.UpdateOrAddChildByAttrib(BlockNode.Create('TEM'), 'Name')
    _, section = block.UpdateOrAddChildByAttrib(SectionNode.Create(1), 'Number')
    _, chan = section.UpdateOrAddChildByAttrib(ChannelNode.Create('TEM'), 'Name')
    _, quantized = chan.UpdateOrAddChildByAttrib(FilterNode.Create('Quantized'), 'Name')
    for name, value in PARAMS.to_attributes().items():
        quantized.attrib[name] = value
    quantized.BitsPerPixel = PARAMS.bits
    quantized.CrushDisplayRange = (10.0, 50.0)
    quantized.MaskName = 'Mask'
    quantized.Imageset.attrib.update({'InputTransform': 'Grid', 'InputTransformChecksum': 'abc',
                                      'InputTransformType': 'Grid'})
    codes = np.tile(np.arange(64, dtype=np.uint8), (8, 1))
    _add_assembled(quantized, 8, codes)
    _add_assembled(quantized, 16, codes[::2, ::2])
    VolumeManager.Save(volume)
    yield path
    shutil.rmtree(base, ignore_errors=True)


def _run(volume_dir: str, levels: str | None = None) -> None:
    args = [volume_dir, '-debug', 'AdjustAssembledContrast', '-InputFilter', 'Quantized', '-OutputFilter', 'Leveled']
    if levels is not None:
        args += ['-Downsample', levels]
    build.Execute(args)


def _channel(volume_dir: str) -> ChannelNode:
    return VolumeManager.Load(volume_dir).find('Block/Section/Channel')


def _leveled(volume_dir: str, downsample: int) -> np.ndarray:
    image = _channel(volume_dir).GetFilter('Leveled').GetImage(downsample)
    assert image is not None
    with Image.open(image.FullPath) as im:
        assert im.mode == 'L'
        return np.asarray(im)


def _set_display_range(volume_dir: str, display_range: tuple[float, float]) -> None:
    volume = VolumeManager.Load(volume_dir)
    quantized = volume.find('Block/Section/Channel').GetFilter('Quantized')
    quantized.CrushDisplayRange = display_range
    VolumeManager.Save(volume)


def test_display_range_maps_linearly_to_8_bits(volume_dir: str) -> None:
    _run(volume_dir)
    row = _leveled(volume_dir, 8)[0]
    assert row[10] == 0 and row[50] == 255
    assert row[:10].max() == 0 and row[50:].min() == 255
    assert row[30] == round(20 * 255 / 40)
    assert np.all(np.diff(row.astype(int)) >= 0)
    assert _leveled(volume_dir, 16).shape == (4, 32)


def test_leveled_has_no_tiles_and_follows_the_assembled_set(volume_dir: str) -> None:
    _run(volume_dir)
    leveled = _channel(volume_dir).GetFilter('Leveled')
    assert not leveled.HasTilePyramid
    assert leveled.BitsPerPixel == 8
    assert leveled.MaskName == 'Mask'
    assert leveled.Imageset.attrib['InputTransform'] == 'Grid'
    assert leveled.Imageset.attrib['InputTransformChecksum'] == 'abc'
    assert (leveled.MinIntensityCutoff, leveled.MaxIntensityCutoff) == (10.0, 50.0)


def test_current_images_are_not_rewritten(volume_dir: str) -> None:
    _run(volume_dir)
    path = _channel(volume_dir).GetFilter('Leveled').GetImage(8).FullPath
    mtime = os.path.getmtime(path)
    os.utime(path, (mtime - 100, mtime - 100))
    _run(volume_dir)
    assert os.path.getmtime(path) == mtime - 100


def test_display_range_change_rebuilds(volume_dir: str) -> None:
    _run(volume_dir)
    _set_display_range(volume_dir, (0.0, 63.0))
    _run(volume_dir)
    row = _leveled(volume_dir, 8)[0]
    assert row[0] == 0 and row[63] == 255
    assert _channel(volume_dir).GetFilter('Leveled').MinIntensityCutoff == 0.0


def test_only_requested_levels_are_leveled(volume_dir: str) -> None:
    _run(volume_dir, '16,32')
    leveled = _channel(volume_dir).GetFilter('Leveled')
    assert leveled.GetImage(16) is not None
    assert leveled.GetImage(8) is None
    assert leveled.GetImage(32) is None  # requested but never assembled


def test_uncrushed_filter_is_skipped(volume_dir: str) -> None:
    volume = VolumeManager.Load(volume_dir)
    quantized = volume.find('Block/Section/Channel').GetFilter('Quantized')
    for name in PARAMS.to_attributes():
        del quantized.attrib[name]
    quantized.AttributesChanged = True
    VolumeManager.Save(volume)
    assert _channel(volume_dir).GetFilter('Quantized').attrib.get(next(iter(PARAMS.to_attributes()))) is None
    _run(volume_dir)
    assert _channel(volume_dir).GetFilter('Leveled') is None
