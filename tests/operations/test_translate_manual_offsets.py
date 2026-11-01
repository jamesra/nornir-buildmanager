"""Manual offsets survive a lost file, and the translate report surfaces a bad join.

RC3 1637's manual offsets went missing at some point. TranslateTransform then wrote an empty
offsets file, which also made it newer than the mosaic, so the next build would have rebuilt the
section without the fix and said nothing. The translate report now records the offsets a mosaic
was built with, so a missing file is restored from it; if it cannot be, the mosaic is left alone.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import nornir_imageregistration
from nornir_buildmanager.operations import registration
from nornir_imageregistration.settings import LoadMosaicOffsets, TileOffset
from nornir_imageregistration.translate_report import TranslateReport


class _FakeTransformNode:
    def __init__(self, attrib: dict[str, str] | None = None):
        self.attrib = dict(attrib or {})
        self.FullPath = 'Translated_Prune_Max0.5.mosaic'


class _FakeParentNode:
    def __init__(self):
        self.children = []

    def UpdateOrAddChildByAttrib(self, node, attrib_name):
        self.children.append(node)
        return True, node


class _TempDir(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_manual_offsets_')
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.offsets_path = os.path.join(self.tmpdir, 'Translated_Prune_ManualOffsets.csv')
        self.report_path = os.path.join(self.tmpdir, 'Translated_Prune_TranslateReport.json')


class TestRestoreManualOffsets(_TempDir):

    def write_report(self, offsets):
        report = TranslateReport()
        report.set_known_offsets(offsets, [])
        report.save(self.report_path)

    def test_a_missing_file_is_restored_from_the_report(self):
        used = [TileOffset(A=445, B=17, Y=-4800.0, X=1300.0, Comment='1637 block')]
        self.write_report(used)
        with mock.patch.object(registration.prettyoutput, 'LogErr') as logged:
            self.assertTrue(registration._RestoreManualOffsets(self.offsets_path, self.report_path,
                                                               _FakeTransformNode({'ManualOffsetsCount': '1'}), None))
        self.assertEqual(used, LoadMosaicOffsets(self.offsets_path))
        self.assertIn('restored 1 offsets', str(logged.call_args))

    def test_offsets_that_cannot_be_restored_block_the_rebuild(self):
        with mock.patch.object(registration.prettyoutput, 'LogErr'):
            self.assertFalse(registration._RestoreManualOffsets(
                self.offsets_path, self.report_path, _FakeTransformNode({'ManualOffsetsCount': '3'}), None))
        self.assertFalse(os.path.exists(self.offsets_path))

    def test_a_mosaic_built_without_offsets_proceeds(self):
        for node in (None, _FakeTransformNode(), _FakeTransformNode({'ManualOffsetsCount': '0'})):
            with self.subTest(node=node):
                self.assertTrue(registration._RestoreManualOffsets(self.offsets_path, self.report_path, node, None))

    def test_a_report_with_no_offsets_restores_nothing(self):
        self.write_report([])
        self.assertTrue(registration._RestoreManualOffsets(self.offsets_path, self.report_path, None, None))
        self.assertFalse(os.path.exists(self.offsets_path))


class TestSaveTranslateReport(_TempDir):

    def test_a_join_needing_review_marks_the_transform_and_logs_why(self):
        report = TranslateReport(review_reasons=['Join between group 0 (553 tiles) and group 1 (300 tiles) ...'])
        node = _FakeTransformNode()
        parent = _FakeParentNode()
        with mock.patch.object(registration.prettyoutput, 'LogErr') as logged:
            registration._SaveTranslateReport(report, self.report_path, os.path.basename(self.report_path),
                                              parent, node, None)
        self.assertEqual('1', node.attrib['NeedsReview'])
        self.assertIn('group 1 (300 tiles)', str(logged.call_args))
        with open(self.report_path) as f:
            self.assertTrue(json.load(f)['needs_review'])
        self.assertEqual(1, len(parent.children))

    def test_a_clean_report_clears_an_old_review_mark(self):
        node = _FakeTransformNode({'NeedsReview': '1'})
        registration._SaveTranslateReport(TranslateReport(), self.report_path, os.path.basename(self.report_path),
                                          _FakeParentNode(), node, None)
        self.assertNotIn('NeedsReview', node.attrib)


class TestOffsetsChecksumDecidesTheRebuild(unittest.TestCase):
    """TranslateTransform compares the recorded content checksum, not file timestamps."""

    def test_the_comparison_uses_the_recorded_content_checksum(self):
        import inspect
        source = inspect.getsource(registration.TranslateTransform)
        self.assertIn("attrib.get('ManualOffsetsChecksum')", source)
        self.assertIn("attrib['ManualOffsetsChecksum'] = manual_offsets_checksum", source)
        self.assertIn('report=translate_report', source)

    def test_restored_offsets_match_the_recorded_checksum(self):
        used = [TileOffset(A=17, B=445, Y=4800.0, X=-1300.0, Comment='1637 block')]
        recorded = nornir_imageregistration.settings.MosaicOffsetsChecksum(used)
        tmpdir = tempfile.mkdtemp(prefix='test_manual_offsets_')
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        path = os.path.join(tmpdir, 'offsets.csv')
        nornir_imageregistration.settings.SaveMosaicOffsets(used, path)
        self.assertEqual(recorded, nornir_imageregistration.settings.MosaicOffsetsChecksum(LoadMosaicOffsets(path)))


if __name__ == '__main__':
    unittest.main()
