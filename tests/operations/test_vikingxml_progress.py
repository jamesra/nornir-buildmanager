"""Tests for CreateVikingXML iterate_progress telemetry."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ETree
from unittest import mock

from nornir_buildmanager.operations import vikingxml
from nornir_buildmanager.volumemanager.blocknode import BlockNode
from nornir_buildmanager.volumemanager.mappingnode import MappingNode
from nornir_buildmanager.volumemanager.sectionmappingsnode import SectionMappingsNode
from nornir_buildmanager.volumemanager.stosgroupnode import StosGroupNode
from nornir_buildmanager.volumemanager.stosmapnode import StosMapNode
from nornir_buildmanager.volumemanager.transformnode import TransformNode
from nornir_buildmanager.volumemanager.volumenode import VolumeNode


class TestParseSectionsProgress(unittest.TestCase):
    def test_parse_sections_reports_section_fields_and_complete(self) -> None:
        section_a = mock.Mock()
        section_a.Number = 10
        section_a.Name = "10"
        section_a.Path = "0010"
        section_a.Channels = []
        section_a.findall.return_value = []

        section_b = mock.Mock()
        section_b.Number = 11
        section_b.Name = "11"
        section_b.Path = "0011"
        section_b.Channels = []
        section_b.findall.return_value = []

        block = mock.Mock()
        block.Path = "TEM"
        block.Sections = [section_a, section_b]

        volume = mock.Mock()
        volume.findall.return_value = [block]

        output = ETree.Element("Volume")

        with mock.patch.object(vikingxml, "report_iterate") as report:
            with mock.patch.object(vikingxml, "report_iterate_complete") as complete:
                vikingxml.ParseSections(volume, output)

        self.assertEqual(len(output.findall("./Sections/Section")), 2)
        complete.assert_called_once_with("vikingxml:sections", 2)

        # Initial 0/N, then start+finish per section.
        self.assertGreaterEqual(report.call_count, 5)
        start_calls = [
            c for c in report.call_args_list
            if c.args[:1] == ("vikingxml:sections",) and c.kwargs.get("section") == "10"
        ]
        self.assertTrue(start_calls)
        self.assertEqual(start_calls[0].kwargs.get("element"), "10")
        self.assertEqual(start_calls[0].args[1], 0)  # current before ParseSection

    def test_parse_sections_reports_before_channels(self) -> None:
        """Sections track must open before any channel work so the UI can nest bars."""
        channel = mock.Mock()
        channel.Name = "TEM"
        channel.Path = "TEM"
        channel.find.return_value = None
        channel.findall.return_value = []
        channel.Filters = []

        section = mock.Mock()
        section.Number = 42
        section.Name = "42"
        section.Path = "0042"
        section.Channels = [channel]
        section.findall.return_value = []

        block = mock.Mock()
        block.Path = "TEM"
        block.Sections = [section]

        volume = mock.Mock()
        volume.findall.return_value = [block]
        output = ETree.Element("Volume")

        call_order: list[str] = []

        def track_report(*args, **kwargs):
            call_order.append(args[0])

        def track_complete(track_id, total):
            call_order.append(f"complete:{track_id}")

        with mock.patch.object(vikingxml, "report_iterate", side_effect=track_report):
            with mock.patch.object(vikingxml, "report_iterate_complete", side_effect=track_complete):
                vikingxml.ParseSections(volume, output)

        self.assertIn("vikingxml:sections", call_order)
        self.assertIn("vikingxml:channels", call_order)
        first_sections = call_order.index("vikingxml:sections")
        first_channels = call_order.index("vikingxml:channels")
        self.assertLess(
            first_sections,
            first_channels,
            "sections progress must publish before channel progress during work",
        )


class TestParseChannelsProgress(unittest.TestCase):
    def test_parse_channels_reports_nested_track(self) -> None:
        channel_a = mock.Mock()
        channel_a.Name = "TEM"
        channel_a.Path = "TEM"
        channel_a.find.return_value = None
        channel_a.findall.return_value = []
        channel_a.Filters = []

        channel_b = mock.Mock()
        channel_b.Name = "SEM"
        channel_b.Path = "SEM"
        channel_b.find.return_value = None
        channel_b.findall.return_value = []
        channel_b.Filters = []

        section = mock.Mock()
        section.Number = 42
        section.Channels = [channel_a, channel_b]

        output_section = ETree.Element("Section", {"Number": "42"})

        with mock.patch.object(vikingxml, "report_iterate") as report:
            with mock.patch.object(vikingxml, "report_iterate_complete") as complete:
                vikingxml.ParseChannels(section, output_section)

        complete.assert_called_once_with("vikingxml:channels", 2)
        channel_reports = [
            c for c in report.call_args_list
            if c.args[:1] == ("vikingxml:channels",)
        ]
        self.assertGreaterEqual(len(channel_reports), 3)
        labeled = [c for c in channel_reports if c.args[3] == "Channel TEM"]
        self.assertTrue(labeled)
        self.assertEqual(labeled[0].kwargs.get("section"), 42)
        self.assertEqual(labeled[0].kwargs.get("element"), "TEM")
        self.assertEqual(labeled[0].kwargs.get("depth"), 1)


class TestParseStosProgress(unittest.TestCase):
    def test_parse_stos_group_reports_path_and_complete(self) -> None:
        volume = VolumeNode.Create(Name="TestVolume", Path="/tmp/test-volume")
        block = BlockNode.Create(Name="TEM", Path="TEM")
        volume.append(block)

        stos_map = StosMapNode.Create(Name="TestMap")
        stos_map.append(MappingNode.Create(5, [4]))
        block.append(stos_map)

        group = StosGroupNode.Create(Name="GroupA", Downsample=32)
        section_mapping = SectionMappingsNode.Create(MappedSectionNumber=4)
        transform = TransformNode.Create(Name="stos", Type="Grid", Path="GroupA-4-5.stos")
        transform.ControlSectionNumber = 5
        transform.MappedSectionNumber = 4
        section_mapping.append(transform)
        group.append(section_mapping)
        block.append(group)

        output = ETree.Element("Volume", {"num_stos": "0"})

        with mock.patch.object(vikingxml, "report_iterate") as report:
            with mock.patch.object(vikingxml, "report_iterate_complete") as complete:
                added = vikingxml._parse_stos_for_group(volume, output, "TestMap", "GroupA")

        self.assertEqual(added, 1)
        complete.assert_called_once_with("vikingxml:stos:GroupA", 1)
        progress_calls = [
            c for c in report.call_args_list
            if c.args[:1] == ("vikingxml:stos:GroupA",) and c.args[1] == 1
        ]
        self.assertTrue(progress_calls)
        self.assertEqual(progress_calls[0].kwargs.get("element"), "4 -> 5")
        self.assertIn("GroupA-4-5.stos", progress_calls[0].kwargs.get("path", ""))
        self.assertEqual(progress_calls[0].kwargs.get("section"), 4)


if __name__ == "__main__":
    unittest.main()
