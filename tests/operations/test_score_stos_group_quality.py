"""Tests for ScoreStosGroupQuality cache + PairZNCC attrib updates."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

import nornir_imageregistration.core as core
from nornir_buildmanager.operations import block
from nornir_imageregistration.files.stosfile import StosFile
from nornir_imageregistration.stos_quality import load_quality_cache
from nornir_imageregistration.transforms import factory


def _identity_transform(shape: tuple[int, int] = (32, 32)):
    dims = np.asarray(shape, dtype=np.float64)
    return factory.CreateRigidTransform((0, 0), 0.0, dims, dims)


def _write_stos(group: Path, name: str = '2-1_ctrl-TEM_map-TEM.stos') -> Path:
    images = group.parent / 'images'
    ctrl = images / 'ctrl.png'
    mapped = images / 'map.png'
    images.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(3)
    image = rng.integers(32, 224, size=(32, 32), dtype=np.uint8)
    core.SaveImage(str(ctrl), image)
    core.SaveImage(str(mapped), image)
    group.mkdir(parents=True, exist_ok=True)
    stos_path = group / name
    stos = StosFile()
    stos.ControlImageFullPath = str(ctrl)
    stos.MappedImageFullPath = str(mapped)
    stos.ControlImageDim = [1.0, 1.0, 32, 32]
    stos.MappedImageDim = [1.0, 1.0, 32, 32]
    stos.Downsample = 1
    stos.Transform = _identity_transform()
    stos.Save(str(stos_path))
    return stos_path


def test_score_stos_group_quality_writes_cache_and_attrib(tmp_path: Path) -> None:
    """Batch scorer writes JSON and sets PairZNCC on the transform node."""
    group_dir = tmp_path / 'Grid16'
    stos_path = _write_stos(group_dir)

    transform_node = MagicMock()
    transform_node.FullPath = str(stos_path)
    transform_node.Path = stos_path.name
    transform_node.PairZNCC = None
    transform_node.FindParent = MagicMock(return_value=None)

    group_node = MagicMock()
    group_node.FullPath = str(group_dir)
    group_node.TransformsForMapping = MagicMock(return_value=[transform_node])
    group_node.PathToManualTransform = MagicMock(return_value=None)
    group_node.FindParent = MagicMock(return_value=None)

    mapping = MagicMock()
    mapping.Mapped = [2]
    mapping.Control = 1
    stos_map = MagicMock()
    stos_map.Mappings = [mapping]

    logger = MagicMock()
    result = block.ScoreStosGroupQuality(
        {},
        stos_map_node=stos_map,
        group_node=group_node,
        Logger=logger,
        MaxSide=64,
    )
    assert result is group_node

    cache = load_quality_cache(str(group_dir))
    assert stos_path.name in cache.entries
    assert cache.entries[stos_path.name]['pair_zncc'] > 0.95
    assert transform_node.PairZNCC == pytest.approx(cache.entries[stos_path.name]['pair_zncc'])


def test_merge_refine_quality_summary(tmp_path: Path) -> None:
    """Refine diagnostics merge into the quality cache without requiring pair ZNCC."""
    group_dir = tmp_path / 'Grid16'
    stos_path = _write_stos(group_dir)
    npz = group_dir / 'refine_pass02_diagnostics.npz'
    zncc = np.asarray([0.4, 0.8], dtype=np.float64)
    locked = np.asarray([True, True], dtype=bool)
    role = np.asarray([2, 2], dtype=np.int64)
    np.savez_compressed(npz, zncc=zncc, locked=locked, role=role)

    block._merge_refine_quality_summary(str(stos_path))
    cache = load_quality_cache(str(group_dir))
    entry = cache.entries[stos_path.name]
    assert entry['refine']['pass'] == 2
    assert entry['refine']['median_lock_zncc'] == pytest.approx(0.6)
