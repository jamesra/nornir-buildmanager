"""MosaicVolume.Save(skip_unchanged=True) leaves files that already hold the new content alone.

__MoveMosaicsToZeroOrigin recomputes every section's ChannelToVolume.mosaic on each
MosaicToVolume. On srv (RC3, 2026-11-02) it rewrote all 1598 sections, ~105 minutes, when
only section 0001 had changed.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import nornir_imageregistration
from nornir_imageregistration import mosaic
from nornir_imageregistration.grid_subdivision import ITKGridDivision
from nornir_imageregistration.transforms.gridwithrbffallback import GridWithRBFFallback

from nornir_buildmanager.operations.helpers import mosaicvolume

#: A file time no write can produce (1970-01-01T00:00:01; Windows keeps 100 ns units).
_OLD_MTIME: int = 1_000_000_000


def _section(path: Path, offset: tuple[float, float]) -> mosaic.Mosaic:
    grid = GridWithRBFFallback(ITKGridDivision(
        source_shape=(100, 100), cell_size=(32, 32), grid_spacing=(32, 32),
        transform=nornir_imageregistration.transforms.Rigid(target_offset=offset)))
    section = mosaic.Mosaic({'tile.png': grid})
    section.transformNode = mock.Mock(FullPath=str(path))  # type: ignore[attr-defined]
    return section


def _volume(tmp_path: Path, offsets) -> mosaicvolume.MosaicVolume:
    vol = mosaicvolume.MosaicVolume()
    for i, offset in enumerate(offsets):
        vol.AddSection(str(i), _section(tmp_path / f'{i}.mosaic', offset))
    return vol


def test_unchanged_files_are_not_rewritten(tmp_path: Path) -> None:
    _volume(tmp_path, [(0.0, 0.0), (10.0, 20.0)]).Save()
    first = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    for p in tmp_path.iterdir():
        os.utime(p, ns=(_OLD_MTIME, _OLD_MTIME))

    vol = _volume(tmp_path, [(0.0, 0.0), (10.5, 20.0)])
    vol.Save(skip_unchanged=True)

    assert (tmp_path / '0.mosaic').stat().st_mtime_ns == _OLD_MTIME, 'unchanged section was rewritten'
    assert (tmp_path / '1.mosaic').stat().st_mtime_ns != _OLD_MTIME, 'changed section was not written'
    assert (tmp_path / '0.mosaic').read_bytes() == first['0.mosaic']
    for section in vol.SectionToVolumeTransforms.values():
        section.transformNode.ResetChecksum.assert_called_once()


def test_missing_file_is_written(tmp_path: Path) -> None:
    _volume(tmp_path, [(5.0, 5.0)]).Save(skip_unchanged=True)
    assert (tmp_path / '0.mosaic').exists()


def test_default_save_always_writes(tmp_path: Path) -> None:
    _volume(tmp_path, [(5.0, 5.0)]).Save()
    os.utime(tmp_path / '0.mosaic', ns=(_OLD_MTIME, _OLD_MTIME))
    _volume(tmp_path, [(5.0, 5.0)]).Save()
    assert (tmp_path / '0.mosaic').stat().st_mtime_ns != _OLD_MTIME
