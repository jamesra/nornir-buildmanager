"""The post-import TileGains step only touches the sections the import read (RC1_Crush, 2026-11-10).

ImportMRC takes no -Sections and its pipeline walks every section of the volume. A batch of two MRCs restamped the
gains of sections 1-12 and more, already registered on ``Translated_Stage``, at about 80 s each.
"""

from __future__ import annotations

import os
import tempfile
import types
import unittest
from unittest import mock

from nornir_buildmanager.operations import tilegains
from nornir_imageregistration import noise_crush

PARAMS = noise_crush.CrushParams(step=40, offset=0, bits=8, sigma=16.0, k=2.5, source_dtype="uint16")


class _Reached(Exception):
    """Raised by the patched mosaic load: the step went past its early returns."""


def _filter(section_number: int, **attrib: str) -> types.SimpleNamespace:
    """A crushed filter node inside the given section (filter -> channel -> section)."""
    section = types.SimpleNamespace(Number=section_number)
    channel = types.SimpleNamespace(Parent=section)
    return types.SimpleNamespace(attrib={**PARAMS.to_attributes(), **attrib}, FullPath=f'/vol/{section_number}/Raw8',
                                 Parent=channel, HasTilePyramid=True,
                                 TilePyramid=types.SimpleNamespace(FullPath='/x', GetLevel=lambda d: types.SimpleNamespace(FullPath='/x')))


class TestImportScope(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for number in (10, 66):
            folder = os.path.join(self._tmp.name, f'{number:04d}')
            os.makedirs(folder)
            open(os.path.join(folder, f'{number:04d}.mrc'), 'wb').close()
        self.transform = types.SimpleNamespace(Name='Stage', attrib={'Checksum': 'abc'}, FullPath='/vol/Stage.mosaic')

    def _run(self, filter_node: types.SimpleNamespace, **kwargs):
        with mock.patch.object(tilegains.nornir_imageregistration.Mosaic, 'LoadFromMosaicFile',
                               side_effect=_Reached) as load:
            try:
                result = tilegains.TileGains(self.transform, filter_node, Downsample=1, CrushedOnly=True,
                                             SkipIfCurrent=True, **kwargs)
            except _Reached:
                return 'reached', load
        return result, load

    def test_a_section_outside_the_import_is_left_alone(self) -> None:
        (result, load) = self._run(_filter(12), ImportDir=self._tmp.name, ImportExtension='mrc')
        self.assertIsNone(result)
        load.assert_not_called()

    def test_a_section_read_by_the_import_is_processed(self) -> None:
        for number in (10, 66):
            (result, _) = self._run(_filter(number), ImportDir=self._tmp.name, ImportExtension='mrc')
            self.assertEqual('reached', result)

    def test_without_an_import_folder_every_section_is_processed(self) -> None:
        (result, _) = self._run(_filter(12))
        self.assertEqual('reached', result)

    def test_gains_stamped_from_another_transform_are_kept(self) -> None:
        stamped = _filter(10, TileGainsSource='Translated_Stage:768df02e', TileGainsChecksum='6f9564d7')
        (result, load) = self._run(stamped, ImportDir=self._tmp.name, ImportExtension='mrc', KeepOtherSource=True)
        self.assertIsNone(result)
        load.assert_not_called()

    def test_gains_stamped_from_stage_are_still_refreshed_by_an_import(self) -> None:
        stamped = _filter(10, TileGainsSource='Stage:old', TileGainsChecksum='6f9564d7')
        (result, _) = self._run(stamped, ImportDir=self._tmp.name, ImportExtension='mrc', KeepOtherSource=True)
        self.assertEqual('reached', result)


if __name__ == '__main__':
    unittest.main()
