"""One input file per section number (RC1: "0347.mrc" and "0347 2.mrc" are both section 347).

Two files that parse to one section number both wrote into that section, leaving it mixing two
acquisitions. The rule (James, 2026-11-01): the higher version wins; when versions do not decide,
the later creation date does.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from nornir_buildmanager.importers import shared


class TestSelectOneFilePerSection(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def touch(self, name: str) -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, 'wb') as f:
            f.write(b'mrc')
        return path

    def names(self, paths) -> list[str]:
        return [os.path.basename(p) for p in paths]

    def test_distinct_sections_are_all_kept(self):
        paths = [self.touch(n) for n in ('0022_redo.mrc', '0291_0292.mrc', '0292_0291.mrc', '0123_actual.mrc')]
        self.assertEqual(sorted(self.names(paths)), self.names(shared.SelectOneFilePerSection(paths)))

    def test_the_higher_version_wins(self):
        paths = [self.touch('0347 2.mrc'), self.touch('0347.mrc')]
        with mock.patch.object(shared, '_creation_time', side_effect=lambda p: 2.0 if p.endswith('0347.mrc') else 1.0):
            chosen = shared.SelectOneFilePerSection(paths)
        self.assertEqual(['0347 2.mrc'], self.names(chosen), 'version decides before creation date')

    def test_equal_versions_go_to_the_later_creation_date(self):
        paths = [self.touch('0347_first.mrc'), self.touch('0347_second.mrc')]
        created = {'0347_first.mrc': 2.0, '0347_second.mrc': 1.0}
        with mock.patch.object(shared, '_creation_time', side_effect=lambda p: created[os.path.basename(p)]), \
                mock.patch.object(shared.prettyoutput, 'Log') as logged:
            chosen = shared.SelectOneFilePerSection(paths)
        self.assertEqual(['0347_first.mrc'], self.names(chosen))
        self.assertIn('skipping 0347_second.mrc', str(logged.call_args))

    def test_unparsable_names_are_passed_through(self):
        paths = [self.touch('notes.mrc'), self.touch('0001.mrc')]
        self.assertEqual(['0001.mrc', 'notes.mrc'], self.names(shared.SelectOneFilePerSection(paths)))


if __name__ == '__main__':
    unittest.main()
