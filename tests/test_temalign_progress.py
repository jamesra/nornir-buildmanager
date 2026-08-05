"""Unit tests for TEMAlign explicit iterate_progress tracks."""

from __future__ import annotations

import unittest
from unittest import mock

from nornir_buildmanager.operations import block as block_mod


class TestAssembleStosOverlaysProgress(unittest.TestCase):
    def test_skips_missing_images_still_increment(self) -> None:
        mapping = mock.Mock()
        mapping.Mapped = [10]
        mapping.Control = 11

        stos_map = mock.Mock()
        stos_map.Mappings = [mapping]

        transform = mock.Mock()
        transform.FullPath = "/tmp/a.stos"
        transform.Path = "a.stos"
        transform.Type = "Brute"
        transform.FindParent.return_value = mock.Mock()

        group = mock.Mock()
        group.Name = "StosBrute64"
        group.FullPath = "/tmp/group"
        group.Downsample = 64
        group.TransformsForMapping.return_value = [transform]

        stos_images = mock.Mock()
        stos_images.ControlImageNode = None
        stos_images.MappedImageNode = None

        with mock.patch.object(block_mod.os.path, "exists", return_value=True):
            with mock.patch.object(block_mod.os, "getcwd", return_value="/tmp"):
                with mock.patch.object(block_mod.os, "chdir"):
                    with mock.patch.object(block_mod.os, "makedirs"):
                        with mock.patch.object(block_mod.tempfile, "mkdtemp", return_value="/tmp/x"):
                            with mock.patch.object(block_mod.files, "rmtree"):
                                with mock.patch.object(block_mod, "StosImageNodes", return_value=stos_images):
                                    with mock.patch.object(
                                            block_mod, "GetOrCreateImageNodeHelper",
                                            side_effect=lambda parent, path: (False, mock.Mock())):
                                        with mock.patch.object(block_mod, "report_iterate") as report:
                                            with mock.patch.object(block_mod.nornir_pools, "ReleaseStagePools"):
                                                result = block_mod.AssembleStosOverlays(
                                                    {}, stos_map, group, mock.Mock())

        self.assertIsNone(result)
        currents = [c.args[1] for c in report.call_args_list]
        self.assertEqual(currents, [0, 1])
        self.assertTrue(all(c.args[0] == "stos_overlays:jobs" for c in report.call_args_list))


class TestSelectBestRegistrationChainProgress(unittest.TestCase):
    def test_early_continue_still_increments(self) -> None:
        input_group = mock.Mock()
        input_map = mock.Mock()
        input_map.CenterSection = 5
        input_map.MappedToControls.return_value = {1: [], 2: []}
        input_group.GetSectionMapping.return_value = None

        block = mock.Mock()
        block.NonStosSectionNumbers = set()
        input_group.FindParent.return_value = block
        input_group.attrib = {"Name": "StosBrute64", "Path": "StosBrute64"}

        output_map = mock.Mock()
        output_map.CenterSection = 5
        output_map.ClearBannedControlMappings.return_value = False
        output_map.FindAllControlsForMapped.return_value = set()

        output_group = mock.Mock()

        with mock.patch(
                "nornir_buildmanager.volumemanager.stosmapnode.StosMapNode.Create",
                return_value=output_map):
            with mock.patch.object(block, "UpdateOrAddChildByAttrib", side_effect=[
                (True, output_map),
                (True, output_group),
            ]):
                with mock.patch(
                        "nornir_buildmanager.volumemanager.stosgroupnode.StosGroupNode",
                        return_value=output_group):
                    with mock.patch.object(block_mod, "report_iterate") as report:
                        list(block_mod.SelectBestRegistrationChain(
                            {}, input_group, input_map, "FinalStosMap", mock.Mock()))

        currents = [c.args[1] for c in report.call_args_list]
        self.assertEqual(currents, [0, 1, 2])
        self.assertTrue(all(c.args[0] == "stos_chain:mappings" for c in report.call_args_list))


