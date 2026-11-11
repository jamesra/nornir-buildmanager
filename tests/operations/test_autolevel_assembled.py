"""AdjustAssembledContrast: Leveled images from a crushed filter's assembled images.

James, 2026-11-10: crushed sections build their mosaic on Quantized and assemble it; Leveled then holds only
assembled 8-bit images mapped from the import's display range (CrushDisplayMin/Max), so TEMAlign reads crushed and
legacy sections alike.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from typing import cast

import numpy as np
import pytest
from nornir_buildmanager.operations import tile
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
def channel() -> Iterator[ChannelNode]:
    base = tempfile.mkdtemp(prefix='nornir_autolevel_assembled_')
    volume = cast(XContainerElementWrapper, VolumeManager.Load(os.path.join(base, 'vol'), Create=True))
    _, block = volume.UpdateOrAddChildByAttrib(BlockNode.Create('Block'), 'Name')
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
    yield chan
    shutil.rmtree(base, ignore_errors=True)


def _run(chan: ChannelNode, levels: str | None = None) -> None:
    for _ in tile.AutolevelAssembledImages(InputFilter=chan.GetFilter('Quantized'), OutputFilterName='Leveled',
                                           Levels=levels):
        pass


def _leveled(chan: ChannelNode, downsample: int) -> np.ndarray:
    image = chan.GetFilter('Leveled').GetImage(downsample)
    assert image is not None
    with Image.open(image.FullPath) as im:
        assert im.mode == 'L'
        return np.asarray(im)


def test_display_range_maps_linearly_to_8_bits(channel: ChannelNode) -> None:
    _run(channel)
    row = _leveled(channel, 8)[0]
    assert row[10] == 0 and row[50] == 255
    assert row[:10].max() == 0 and row[50:].min() == 255
    assert row[30] == round(20 * 255 / 40)
    assert np.all(np.diff(row.astype(int)) >= 0)
    assert _leveled(channel, 16).shape == (4, 32)


def test_leveled_has_no_tiles_and_follows_the_assembled_set(channel: ChannelNode) -> None:
    _run(channel)
    leveled = channel.GetFilter('Leveled')
    assert not leveled.HasTilePyramid
    assert leveled.BitsPerPixel == 8
    assert leveled.MaskName == 'Mask'
    assert leveled.Imageset.attrib['InputTransform'] == 'Grid'
    assert leveled.Imageset.attrib['InputTransformChecksum'] == 'abc'
    assert (leveled.MinIntensityCutoff, leveled.MaxIntensityCutoff) == (10.0, 50.0)


def test_current_images_are_not_rewritten(channel: ChannelNode) -> None:
    _run(channel)
    path = channel.GetFilter('Leveled').GetImage(8).FullPath
    mtime = os.path.getmtime(path)
    os.utime(path, (mtime - 100, mtime - 100))
    _run(channel)
    assert os.path.getmtime(path) == mtime - 100


def test_display_range_change_rebuilds(channel: ChannelNode) -> None:
    _run(channel)
    channel.GetFilter('Quantized').CrushDisplayRange = (0.0, 63.0)
    _run(channel)
    row = _leveled(channel, 8)[0]
    assert row[0] == 0 and row[63] == 255
    assert channel.GetFilter('Leveled').MinIntensityCutoff == 0.0


def test_only_requested_levels_are_leveled(channel: ChannelNode) -> None:
    _run(channel, '16,32')
    leveled = channel.GetFilter('Leveled')
    assert leveled.GetImage(16) is not None
    assert leveled.GetImage(8) is None
    assert leveled.GetImage(32) is None  # requested but never assembled


def test_uncrushed_filter_is_skipped(channel: ChannelNode) -> None:
    quantized = channel.GetFilter('Quantized')
    for name in PARAMS.to_attributes():
        del quantized.attrib[name]
    _run(channel)
    assert channel.GetFilter('Leveled') is None
