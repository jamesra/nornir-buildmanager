"""Tests for CreateBlobFilter generate-vs-downsample decisions."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import nornir_imageregistration
import nornir_shared.checksum
from nornir_buildmanager.operations.channel import CreateBlobFilter
from nornir_buildmanager.volumemanager import (
    ChannelNode,
    FilterNode,
    ImageNode,
    ImageSetNode,
    SectionNode,
)

_IMAGE_NAME = 'section.png'
_LOGGER = logging.getLogger('test_create_blob_filter')


def _gray(height: int, width: int, value: int) -> np.ndarray:
    return np.full((height, width), value, dtype=np.uint8)


def _noise(height: int, width: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(height, width), dtype=np.uint8)


def _set_filesize_checksum(image: ImageNode) -> str:
    checksum = nornir_shared.checksum.FilesizeChecksum(image.FullPath)
    assert checksum is not None
    image.attrib['Checksum'] = checksum
    return checksum


def _add_level(imageset: ImageSetNode, downsample: float, array: np.ndarray) -> ImageNode:
    _added, level = imageset.GetOrCreateLevel(downsample, GenerateData=False)
    assert level is not None
    os.makedirs(level.FullPath, exist_ok=True)
    image = ImageNode.Create(_IMAGE_NAME)
    _added_image, added_node = level.UpdateOrAddChild(image)
    image_node = added_node if isinstance(added_node, ImageNode) else image
    nornir_imageregistration.SaveImage(image_node.FullPath, array)
    return image_node


def _make_leveled_channel(tmp_path: Path) -> tuple[ChannelNode, FilterNode, ImageNode]:
    """Section/TEM/Leveled with a 16x downsample input image."""
    section = SectionNode.Create(Number=1, Path=str(tmp_path / '0001'))
    os.makedirs(section.FullPath, exist_ok=True)
    channel = ChannelNode.Create('TEM')
    section.append(channel)
    leveled = FilterNode.Create('Leveled')
    channel.append(leveled)
    leveled_16 = _add_level(leveled.Imageset, 16, _gray(32, 32, 80))
    _set_filesize_checksum(leveled_16)
    return channel, leveled, leveled_16


def _write_blob_sentinel(source_path: str, dest_path: str, **_kwargs) -> SimpleNamespace:
    nornir_imageregistration.SaveImage(dest_path, _gray(32, 32, 200))
    return SimpleNamespace(backend='test', used_numpy_fallback=False)


def test_current_finer_blob_skips_generate_and_downsamples(tmp_path: Path) -> None:
    """A current Blob/8 matching assembled Leveled/8 is the source for 16/32/64."""
    channel, leveled, leveled_16 = _make_leveled_channel(tmp_path)
    leveled_8 = _add_level(leveled.Imageset, 8, _gray(64, 64, 40))
    _set_filesize_checksum(leveled_8)
    blob = FilterNode.Create('Blob')
    channel.append(blob)
    blob_8 = _add_level(blob.Imageset, 8, _noise(64, 64, seed=5))
    checksum_8 = _set_filesize_checksum(blob_8)

    with patch('nornir_buildmanager.operations.channel._run_python_blob',
               side_effect=AssertionError('blob generate should be skipped')):
        CreateBlobFilter(
            {'r': 3, 'median': 3, 'max': 3},
            _LOGGER,
            leveled,
            'Blob',
            Levels='16,32,64',
            Interlace=False)

    blob_16 = blob.Imageset.GetImage(16)
    assert blob_16 is not None
    assert os.path.exists(blob_16.FullPath)
    assert blob_16.attrib['InputImageChecksum'] == checksum_8
    assert blob_16.attrib['InputImageChecksum'] != leveled_16.attrib['Checksum']
    assert blob.Imageset.GetImage(32) is not None
    assert blob.Imageset.GetImage(64) is not None


def test_mismatched_finer_blob_generates_from_leveled(tmp_path: Path) -> None:
    """Leftover Blob/8 that does not match assembled Leveled/8 is not used."""
    _channel, leveled, leveled_16 = _make_leveled_channel(tmp_path)
    leveled_8 = _add_level(leveled.Imageset, 8, _gray(64, 64, 40))
    _set_filesize_checksum(leveled_8)
    blob = FilterNode.Create('Blob')
    leveled.Parent.append(blob)
    leftover_8 = _add_level(blob.Imageset, 8, _noise(80, 80, seed=9))
    _set_filesize_checksum(leftover_8)
    leveled_checksum = leveled_16.attrib['Checksum']

    with patch('nornir_buildmanager.operations.channel._run_python_blob',
               side_effect=_write_blob_sentinel) as blob_run:
        CreateBlobFilter(
            {'r': 3, 'median': 3, 'max': 3},
            _LOGGER,
            leveled,
            'Blob',
            Levels='16,32,64',
            Interlace=False)

    assert blob_run.call_count == 1
    blob_16 = blob.Imageset.GetImage(16)
    assert blob_16 is not None
    assert os.path.exists(blob_16.FullPath)
    assert blob_16.attrib.get('InputImageChecksum') == leveled_checksum
    loaded = nornir_imageregistration.LoadImage(blob_16.FullPath)
    assert int(np.max(loaded)) == 200
    assert tuple(nornir_imageregistration.GetImageSize(blob_16.FullPath)) == (32, 32)


def test_unverified_finer_blob_generates_from_leveled(tmp_path: Path) -> None:
    """Leftover Blob/8 is ignored when assembled Leveled has no image at 8."""
    _channel, leveled, leveled_16 = _make_leveled_channel(tmp_path)
    blob = FilterNode.Create('Blob')
    leveled.Parent.append(blob)
    leftover_8 = _add_level(blob.Imageset, 8, _noise(64, 64, seed=7))
    _set_filesize_checksum(leftover_8)
    leveled_checksum = leveled_16.attrib['Checksum']

    with patch('nornir_buildmanager.operations.channel._run_python_blob',
               side_effect=_write_blob_sentinel) as blob_run:
        CreateBlobFilter(
            {'r': 3, 'median': 3, 'max': 3},
            _LOGGER,
            leveled,
            'Blob',
            Levels='16,32,64',
            Interlace=False)

    assert blob_run.call_count == 1
    blob_16 = blob.Imageset.GetImage(16)
    assert blob_16 is not None
    assert blob_16.attrib.get('InputImageChecksum') == leveled_checksum


def test_blob_pyramid_aligns_to_assembled_shapes(tmp_path: Path) -> None:
    """Shrunk Blob 32/64 match assembled Leveled/Mask when Shrink rounding differs."""
    channel, leveled, _leveled_16 = _make_leveled_channel(tmp_path)
    leveled_32 = _add_level(leveled.Imageset, 32, _gray(17, 17, 90))
    _set_filesize_checksum(leveled_32)
    mask = FilterNode.Create('Mask')
    channel.append(mask)
    leveled.MaskName = 'Mask'
    mask_64 = _add_level(mask.Imageset, 64, _gray(9, 9, 255))
    _set_filesize_checksum(mask_64)

    with patch('nornir_buildmanager.operations.channel._run_python_blob',
               side_effect=_write_blob_sentinel):
        CreateBlobFilter(
            {'r': 3, 'median': 3, 'max': 3},
            _LOGGER,
            leveled,
            'Blob',
            Levels='16,32,64',
            Interlace=False)

    blob = channel.GetFilter('Blob')
    assert blob is not None
    blob_32 = blob.Imageset.GetImage(32)
    blob_64 = blob.Imageset.GetImage(64)
    assert blob_32 is not None
    assert blob_64 is not None
    assert tuple(nornir_imageregistration.GetImageSize(blob_32.FullPath)) == (17, 17)
    assert tuple(nornir_imageregistration.GetImageSize(blob_64.FullPath)) == (9, 9)


def test_missing_finer_blob_generates_from_leveled(tmp_path: Path) -> None:
    """Without a current finer Blob level, 16 is generated from Leveled then pyramided."""
    _channel, leveled, leveled_16 = _make_leveled_channel(tmp_path)
    leveled_checksum = leveled_16.attrib['Checksum']

    with patch('nornir_buildmanager.operations.channel._run_python_blob',
               side_effect=_write_blob_sentinel) as blob_run:
        CreateBlobFilter(
            {'r': 3, 'median': 3, 'max': 3},
            _LOGGER,
            leveled,
            'Blob',
            Levels='16,32,64',
            Interlace=False)

    assert blob_run.call_count == 1
    blob = leveled.Parent.GetChildByAttrib('Filter', 'Name', 'Blob')
    assert blob is not None
    blob_16 = blob.Imageset.GetImage(16)
    assert blob_16 is not None
    assert os.path.exists(blob_16.FullPath)
    assert blob_16.attrib.get('InputImageChecksum') == leveled_checksum
    loaded = nornir_imageregistration.LoadImage(blob_16.FullPath)
    assert int(np.max(loaded)) == 200
    blob_32 = blob.Imageset.GetImage(32)
    assert blob_32 is not None
    assert blob_32.attrib.get('InputImageChecksum') == nornir_shared.checksum.FilesizeChecksum(
        blob_16.FullPath)
    assert blob.Imageset.GetImage(64) is not None
