"""The DM4 importer without its optional ``dm4`` extra.

``nornir_buildmanager.importers.dm4`` used to ``import dm4`` at module level, so without the
package every test that imported the module failed at collection with
``ModuleNotFoundError: No module named 'dm4'``. The module now imports without the package
(filename parsing needs no DM4 reader) and only the entry points that read DM4 files fail,
naming the extra and how to install it.
"""
from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from nornir_buildmanager.exceptions import NornirMissingDependencyException
from nornir_buildmanager.importers import dm4 as dm4_importer


class TestDm4ExtraMissing(unittest.TestCase):

    def _without_dm4(self) -> mock._patch:
        return mock.patch.object(dm4_importer, '_DM4_IMPORT_ERROR', ImportError("No module named 'dm4'"))

    def test_import_names_the_missing_extra(self) -> None:
        with (self._without_dm4(), tempfile.TemporaryDirectory() as import_dir,
              self.assertRaises(NornirMissingDependencyException) as raised):
            list(dm4_importer.Import(None, import_dir))  # a generator: the check runs on first next()
        self.assertIn('pip install -e dm4', str(raised.exception))
        self.assertIn('nornir_buildmanager[dm4]', str(raised.exception))

    def test_reading_a_file_names_the_missing_extra(self) -> None:
        with self._without_dm4(), self.assertRaises(NornirMissingDependencyException):
            dm4_importer.DM4FileHandler('missing.dm4')

    def test_filename_parsing_needs_no_dm4_reader(self) -> None:
        with self._without_dm4():
            self.assertIsNotNone(
                dm4_importer.DigitalMicrograph4Import.GetMetaFromFilename('Glumi1_3VBSED_stack_00_slice_0476.dm4'))


if __name__ == '__main__':
    unittest.main()
