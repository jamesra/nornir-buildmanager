"""A rewritten notes file must be recopied even when its mtime did not advance (review #245).

`TryAddNotes` decided whether to copy using `files.RemoveOutdatedFile`, an mtime comparison
that treats *equal* timestamps as "not outdated". The `<Notes>` element is rebuilt from the
**source** file regardless, so when the copy was skipped the two silently disagreed: metadata
carried the new text while `notes.txt` on disk kept the old.

This surfaced as a flaky Hypothesis test. `unittest` runs `setUp` once per test method while
`@given` runs 25 examples inside it, so every example shared one channel and one source
directory, and consecutive examples wrote `notes.txt` fast enough to land on the same mtime.
Measured before the fix, `test_notes_text_round_trips_to_file_and_xml` failed 2 of 12 runs
with a different pair of strings each time:

    'Zã'  != '\\x18;ç㇖çé'
    'Þ'   != '¨\\x9e𪦆'
    't'   != 'K'

Those "wrong" values are not corruption. They are *earlier examples' text*, which is why the
actual was always shorter than and unrelated to the expected. Reading them as mojibake
suggested a codec problem, and two of the three fit that story; `'t' != 'K'` did not, and
following the one that did not fit is what led here. Both are single characters, so no
encoding maps one to the other -- they are simply two different examples.

Confirmed by injecting the timing directly rather than waiting for it: rewriting the source
between calls left the copy stale exactly when the two mtimes came out equal, and inserting a
1.1s pause between examples dropped it to 0 of 5.

The tests below force equal mtimes with `os.utime` so the case is deterministic, rather than
depending on how fast the filesystem clock ticks.

The mtime check is *not* removed. It exists for the image pyramids, where reading both sides
to compare them would cost far more than the copy it guards; content comparison is affordable
here only because notes files are a few KB.

After the fix: 0 of 20 runs of the module failed, about 500 Hypothesis examples.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from xml.sax.saxutils import escape

from nornir_buildmanager.importers.shared import TryAddNotes
from nornir_buildmanager.volumemanager import BlockNode, ChannelNode, VolumeManager


class _NotesFixture(unittest.TestCase):

    def setUp(self) -> None:
        self._temp_dir = tempfile.mkdtemp(prefix='nornir-notes-stale-')
        self.addCleanup(lambda: shutil.rmtree(self._temp_dir, ignore_errors=True))
        volume = VolumeManager.Load(os.path.join(self._temp_dir, 'volume'), Create=True)
        assert volume is not None
        [_a, block] = volume.UpdateOrAddChildByAttrib(BlockNode.Create('TEM'), 'Name')
        [_b, section] = block.GetOrCreateSection(9)
        [_c, self.channel] = section.UpdateOrAddChildByAttrib(
            ChannelNode.Create('TEM'), 'Name')
        self.source_dir = os.path.join(self._temp_dir, '0009_Source')
        os.makedirs(self.source_dir, exist_ok=True)

    @property
    def source_path(self) -> str:
        return os.path.join(self.source_dir, 'notes.txt')

    @property
    def copied_path(self) -> str:
        return os.path.join(self.channel.FullPath, 'notes.txt')

    def write_source(self, text: str, mtime_ns: int | None = None) -> None:
        """Set the mtime in nanoseconds, not as a float.

        os.utime with a float from getmtime does not round-trip exactly, so the source can
        land a hair newer than the copy and the mtime check fires after all. That made an
        earlier version of these tests pass intermittently against the unfixed code -- the
        same nondeterminism as the bug under test, which is worth avoiding in the test that
        pins it.
        """
        with open(self.source_path, 'w', encoding='utf-8') as handle:
            handle.write(text)
        if mtime_ns is not None:
            os.utime(self.source_path, ns=(mtime_ns, mtime_ns))

    def copied_mtime_ns(self) -> int:
        return os.stat(self.copied_path).st_mtime_ns

    def copied_text(self) -> str:
        with open(self.copied_path, encoding='utf-8') as handle:
            return handle.read()

    def xml_text(self) -> str | None:
        node = self.channel.find('Notes')
        return None if node is None else node.text


class TestAStaleCopyIsReplaced(_NotesFixture):

    def test_new_text_reaches_disk_when_the_mtime_did_not_advance(self):
        self.write_source('the first notes')
        TryAddNotes(self.channel, self.source_dir, None)
        frozen = self.copied_mtime_ns()

        self.write_source('the second notes', mtime_ns=frozen)
        TryAddNotes(self.channel, self.source_dir, None)

        self.assertEqual('the second notes', self.copied_text(),
                         'the copy kept the previous text because the mtimes matched')

    def test_the_file_and_the_metadata_do_not_disagree(self):
        """The <Notes> element is built from the source, so a skipped copy diverges."""
        self.write_source('the first notes')
        TryAddNotes(self.channel, self.source_dir, None)
        frozen = self.copied_mtime_ns()

        self.write_source('the second notes', mtime_ns=frozen)
        TryAddNotes(self.channel, self.source_dir, None)

        self.assertEqual(escape(self.copied_text()), self.xml_text(),
                         'notes.txt and VolumeData.xml describe different text')

    def test_it_reports_a_change_so_callers_save_the_volume(self):
        self.write_source('the first notes')
        TryAddNotes(self.channel, self.source_dir, None)
        frozen = self.copied_mtime_ns()

        self.write_source('the second notes', mtime_ns=frozen)
        self.assertTrue(TryAddNotes(self.channel, self.source_dir, None))

    def test_an_older_source_still_updates_when_the_content_differs(self):
        """Rewriting a file can leave its mtime *behind* the copy, not merely equal."""
        self.write_source('the first notes')
        TryAddNotes(self.channel, self.source_dir, None)
        frozen = self.copied_mtime_ns()

        self.write_source('the second notes', mtime_ns=frozen - 600_000_000_000)
        TryAddNotes(self.channel, self.source_dir, None)

        self.assertEqual('the second notes', self.copied_text())

    def test_non_ascii_text_survives_the_replacement(self):
        """The reported failures were all non-ASCII, so pin that they are not the cause."""
        self.write_source('\u00e9\u7ea8 first')
        TryAddNotes(self.channel, self.source_dir, None)
        frozen = self.copied_mtime_ns()

        self.write_source('\u00e9\u7ea8 second \U0002a986', mtime_ns=frozen)
        TryAddNotes(self.channel, self.source_dir, None)

        self.assertEqual('\u00e9\u7ea8 second \U0002a986', self.copied_text())


class TestItStillDoesNothingWhenNothingChanged(_NotesFixture):
    """Content comparison must not turn every call into a copy."""

    def test_an_unchanged_notes_file_reports_no_change(self):
        self.write_source('unchanged notes')
        self.assertTrue(TryAddNotes(self.channel, self.source_dir, None))
        self.assertFalse(TryAddNotes(self.channel, self.source_dir, None),
                         'a second call with identical content should be a no-op')

    def test_an_unchanged_file_is_not_rewritten(self):
        self.write_source('unchanged notes')
        TryAddNotes(self.channel, self.source_dir, None)
        before = os.stat(self.copied_path).st_mtime_ns

        TryAddNotes(self.channel, self.source_dir, None)

        self.assertEqual(before, os.stat(self.copied_path).st_mtime_ns,
                         'the destination was rewritten despite identical content')


class TestTheContentComparison(_NotesFixture):
    """The helper itself. Imported locally so the behavioural tests above still collect
    against a build that predates it, and demonstrate the real failure rather than an
    ImportError."""

    @staticmethod
    def differs(source, dest):
        from nornir_buildmanager.importers.shared import _notes_content_differs

        return _notes_content_differs(source, dest)

    def test_a_missing_destination_counts_as_different(self):
        self.write_source('some notes')
        self.assertTrue(self.differs(self.source_path, os.path.join(self._temp_dir, 'absent.txt')))

    def test_identical_bytes_are_not_different(self):
        self.write_source('some notes')
        TryAddNotes(self.channel, self.source_dir, None)
        self.assertFalse(self.differs(self.source_path, self.copied_path))

    def test_differing_bytes_are_different(self):
        self.write_source('some notes')
        TryAddNotes(self.channel, self.source_dir, None)
        self.write_source('other notes')
        self.assertTrue(self.differs(self.source_path, self.copied_path))

    def test_it_compares_bytes_rather_than_decoded_text(self):
        """Notes files come off a microscope; they are not guaranteed to be valid UTF-8."""
        with open(self.source_path, 'wb') as handle:
            handle.write(b'\xff\xfe not valid utf-8')
        dest = os.path.join(self._temp_dir, 'dest.txt')
        with open(dest, 'wb') as handle:
            handle.write(b'\xff\xfe not valid utf-8')

        self.assertFalse(self.differs(self.source_path, dest))

    def test_a_directory_as_destination_does_not_raise(self):
        self.write_source('some notes')
        self.assertTrue(self.differs(self.source_path, self._temp_dir))


if __name__ == '__main__':
    unittest.main()
