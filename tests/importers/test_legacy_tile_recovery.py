"""Rebuilding damaged raw tiles from a legacy 8-bit import (RC3's 0-byte TIFs, 2026-11-16).

The legacy tiles are made here the way a legacy import made them: a section-wide contrast stretch with
gamma, clipped to 8 bits, written flipped. The recovery must find the flip, invert the stretch to within
one legacy level, refuse a section whose legacy numbering does not match, and never touch a readable tile.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image
from scipy import ndimage

from nornir_buildmanager.importers import legacy_tile_recovery as recovery

_TILES = 6
_SIZE = 64
_LOW, _HIGH, _GAMMA = 3000.0, 9000.0, 1.6


def _raw_tile(rng: np.random.Generator) -> np.ndarray:
    structure = ndimage.gaussian_filter(rng.normal(0, 1, (_SIZE, _SIZE)), 3)
    structure = (structure - structure.min()) / np.ptp(structure)
    counts = 2500 + 7000 * structure + rng.normal(0, 30, (_SIZE, _SIZE))
    return np.clip(counts, 0, 65535).astype(np.uint16)


def _legacy_tile(raw: np.ndarray) -> np.ndarray:
    unit = np.clip((raw.astype(np.float64) - _LOW) / (_HIGH - _LOW), 0, 1) ** (1 / _GAMMA)
    return np.rint(unit * 255).astype(np.uint8)[::-1, :]  # the legacy import wrote it flipped


class _Section(unittest.TestCase):

    def setUp(self) -> None:
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.raw_dir = os.path.join(self.directory, 'raw')
        self.legacy_dir = os.path.join(self.directory, 'Raw8', 'TilePyramid', '001')
        os.makedirs(self.raw_dir)
        os.makedirs(self.legacy_dir)
        rng = np.random.default_rng(7)
        self.originals = [_raw_tile(rng) for _ in range(_TILES)]
        lines = [f'ImageSize = {_SIZE} {_SIZE}', 'DataMode = 6', 'PixelSpacing = 2.18', '']
        for i, raw in enumerate(self.originals):
            lines += [f'[Image = {i:03d}.tif]', f'PieceCoordinates = {i * 60} 0 0', '']
            Image.fromarray(raw).save(self.raw_path(i))
            Image.fromarray(_legacy_tile(raw)).save(os.path.join(self.legacy_dir, f'{i:03d}.png'))
        self.idoc = os.path.join(self.raw_dir, '1.idoc')
        with open(self.idoc, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

    def raw_path(self, i: int) -> str:
        return os.path.join(self.raw_dir, f'{i:03d}.tif')

    def raw_bytes(self, i: int) -> bytes:
        with open(self.raw_path(i), 'rb') as f:
            return f.read()

    def truncate(self, i: int) -> None:
        open(self.raw_path(i), 'wb').close()


class TestAZeroByteTileIsRebuilt(_Section):

    def test_rebuilt_counts_match_the_lost_tile_within_one_legacy_level(self) -> None:
        self.truncate(3)
        written = recovery.recover_section(self.idoc, self.legacy_dir, apply=True)
        self.assertEqual(written, [self.raw_path(3)])

        with Image.open(self.raw_path(3)) as im:
            rebuilt = np.asarray(im)
        original = self.originals[3]
        self.assertEqual((rebuilt.dtype, rebuilt.shape), (original.dtype, original.shape))
        inside = (original > _LOW + 200) & (original < _HIGH - 200)  # the 2022 stretch clipped the rest
        level_width = (_HIGH - _LOW) / 255 * _GAMMA  # widest step of the gamma curve, in counts
        error = np.abs(rebuilt.astype(np.float64) - original)[inside]
        self.assertLess(float(np.percentile(error, 99)), level_width)

    def test_provenance_is_recorded_beside_the_tile(self) -> None:
        self.truncate(3)
        recovery.recover_section(self.idoc, self.legacy_dir, apply=True)
        with open(self.raw_path(3) + '.recovered.json', encoding='utf-8') as f:
            provenance = json.load(f)
        self.assertEqual((provenance['idoc_index'], provenance['orientation']), (3, 'flipud'))
        self.assertTrue(provenance['recovered_from'].endswith('003.png'))
        self.assertIn(2, provenance['training_indices'])
        self.assertIn(4, provenance['training_indices'])

    def test_readable_tiles_are_not_touched(self) -> None:
        self.truncate(3)
        before = {i: self.raw_bytes(i) for i in range(_TILES) if i != 3}
        recovery.recover_section(self.idoc, self.legacy_dir, apply=True)
        self.assertEqual({i: self.raw_bytes(i) for i in before}, before)
        self.assertFalse(os.path.exists(self.raw_path(2) + '.recovered.json'))

    def test_dry_run_writes_nothing(self) -> None:
        self.truncate(3)
        self.assertEqual(recovery.recover_section(self.idoc, self.legacy_dir, apply=False), [self.raw_path(3)])
        self.assertEqual(os.path.getsize(self.raw_path(3)), 0)
        self.assertFalse(os.path.exists(self.raw_path(3) + '.recovered.json'))

    def test_a_healthy_section_writes_nothing(self) -> None:
        self.assertEqual(recovery.recover_section(self.idoc, self.legacy_dir, apply=True), [])

    def test_a_damaged_tile_without_a_legacy_tile_is_left_out(self) -> None:
        self.truncate(3)
        os.remove(os.path.join(self.legacy_dir, '003.png'))
        self.assertEqual(recovery.recover_section(self.idoc, self.legacy_dir, apply=True), [])
        self.assertEqual(os.path.getsize(self.raw_path(3)), 0)


class TestAMismatchedLegacyNumberingIsRefused(_Section):

    def test_shifted_legacy_names_are_refused(self) -> None:
        """A 2022 import that numbered survivors from zero (#153) names a different tile at each index."""
        self.truncate(3)
        names = [os.path.join(self.legacy_dir, f'{i:03d}.png') for i in range(_TILES)]
        rotated = names[1:] + names[:1]
        temporary = [n + '.tmp' for n in names]
        for src, tmp in zip(names, temporary):
            os.rename(src, tmp)
        for tmp, dst in zip(temporary, rotated):
            os.rename(tmp, dst)
        with self.assertRaises(recovery.RecoveryRefused):
            recovery.recover_section(self.idoc, self.legacy_dir, apply=True)
        self.assertEqual(os.path.getsize(self.raw_path(3)), 0)

    def test_the_command_line_exits_2_when_refused(self) -> None:
        self.truncate(3)
        Image.fromarray(np.zeros((_SIZE, _SIZE), dtype=np.uint8)).save(os.path.join(self.legacy_dir, '002.png'))
        self.assertEqual(recovery.main([self.idoc, self.legacy_dir, '--apply']), 2)
        self.assertEqual(os.path.getsize(self.raw_path(3)), 0)


if __name__ == '__main__':
    unittest.main()
