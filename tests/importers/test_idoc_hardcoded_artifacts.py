"""Regression tests for the two hardcoded artifacts removed from the idoc import path (#156).

The review filed these together as debt, with the concern that the first one left
``SectionNumber = 0``. Measurement showed something different: that branch could never run,
because the section number is parsed by ``(?P<Number>\\d+)`` -- digits with no sign -- and a
directory name that does not parse raises instead of returning a sentinel. So the tests here
are in two parts.

The first part pins the premise: no name yields a negative section number, and an unparseable
name raises. Those are the two properties that make the deleted branch dead code, so if either
ever stops holding, the deletion needs revisiting and one of these tests will say so.

The second part covers the ``'RC3' in VolumeObj.FullPath`` mosaic deletion, which was live and
did fire. It was a plain substring test, so it also matched volumes that merely contained those
three characters.
"""

from __future__ import annotations

import datetime
import os
import re
import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.importers import idoc, shared

_IDOC_SOURCE = idoc.__file__


def _idoc_text() -> str:
    with open(_IDOC_SOURCE, 'r', encoding='utf-8') as handle:
        return handle.read()


def _idoc_code() -> str:
    """The importer with comment lines dropped.

    The removals are explained in comments that name what they replaced, so a search of the
    raw text would match the explanation as readily as a reintroduced line.
    """
    return '\n'.join(line for line in _idoc_text().splitlines()
                     if not line.lstrip().startswith('#'))


class TestTheDeadBranchPremise(unittest.TestCase):
    """The section number is never negative, which is why the removed branch was unreachable."""

    def test_a_parseable_name_never_yields_a_negative_number(self):
        for name in ['0', '0000', '1', '0042', '1234_TEM', '0001_Something', '9999_Name_8']:
            info = shared.GetSectionInfo(os.path.join(r'C:\volume', name))
            self.assertIsNotNone(info.number, f'{name!r} parsed to a None section number')
            self.assertGreaterEqual(info.number, 0,
                                    f'{name!r} parsed to a negative section number')

    def test_a_minus_sign_does_not_produce_a_negative_number(self):
        # The obvious way to reach the removed branch. The regex requires a leading digit, so
        # the minus sign is not consumed as a sign and the name simply fails to parse.
        for name in ['-5', '-5_Name', '-0001_Something', '-1']:
            with self.assertRaises(NornirUserException):
                shared.GetSectionInfo(os.path.join(r'C:\volume', name))

    @given(st.text(min_size=0, max_size=40))
    @settings(max_examples=500, deadline=None)
    def test_no_generated_name_yields_a_negative_number(self, name):
        try:
            info = shared.GetSectionInfo(os.path.join(r'C:\volume', name))
        except NornirUserException:
            return  # Unparseable names raise; that is the other half of the premise.

        self.assertIsNotNone(info.number, f'{name!r} parsed to a None section number')
        self.assertGreaterEqual(info.number, 0,
                                f'{name!r} parsed to a negative section number')

    def test_the_capture_group_admits_no_sign(self):
        # The property tests sample; this reads the guarantee itself, so a future regex edit
        # that admits a sign fails here even if no generated example happens to hit it.
        source_path = os.path.join(os.path.dirname(shared.__file__), 'shared.py')
        with open(source_path, 'r', encoding='utf-8') as handle:
            text = handle.read()

        match = re.search(r'\(\?P<Number>([^)]*)\)', text)
        self.assertIsNotNone(match, 'the Number capture group is no longer recognisable')
        self.assertEqual(r'\d+', match.group(1),
                         'the Number capture group changed; if it now admits a sign, the '
                         'branch removed in #156 is no longer dead code')


class TestAnUnparseableDirectoryIsRejected(unittest.TestCase):
    """The behaviour the removed branch was standing in front of: a clear refusal."""

    def test_a_directory_with_no_section_number_raises(self):
        for name in ['Something', 'TEM', 'RawData', 'notes', 'RC3']:
            with self.assertRaises(NornirUserException):
                shared.GetSectionInfo(os.path.join(r'C:\volume', name))

    def test_the_refusal_names_the_expected_format(self):
        with self.assertRaises(NornirUserException) as caught:
            shared.GetSectionInfo(r'C:\volume\RawData')

        message = str(caught.exception)
        self.assertIn('Section#', message,
                      'the error should tell the user what to name the directory')

    def test_section_zero_is_still_accepted(self):
        # SectionNumber = 0 remains reachable, legitimately, for a directory named for
        # section 0. Removing the dead branch must not turn that into a rejection.
        info = shared.GetSectionInfo(r'C:\volume\0000_Name')
        self.assertEqual(0, info.number)
        self.assertEqual('Name', info.name)