class TestSliceToVolumeProgress(unittest.TestCase):
    def test_callback_count_matches_precount(self) -> None:
        stos_map = mock.Mock()
        stos_group = mock.Mock()
        stos_group.Downsample = 16
        block = mock.Mock()
        stos_group.Parent = block

        root = mock.Mock()
        root.SectionNumber = 5
        rt = mock.Mock()
        rt.RootNodes = {5: root}
        rt.Nodes = {5: root}
        step = mock.Mock()
        rt.GenerateOrderedMappingsToRootNode.return_value = [step, step, step]

        output_group = mock.Mock()
        output_map = mock.Mock()

        with mock.patch.object(block_mod, "__StosMapToRegistrationTree", return_value=rt):
            with mock.patch.object(block, "GetOrCreateStosGroup", return_value=(False, output_group)):
                with mock.patch.object(block, "RemoveStosMap"):
                    with mock.patch.object(
                            block_mod, "__RegistrationTreeToSliceToVolumeMap", return_value=output_map):
                        with mock.patch.object(block, "UpdateOrAddChildByAttrib", return_value=(False, output_map)):
                            with mock.patch.object(
                                    block_mod, "SliceToVolumeFromRegistrationTreeNode",
                                    side_effect=lambda *a, **k: (k["progress_callback"]() for _ in range(3))):
                                with mock.patch.object(block_mod, "report_iterate") as report:
                                    list(block_mod.BuildSliceToVolumeTransforms(
                                        stos_map, stos_group, None, "SliceToVolume", 16,
                                        False, None, NoLinearBlend=True))

        currents = [c.args[1] for c in report.call_args_list]
        self.assertEqual(currents[0], 0)
        self.assertEqual(currents[-1], 3)
        self.assertTrue(all(c.args[0] == "slice_to_volume:sections" for c in report.call_args_list))


class TestMosaicToVolumeProgress(unittest.TestCase):
    def test_missing_transform_still_increments(self) -> None:
        channel_ok = mock.Mock()
        channel_ok.GetTransform.return_value = mock.Mock()
        channel_ok.GetChildByAttrib.return_value = mock.Mock()

        channel_missing = mock.Mock()
        channel_missing.GetTransform.return_value = None

        block = mock.Mock()
        block.findall.return_value = [channel_missing, channel_ok]
        stos_map = mock.Mock()
        stos_group = mock.Mock()

        with mock.patch.object(
                block_mod.nornir_buildmanager.volumemanager,
                "SearchCollection",
                return_value=[channel_missing, channel_ok]):
            with mock.patch.object(
                    block_mod, "BuildChannelMosaicToVolumeTransform", return_value=None):
                with mock.patch.object(block_mod, "__MoveMosaicsToZeroOrigin"):
                    with mock.patch.object(block_mod, "report_iterate") as report:
                        list(block_mod.BuildMosaicToVolumeTransforms(
                            stos_map, stos_group, block, "TEM", "Grid", "ChannelToVolume",
                            mock.Mock()))

        currents = [c.args[1] for c in report.call_args_list]
        self.assertEqual(currents, [0, 1, 2])
        self.assertTrue(all(c.args[0] == "mosaic_to_volume:sections" for c in report.call_args_list))


class TestStosBrutePairsProgress(unittest.TestCase):
    def test_reports_pair_progress(self) -> None:
        mapping = mock.Mock()
        mapping.Control = 5
        mapping.Mapped = [4]

        mapped_section = mock.Mock()
        mapped_filter = mock.Mock()
        mapped_filter.FullPath = "/m"
        control_filter = mock.Mock()
        control_filter.FullPath = "/c"
        mapped_section.MatchChannelFilterPattern.return_value = [mapped_filter]

        control_section = mock.Mock()
        control_section.MatchChannelFilterPattern.return_value = [control_filter]

        block = mock.Mock()
        block.GetSection.side_effect = lambda n: control_section if n == 5 else mapped_section
        stos_group = mock.Mock()
        stos_group.FullPath = "/tmp/g"
        stos_mapping = mock.Mock()
        stos_mapping.Parent = block
        stos_group.GetOrCreateSectionMapping.return_value = (False, stos_mapping)

        with mock.patch.object(block_mod.os, "makedirs"):
            with mock.patch.object(block, "GetOrCreateStosGroup", return_value=(False, stos_group)):
                with mock.patch.object(
                        block_mod.nornir_buildmanager.volumemanager.stosgroupnode.StosGroupNode,
                        "GenerateStosFilename",
                        return_value="4-5.stos"):
                    with mock.patch.object(
                            block_mod, "FilterToFilterBruteRegistration", return_value=None):
                        with mock.patch.object(block_mod, "report_iterate") as report:
                            list(block_mod.StosBrute(
                                {"Downsample": 64, "UseMasks": False},
                                mapping,
                                block,
                                "TEM",
                                "Blob",
                                mock.Mock(),
                                mock.Mock(),
                                OutputGroup="StosBrute64",
                            ))

        currents = [c.args[1] for c in report.call_args_list]
        self.assertEqual(currents, [0, 1])
        self.assertTrue(all(c.args[0] == "stos_brute:pairs" for c in report.call_args_list))
        self.assertEqual(report.call_args_list[0].kwargs["depth"], 1)


if __name__ == "__main__":
    unittest.main()
