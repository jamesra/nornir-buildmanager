"""Tests for Manual→Automatic STOS path rebase and FullPath matching."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np

import nornir_imageregistration.core as core
from nornir_buildmanager.operations import block
from nornir_imageregistration.files.stosfile import StosFile, paths_refer_to_same_file

_MIN_TRANSFORM = (
    "FixedCenterOfRotationAffineTransform_double_2_2 vp 8 1 0 0 1 0 0 1 1 fp 2 2 2"
)


def _write_tiny_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    core.SaveImage(str(path), np.zeros((4, 4), dtype=np.uint8))


def test_paths_refer_to_same_file_normalizes() -> None:
    """Absolute and relative forms of the same location match after Load."""
    assert paths_refer_to_same_file(None, None) is True
    assert paths_refer_to_same_file('a', None) is False
    left = os.path.abspath(os.path.join('tmp', 'x.png'))
    right = os.path.normpath(left)
    assert paths_refer_to_same_file(left, right) is True


def test_manual_copy_rebase_rewrites_relative_lines(tmp_path: Path) -> None:
    """After Manual→group-root copy, UpdateStosImagePaths re-Saves relatives vs the destination."""
    group = tmp_path / 'StosGroup'
    manual = group / 'Manual'
    images = tmp_path / 'Images'
    ctrl = images / 'ctrl.png'
    mapped = images / 'map.png'
    _write_tiny_png(ctrl)
    _write_tiny_png(mapped)
    manual.mkdir(parents=True)

    # Write a Manual .stos with paths relative to Manual/ (one level deeper).
    manual_stos = manual / 'pair.stos'
    stos = StosFile()
    stos.ControlImageFullPath = str(ctrl)
    stos.MappedImageFullPath = str(mapped)
    stos.ControlImageDim = [1.0, 1.0, 4, 4]
    stos.MappedImageDim = [1.0, 1.0, 4, 4]
    stos.Transform = _MIN_TRANSFORM
    stos.Save(str(manual_stos), relative_paths=True)

    with open(manual_stos, encoding='utf-8') as handle:
        manual_lines = handle.read().splitlines()
    assert not os.path.isabs(manual_lines[0].replace('/', os.sep))
    assert manual_lines[0].startswith('../')

    # Naive copy leaves Manual-relative lines under the group root (wrong base).
    auto_stos = group / 'pair.stos'
    group.mkdir(parents=True, exist_ok=True)
    shutil.copy(manual_stos, auto_stos)

    # Rebase as buildmanager does after Manual copy (images only, no masks).
    updated = block.UpdateStosImagePaths(str(auto_stos), str(ctrl), str(mapped))
    assert updated is True

    with open(auto_stos, encoding='utf-8') as handle:
        auto_lines = handle.read().splitlines()
    assert not os.path.isabs(auto_lines[0].replace('/', os.sep))
    # From StosGroup/, images are at ../Images/...
    assert 'Images' in auto_lines[0]
    assert auto_lines[0] != manual_lines[0] or auto_lines[0].startswith('../Images')

    loaded = StosFile.Load(str(auto_stos))
    assert paths_refer_to_same_file(loaded.ControlImageFullPath, str(ctrl))
    assert paths_refer_to_same_file(loaded.MappedImageFullPath, str(mapped))
    assert os.path.isfile(loaded.ControlImageFullPath)
    assert os.path.isfile(loaded.MappedImageFullPath)


def test_update_stos_image_paths_no_op_when_fullpaths_match(tmp_path: Path) -> None:
    """Matching FullPaths after Load does not rewrite the file."""
    ctrl = tmp_path / 'c.png'
    mapped = tmp_path / 'm.png'
    _write_tiny_png(ctrl)
    _write_tiny_png(mapped)
    stos_path = tmp_path / 'pair.stos'
    stos = StosFile()
    stos.ControlImageFullPath = str(ctrl)
    stos.MappedImageFullPath = str(mapped)
    stos.ControlImageDim = [1.0, 1.0, 4, 4]
    stos.MappedImageDim = [1.0, 1.0, 4, 4]
    stos.Transform = _MIN_TRANSFORM
    stos.Save(str(stos_path), relative_paths=True)
    mtime_before = os.path.getmtime(stos_path)

    updated = block.UpdateStosImagePaths(str(stos_path), str(ctrl), str(mapped))
    assert updated is False
    assert os.path.getmtime(stos_path) == mtime_before


def test_update_stos_image_paths_rewrites_windows_desktop_lines(tmp_path: Path) -> None:
    """Manual Desktop image lines are replaced with volume-relative paths."""
    volume = tmp_path / 'RC2' / 'TEM'
    ctrl = volume / '1042' / 'TEM' / 'Leveled' / 'Images' / '032' / '1042_TEM_Leveled.png'
    mapped = volume / '1044' / 'TEM' / 'Leveled' / 'Images' / '032' / '1044_TEM_Leveled.png'
    _write_tiny_png(ctrl)
    _write_tiny_png(mapped)
    stos_path = volume / 'Grid32' / 'Manual' / '1044-1042.stos'
    stos_path.parent.mkdir(parents=True)
    stos_path.write_text(
        "C:\\Users\\u0490822\\Desktop\\RC2_LocalEnhanced\\1042_TEM_32_Leveled.png\n"
        "C:\\Users\\u0490822\\Desktop\\RC2_LocalEnhanced\\1044_TEM_32_Leveled.png\n"
        "0\n0\n"
        "1 1 4 4\n1 1 4 4\n"
        f"{_MIN_TRANSFORM}\n",
        encoding='utf-8')

    updated = block.UpdateStosImagePaths(str(stos_path), str(ctrl), str(mapped))
    assert updated is True

    with open(stos_path, encoding='utf-8') as handle:
        lines = handle.read().splitlines()
    assert 'RC2_LocalEnhanced' not in lines[0]
    assert 'C:' not in lines[0]
    assert not os.path.isabs(lines[0].replace('/', os.sep))

    loaded = StosFile.Load(str(stos_path))
    assert paths_refer_to_same_file(loaded.ControlImageFullPath, str(ctrl))
    assert paths_refer_to_same_file(loaded.MappedImageFullPath, str(mapped))
