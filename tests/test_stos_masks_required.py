"""Every .stos registration uses the Assemble masks (James, 2026-11-20).

StosBrute used masks only with ``-UseMasks`` and refine could be told ``-IgnoreMasks``. Now
masks are always on, a missing mask stops the stage (MissingMaskError) instead of skipping
the pair, and refine refuses an input .stos that names no masks. A StosBrute .stos that was
registered without masks is recomputed, unless it came from a Manual override.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from xml.etree import ElementTree

import nornir_imageregistration.transforms
from nornir_imageregistration.files import stosfile

import nornir_buildmanager
from nornir_buildmanager.exceptions import MissingMaskError, NornirUserException
from nornir_buildmanager.operations import block


def _write_stos(path: str, masks: bool) -> None:
    transform = nornir_imageregistration.transforms.Rigid(target_offset=(0, 0), source_rotation_center=(5, 5),
                                                          angle=0)
    stos = stosfile.StosFile.Create('c.png', 'm.png', transform,
                                    target_mask_fullpath='cm.png' if masks else None,
                                    source_mask_fullpath='mm.png' if masks else None)
    stos.ControlImageDim = stos.MappedImageDim = [1, 1, 10, 10]
    stos.Save(path, AddMasks=masks)


class TestMissingMaskStopsTheStage(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.image = os.path.join(self._tmp.name, 'image.png')
        open(self.image, 'wb').close()

    def _filter(self, mask_node=None, mask_error: Exception | None = None):
        f = mock.Mock(FullPath='TEM/Leveled')
        f.GetOrCreateImage.return_value = SimpleNamespace(FullPath=self.image)
        if mask_error is not None:
            f.GetOrCreateMaskImage.side_effect = mask_error
        else:
            f.GetOrCreateMaskImage.return_value = mask_node
        return f

    def test_a_mask_file_that_is_missing_raises(self) -> None:
        missing = SimpleNamespace(FullPath=os.path.join(self._tmp.name, 'mask.png'))
        with self.assertRaises(MissingMaskError):
            block.GetOrCreateRegistrationImageNodes(self._filter(missing), 32, get_mask=True)

    def test_a_mask_that_cannot_be_built_raises(self) -> None:
        with self.assertRaises(MissingMaskError):
            block.GetOrCreateRegistrationImageNodes(
                self._filter(mask_error=NornirUserException('no mask level')), 32, get_mask=True)

    def test_a_present_mask_is_returned(self) -> None:
        mask = os.path.join(self._tmp.name, 'mask.png')
        open(mask, 'wb').close()
        image, mask_node = block.GetOrCreateRegistrationImageNodes(
            self._filter(SimpleNamespace(FullPath=mask)), 32, get_mask=True)
        self.assertEqual(self.image, image.FullPath)
        self.assertEqual(mask, mask_node.FullPath)

    def test_missing_mask_is_a_user_exception(self) -> None:
        """Callers that catch NornirUserException must re-raise it, not skip the pair."""
        self.assertTrue(issubclass(MissingMaskError, NornirUserException))


class TestRefineRefusesUnmaskedInput(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_an_input_without_masks_raises(self) -> None:
        path = os.path.join(self._tmp.name, 'in.stos')
        _write_stos(path, masks=False)
        with self.assertRaises(MissingMaskError):
            block._require_stos_masks(path)

    def test_an_input_whose_mask_file_is_missing_raises(self) -> None:
        path = os.path.join(self._tmp.name, 'in.stos')
        _write_stos(path, masks=True)
        with self.assertRaises(MissingMaskError):
            block._require_stos_masks(path)


class TestUnmaskedBrutesAreRecomputed(unittest.TestCase):
    """James chose to recompute brutes registered without masks (2026-11-20, "95) Yes")."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _group(self, masks: bool, manual: str | None = None):
        path = os.path.join(self._tmp.name, 'pair.stos')
        _write_stos(path, masks=masks)
        node = SimpleNamespace(FullPath=path, attrib={'InputTransformChecksum': 'x'} if manual else {})
        group = mock.Mock()
        group.GetStosTransformNode.return_value = node
        group.AreStosInputImagesOutdated.return_value = False
        group.PathToManualTransform.return_value = manual
        return group

    def test_an_unmasked_brute_is_recomputed(self) -> None:
        group = self._group(masks=False)
        self.assertTrue(block.brute_stos_needs_replacement(group, mock.Mock(), mock.Mock(), True))

    def test_a_masked_brute_also_checks_its_masks(self) -> None:
        group = self._group(masks=True)
        self.assertFalse(block.brute_stos_needs_replacement(group, mock.Mock(), mock.Mock(), True))
        self.assertTrue(group.AreStosInputImagesOutdated.call_args.kwargs['MaskRequired'])

    def test_an_unmasked_manual_override_is_kept(self) -> None:
        manual = os.path.join(self._tmp.name, 'manual.stos')
        _write_stos(manual, masks=False)
        group = self._group(masks=False, manual=manual)
        group.GetStosTransformNode.return_value.attrib['InputTransformChecksum'] =             stosfile.StosFile.LoadChecksum(manual)
        self.assertFalse(block.brute_stos_needs_replacement(group, mock.Mock(), mock.Mock(), True))


