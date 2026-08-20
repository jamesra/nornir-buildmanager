"""Tests for RecoverNotes ImportDir scanning and TryAddNotes freshness."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from typing import cast
from argparse import Namespace
from xml.sax.saxutils import escape

from hypothesis import given, settings, strategies as st

from nornir_buildmanager.build import BuildParserRoot, call_recover_import_meta_data
from nornir_buildmanager.importers.shared import RecoverNotesFromImportDir, TryAddNotes
from nornir_buildmanager.volumemanager import (
    BlockNode,
    ChannelNode,
    VolumeManager,
    VolumeNode,
)


def _write_notes(folder: str, text: str, filename: str = 'notes.txt') -> str:
    os.makedirs(folder, exist_ok=True)
    notes_path = os.path.join(folder, filename)
    with open(notes_path, 'w', encoding='utf-8') as handle:
        handle.write(text)
    return notes_path


def _load_volume(path: str, create: bool = True) -> VolumeNode:
    volume = VolumeManager.Load(path, Create=create)
    assert volume is not None
    return cast(VolumeNode, volume)


def _notes_text(channel) -> str | None:
    notes_node = channel.find('Notes')
    if notes_node is None:
        return None
    return notes_node.text


class TestRecoverNotesImportDir(unittest.TestCase):

    def setUp(self) -> None:
        self._temp_dir = tempfile.mkdtemp(prefix='nornir-recover-notes-')
        self.addCleanup(lambda: shutil.rmtree(self._temp_dir, ignore_errors=True))
        self.volume_path = os.path.join(self._temp_dir, 'volume')
        self.import_path = os.path.join(self._temp_dir, 'import_src')
        os.makedirs(self.import_path, exist_ok=True)
        self.volume = _load_volume(self.volume_path)
        self.volume.AttributesChanged = True
        self.volume.Save()

    def _create_existing_section(self, number: int, channel_name: str = 'TEM'):
        [_added_block, block] = self.volume.UpdateOrAddChildByAttrib(BlockNode.Create('TEM'), 'Name')
        [_added_section, section] = block.GetOrCreateSection(number)
        [_added_channel, channel] = section.UpdateOrAddChildByAttrib(ChannelNode.Create(channel_name), 'Name')
        return section, channel

    def test_parser_accepts_optional_import_dir(self) -> None:
        parser = BuildParserRoot()
        with_import = parser.parse_args(['RecoverNotes', '/vol', '/raw', '-save'])
        self.assertEqual(with_import.ImportDir, '/raw')
        self.assertTrue(with_import.save_restoration)

        without_import = parser.parse_args(['RecoverNotes', '/vol'])
        self.assertIsNone(without_import.ImportDir)
        self.assertFalse(without_import.save_restoration)

    def test_importdir_updates_existing_section_channel(self) -> None:
        _section, channel = self._create_existing_section(1)
        source_dir = os.path.join(self.import_path, '0001_EggID4567')
        _write_notes(source_dir, 'section one notes')

        changed = RecoverNotesFromImportDir(self.volume, self.import_path)
        self.assertTrue(changed)
        copied = os.path.join(channel.FullPath, 'notes.txt')
        self.assertTrue(os.path.isfile(copied))
        with open(copied, encoding='utf-8') as handle:
            self.assertEqual(handle.read(), 'section one notes')
        self.assertEqual(_notes_text(channel), escape('section one notes'))

    def test_importdir_creates_missing_tem_section_and_channel(self) -> None:
        source_dir = os.path.join(self.import_path, '0002_NewSection')
        _write_notes(source_dir, 'created from import')

        changed = RecoverNotesFromImportDir(self.volume, self.import_path)
        self.assertTrue(changed)

        block = self.volume.GetBlock('TEM')
        self.assertIsNotNone(block)
        section = block.GetSection(2)
        self.assertIsNotNone(section)
        channel = section.GetChannel('TEM')
        self.assertIsNotNone(channel)
        copied = os.path.join(channel.FullPath, 'notes.txt')
        self.assertTrue(os.path.isfile(copied))
        self.assertEqual(_notes_text(channel), escape('created from import'))

    def test_importdir_applies_notes_to_existing_non_tem_channel(self) -> None:
        _section, channel = self._create_existing_section(5, channel_name='LM')
        source_dir = os.path.join(self.import_path, '0005_Probe')
        _write_notes(source_dir, 'lm channel notes')

        RecoverNotesFromImportDir(self.volume, self.import_path)
        self.assertEqual(_notes_text(channel), escape('lm channel notes'))
        self.assertTrue(os.path.isfile(os.path.join(channel.FullPath, 'notes.txt')))

    def test_changed_notes_recopy_and_update_xml(self) -> None:
        _section, channel = self._create_existing_section(3)
        source_dir = os.path.join(self.import_path, '0003_Recapture')
        source_notes = _write_notes(source_dir, 'original notes')

        self.assertTrue(TryAddNotes(channel, source_dir, None))
        copied = os.path.join(channel.FullPath, 'notes.txt')
        self.assertEqual(_notes_text(channel), escape('original notes'))

        past = time.time() - 120
        os.utime(copied, (past, past))
        with open(source_notes, 'w', encoding='utf-8') as handle:
            handle.write('updated notes')
        os.utime(source_notes, None)

        changed = TryAddNotes(channel, source_dir, None)
        self.assertTrue(changed)
        with open(copied, encoding='utf-8') as handle:
            self.assertEqual(handle.read(), 'updated notes')
        self.assertEqual(_notes_text(channel), escape('updated notes'))

    def test_without_importdir_attaches_notes_on_volume_container(self) -> None:
        _write_notes(self.volume_path, 'volume root notes')
        args = Namespace(volumepath=self.volume_path, ImportDir=None, save_restoration=True)
        call_recover_import_meta_data(args)

        reloaded = _load_volume(self.volume_path, create=False)
        self.assertEqual(_notes_text(reloaded), escape('volume root notes'))
        self.assertTrue(os.path.isfile(os.path.join(self.volume_path, 'notes.txt')))

    def test_importdir_fallback_from_capture_filename(self) -> None:
        flat_dir = os.path.join(self.import_path, 'captures')
        os.makedirs(flat_dir, exist_ok=True)
        with open(os.path.join(flat_dir, '0007.mrc'), 'w', encoding='utf-8') as handle:
            handle.write('placeholder')
        _write_notes(flat_dir, 'flat mrc notes')

        changed = RecoverNotesFromImportDir(self.volume, self.import_path)
        self.assertTrue(changed)
        block = self.volume.GetBlock('TEM')
        section = block.GetSection(7)
        self.assertIsNotNone(section)
        channel = section.GetChannel('TEM')
        self.assertEqual(_notes_text(channel), escape('flat mrc notes'))

    def test_skips_contrast_overrides_and_timing(self) -> None:
        _section, channel = self._create_existing_section(8)
        source_dir = os.path.join(self.import_path, '0008_Skip')
        _write_notes(source_dir, 'min max', filename='ContrastOverrides.txt')
        _write_notes(source_dir, 'timing', filename='Timing.txt')

        changed = RecoverNotesFromImportDir(self.volume, self.import_path)
        self.assertFalse(changed)
        self.assertIsNone(_notes_text(channel))

    def test_call_recover_saves_importdir_notes_recursively(self) -> None:
        source_dir = os.path.join(self.import_path, '0004_Saved')
        _write_notes(source_dir, 'persisted notes')
        args = Namespace(volumepath=self.volume_path, ImportDir=self.import_path, save_restoration=True)
        call_recover_import_meta_data(args)

        reloaded = _load_volume(self.volume_path, create=False)
        block = reloaded.GetBlock('TEM')
        self.assertIsNotNone(block)
        section = block.GetSection(4)
        self.assertIsNotNone(section)
        channel = section.GetChannel('TEM')
        self.assertEqual(_notes_text(channel), escape('persisted notes'))


class TestTryAddNotesRoundTrip(unittest.TestCase):

    def setUp(self) -> None:
        self._temp_dir = tempfile.mkdtemp(prefix='nornir-tryadd-notes-')
        self.addCleanup(lambda: shutil.rmtree(self._temp_dir, ignore_errors=True))
        self.volume = _load_volume(os.path.join(self._temp_dir, 'volume'))
        [_added_block, block] = self.volume.UpdateOrAddChildByAttrib(BlockNode.Create('TEM'), 'Name')
        [_added_section, section] = block.GetOrCreateSection(9)
        [_added_channel, self.channel] = section.UpdateOrAddChildByAttrib(ChannelNode.Create('TEM'), 'Name')
        self.source_dir = os.path.join(self._temp_dir, '0009_RoundTrip')

    @given(notes_text=st.text(
        alphabet=st.characters(blacklist_categories=('Cs',), blacklist_characters='\0\r'),
        min_size=1,
        max_size=120,
    ).filter(lambda value: value.strip()))
    @settings(max_examples=25, deadline=None)
    def test_notes_text_round_trips_to_file_and_xml(self, notes_text: str) -> None:
        _write_notes(self.source_dir, notes_text)
        self.assertTrue(TryAddNotes(self.channel, self.source_dir, None))
        copied = os.path.join(self.channel.FullPath, 'notes.txt')
        with open(copied, encoding='utf-8') as handle:
            self.assertEqual(handle.read(), notes_text)
        self.assertEqual(_notes_text(self.channel), escape(notes_text))


if __name__ == '__main__':
    unittest.main()
