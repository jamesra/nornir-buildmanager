"""Tests for ImageSet pyramid rebuild rules in BuildImagePyramid."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

import nornir_imageregistration
import nornir_shared.checksum
from nornir_buildmanager.operations.tile import BuildImagePyramid
from nornir_buildmanager.volumemanager import ImageNode, ImageSetNode

_IMAGE_NAME = 'mosaic.png'


def _gray(height: int, width: int, value: int) -> np.ndarray:
    return np.full((height, width), value, dtype=np.uint8)


def _noise(height: int, width: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(height, width), dtype=np.uint8)


def _make_imageset(tmp_path: Path) -> ImageSetNode:
    imageset = ImageSetNode.Create()
    imageset.Path = str(tmp_path / 'Images')
    return imageset


def _add_level(imageset: ImageSetNode, downsample: float, array: np.ndarray) -> ImageNode:
    _added, level = imageset.GetOrCreateLevel(downsample, GenerateData=False)
    assert level is not None
    os.makedirs(level.FullPath, exist_ok=True)
    image = ImageNode.Create(_IMAGE_NAME)
    _added_image, added_node = level.UpdateOrAddChild(image)
    image_node = added_node if isinstance(added_node, ImageNode) else image
    nornir_imageregistration.SaveImage(image_node.FullPath, array)
    return image_node


def _set_filesize_checksum(image: ImageNode) -> str:
    checksum = nornir_shared.checksum.FilesizeChecksum(image.FullPath)
    assert checksum is not None
    image.attrib['Checksum'] = checksum
    return checksum


def _read_bytes(image: ImageNode) -> bytes:
    with open(image.FullPath, 'rb') as handle:
        return handle.read()


def test_reassemble_parent_rebuilds_stale_child(tmp_path: Path) -> None:
    """Rewriting 8 with a new filesize rebuilds 16 and updates derivation checksums."""
    imageset = _make_imageset(tmp_path)
    image_8 = _add_level(imageset, 8, _gray(64, 64, 40))
    image_16 = _add_level(imageset, 16, _gray(32, 32, 80))
    parent_checksum = _set_filesize_checksum(image_8)
    _set_filesize_checksum(image_16)
    image_16.attrib['InputImageChecksum'] = parent_checksum
    stale_16 = _read_bytes(image_16)

    nornir_imageregistration.SaveImage(image_8.FullPath, _noise(64, 64, seed=11))
    new_parent_checksum = nornir_shared.checksum.FilesizeChecksum(image_8.FullPath)
    assert new_parent_checksum != parent_checksum

    result = BuildImagePyramid(imageset, Levels=[8], Interlace=False)
    assert result is imageset
    assert _read_bytes(image_16) != stale_16
    assert image_16.attrib['InputImageChecksum'] == new_parent_checksum
    assert image_16.attrib['Checksum'] == nornir_shared.checksum.FilesizeChecksum(image_16.FullPath)
    assert image_8.attrib['Checksum'] == new_parent_checksum


def test_adding_finer_level_does_not_replace_assembled_neighbors(tmp_path: Path) -> None:
    """Assemble of 4 must not rewrite existing 8/16/32 PNGs."""
    imageset = _make_imageset(tmp_path)
    image_8 = _add_level(imageset, 8, _gray(64, 64, 40))
    image_16 = _add_level(imageset, 16, _gray(32, 32, 80))
    image_32 = _add_level(imageset, 32, _gray(16, 16, 120))
    checksum_8 = _set_filesize_checksum(image_8)
    checksum_16 = _set_filesize_checksum(image_16)
    checksum_32 = _set_filesize_checksum(image_32)
    image_16.attrib['InputImageChecksum'] = checksum_8
    image_32.attrib['InputImageChecksum'] = checksum_16
    bytes_8 = _read_bytes(image_8)
    bytes_16 = _read_bytes(image_16)
    bytes_32 = _read_bytes(image_32)

    image_4 = _add_level(imageset, 4, _gray(128, 128, 20))
    assert 'InputImageChecksum' not in image_8.attrib

    BuildImagePyramid(imageset, Levels=[4], Interlace=False)

    assert _read_bytes(image_8) == bytes_8
    assert _read_bytes(image_16) == bytes_16
    assert _read_bytes(image_32) == bytes_32
    assert image_8.attrib.get('Checksum') == checksum_8
    assert image_16.attrib.get('Checksum') == checksum_16
    assert image_32.attrib.get('Checksum') == checksum_32
    assert 'InputImageChecksum' not in image_8.attrib
    assert os.path.exists(image_4.FullPath)


def test_matching_input_image_checksum_skips_shrink(tmp_path: Path) -> None:
    """Skip Shrink when 16 already records the live filesize of 8."""
    imageset = _make_imageset(tmp_path)
    image_8 = _add_level(imageset, 8, _gray(64, 64, 40))
    image_16 = _add_level(imageset, 16, _gray(32, 32, 80))
    parent_checksum = _set_filesize_checksum(image_8)
    _set_filesize_checksum(image_16)
    image_16.attrib['InputImageChecksum'] = parent_checksum
    original_16 = _read_bytes(image_16)

    result = BuildImagePyramid(imageset, Levels=[8, 16], Interlace=False)

    assert result is None
    assert _read_bytes(image_16) == original_16
    assert image_16.attrib['InputImageChecksum'] == parent_checksum


def test_missing_child_is_created_from_parent(tmp_path: Path) -> None:
    """A missing 16 is created by shrinking 8."""
    imageset = _make_imageset(tmp_path)
    image_8 = _add_level(imageset, 8, _noise(64, 64, seed=3))
    parent_checksum = _set_filesize_checksum(image_8)

    result = BuildImagePyramid(imageset, Levels=[8, 16], Interlace=False)
    assert result is imageset

    image_16 = imageset.GetImage(16)
    assert image_16 is not None
    assert os.path.exists(image_16.FullPath)
    assert image_16.attrib['InputImageChecksum'] == parent_checksum
    assert image_16.attrib['Checksum'] == nornir_shared.checksum.FilesizeChecksum(image_16.FullPath)


def test_legacy_child_rebuilds_when_parent_filesize_checksum_stale(tmp_path: Path) -> None:
    """16 without InputImageChecksum rebuilds when 8's recorded filesize no longer matches."""
    imageset = _make_imageset(tmp_path)
    image_8 = _add_level(imageset, 8, _gray(64, 64, 40))
    image_16 = _add_level(imageset, 16, _gray(32, 32, 80))
    old_parent_checksum = _set_filesize_checksum(image_8)
    _set_filesize_checksum(image_16)
    assert 'InputImageChecksum' not in image_16.attrib
    stale_16 = _read_bytes(image_16)

    nornir_imageregistration.SaveImage(image_8.FullPath, _noise(64, 64, seed=21))
    new_parent_checksum = nornir_shared.checksum.FilesizeChecksum(image_8.FullPath)
    assert new_parent_checksum != old_parent_checksum

    result = BuildImagePyramid(imageset, Levels=[8], Interlace=False)
    assert result is imageset
    assert _read_bytes(image_16) != stale_16
    assert image_16.attrib['InputImageChecksum'] == new_parent_checksum
    assert image_16.attrib['Checksum'] == nornir_shared.checksum.FilesizeChecksum(image_16.FullPath)