def _lm_filter(path: str = 'LM/Raw8'):
    """A filter with no Assemble Mask filter, as in an LM volume."""
    f = mock.Mock(FullPath=path, MaskName=None, DefaultMaskName='Mask')
    f.GetMaskFilter.return_value = None
    return f


class TestFiltersWithoutAMaskRegisterFullFrame(unittest.TestCase):
    """James chose a full-frame mask when a filter has no Mask filter (2026-11-20, "142: B")."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        logged = mock.patch.object(block, '_full_frame_mask_filters_logged', set())
        logged.start()
        self.addCleanup(logged.stop)

    def test_a_pair_uses_masks_only_when_both_filters_have_one(self) -> None:
        self.assertTrue(block.pair_uses_masks(mock.Mock(), mock.Mock()))
        self.assertFalse(block.pair_uses_masks(mock.Mock(), _lm_filter()))
        self.assertFalse(block.pair_uses_masks(_lm_filter(), _lm_filter('LM/Raw8b')))

    def test_the_fallback_is_logged_once_per_filter(self) -> None:
        lm = _lm_filter()
        with mock.patch.object(block.prettyoutput, 'Log') as log:
            block.pair_uses_masks(lm, mock.Mock())
            block.pair_uses_masks(mock.Mock(), lm)
        self.assertEqual(1, log.call_count)
        self.assertIn('LM/Raw8', log.call_args.args[0])
        self.assertIn('full-frame', log.call_args.args[0])

    def test_the_default_mask_filter_counts_when_mask_name_is_unset(self) -> None:
        f = mock.Mock(FullPath='TEM/Leveled', MaskName=None, DefaultMaskName='Mask')
        block.pair_uses_masks(f, f)
        f.GetMaskFilter.assert_called_with('Mask')

    def test_an_unmasked_brute_of_an_lm_pair_is_kept(self) -> None:
        path = os.path.join(self._tmp.name, 'pair.stos')
        _write_stos(path, masks=False)
        group = mock.Mock()
        group.GetStosTransformNode.return_value = SimpleNamespace(FullPath=path, attrib={})
        group.AreStosInputImagesOutdated.return_value = False
        group.PathToManualTransform.return_value = None
        lm = _lm_filter()
        self.assertFalse(block.brute_stos_needs_replacement(group, lm, lm, True))
        self.assertFalse(group.AreStosInputImagesOutdated.call_args.kwargs['MaskRequired'])

    def _refine_context(self, use_masks: bool, stos_path: str):
        return SimpleNamespace(use_masks=use_masks, input_stos_path=stos_path,
                               output_stos_path=stos_path + '.out', output_stos_node=mock.Mock(),
                               decision=block.stosgroup_workers.RefineScanDecision.REFINE)

    def test_refine_of_an_lm_pair_runs_without_masks(self) -> None:
        path = os.path.join(self._tmp.name, 'pair.stos')
        _write_stos(path, masks=False)
        refine = mock.Mock(side_effect=MissingMaskError('stop here'))
        with self.assertRaisesRegex(MissingMaskError, 'stop here'):
            block._run_refine_or_manual_copy(self._refine_context(False, path), refine, {})
        refine.assert_called_once()

    def test_refine_of_a_masked_pair_still_refuses_an_unmasked_input(self) -> None:
        path = os.path.join(self._tmp.name, 'pair.stos')
        _write_stos(path, masks=False)
        refine = mock.Mock()
        with self.assertRaisesRegex(MissingMaskError, 'names no masks'):
            block._run_refine_or_manual_copy(self._refine_context(True, path), refine, {})
        refine.assert_not_called()


class TestPipelinesHaveNoMaskSwitch(unittest.TestCase):

    def setUp(self) -> None:
        path = os.path.join(os.path.dirname(nornir_buildmanager.__file__), 'config', 'Pipelines.xml')
        self.root = ElementTree.parse(path).getroot()

    def test_no_pipeline_can_ignore_masks(self) -> None:
        flags = {a.get('flag') for a in self.root.iter('Argument')}
        self.assertNotIn('-IgnoreMasks', flags)

    def test_no_stage_is_handed_a_mask_switch(self) -> None:
        for call in self.root.iter('PythonCall'):
            self.assertNotIn('UseMasks', call.attrib, call.get('Function'))
            self.assertNotIn('IgnoreMasks', call.attrib, call.get('Function'))
            for entry in call.iter('Entry'):
                self.assertNotEqual('UseMasks', entry.get('Name'), call.get('Function'))


if __name__ == '__main__':
    unittest.main()