class TestTheStubIsGone(unittest.TestCase):
    """The no-op stub itself."""

    def test_the_no_op_assignment_is_no_longer_in_the_source(self):
        offenders = [line for line in _idoc_code().splitlines()
                     if re.fullmatch(r'\s*i = 5\s*', line)]
        self.assertEqual([], offenders, 'the `i = 5` stub is back in the idoc importer')

    def test_the_section_number_comes_from_the_parsed_directory(self):
        code = _idoc_code()
        self.assertIn('SectionNumber = ExistingSectionInfo.number', code,
                      'the section number should be taken from the parsed directory name')
        self.assertNotIn('ExistingSectionInfo.number < 0', code,
                         'the unreachable negative-number branch is back')


class TestTheRC3HackIsGone(unittest.TestCase):
    """The volume-name-specific mosaic deletion."""

    def test_no_volume_name_is_singled_out(self):
        self.assertNotIn('RC3', _idoc_code(),
                         'the idoc importer branches on a specific volume name again')

    def test_the_mosaic_is_no_longer_deleted_on_a_date_cutoff(self):
        code = _idoc_code()
        self.assertNotIn('cutoff_time', code,
                         'the dated one-time migration is back in the import path')
        self.assertNotIn('os.remove(SupertilePath)', code,
                         'the import path deletes the supertile mosaic again')

    def test_the_removed_migration_would_have_been_dead_weight_by_now(self):
        # The cutoff was the 2022-07-18 deployment date, so the branch could only ever fire
        # for a mosaic older than that. Recording the age here explains the removal.
        cutoff = datetime.datetime(2022, 7, 18, tzinfo=datetime.UTC)
        age_years = (datetime.datetime.now(datetime.UTC) - cutoff).days / 365.25
        self.assertGreater(age_years, 3.0,
                           'the migration cutoff is recent enough that removing it needs '
                           'a second look')


class TestTheSubstringTestWasTooLoose(unittest.TestCase):
    """Why the RC3 test was a hazard and not merely dead weight."""

    @staticmethod
    def would_have_matched(full_path: str) -> bool:
        return 'RC3' in full_path

    def test_it_matched_the_intended_volume(self):
        self.assertTrue(self.would_have_matched(r'C:\volumes\RC3'))

    def test_it_also_matched_unrelated_volumes(self):
        # Each of these would have had its stage.mosaic deleted, forcing an unwanted reimport
        # and invalidating downstream data, despite not being the RC3 volume.
        for path in [r'C:\volumes\RC30', r'C:\volumes\RC31', r'C:\volumes\RC3000',
                     r'C:\users\RC3-backup\TEM', r'C:\ARC3D\volume']:
            self.assertTrue(self.would_have_matched(path),
                            f'{path} was expected to collide with the substring test')
            self.assertNotEqual('RC3', os.path.basename(path.rstrip('\\')),
                                f'{path} is the RC3 volume after all')

    def test_it_missed_a_differently_cased_spelling(self):
        # Loose in one direction and strict in the other: the same volume in lowercase was
        # skipped, so the migration was not even reliable for its one intended target.
        self.assertFalse(self.would_have_matched(r'C:\volumes\rc3'))

    def test_it_left_other_volumes_alone(self):
        for path in [r'C:\volumes\RC2', r'C:\volumes\RC1', r'C:\volumes\TEM']:
            self.assertFalse(self.would_have_matched(path))


class TestTheImporterStillLoads(unittest.TestCase):
    """Cheap guard that the edits did not break the module or leave an unused import."""

    def test_the_module_imports(self):
        self.assertTrue(hasattr(idoc, 'SerialEMIDocImport'))

    def test_datetime_is_no_longer_imported(self):
        # The only use of datetime was the removed cutoff.
        self.assertFalse(hasattr(idoc, 'datetime'),
                         'datetime is imported but no longer used by the idoc importer')


if __name__ == '__main__':
    unittest.main()
