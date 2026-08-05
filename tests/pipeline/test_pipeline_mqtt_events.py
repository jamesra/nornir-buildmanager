"""Tests for PipelineManager MQTT telemetry hooks."""

import unittest
from unittest import mock
from xml.etree import ElementTree

import nornir_buildmanager.pipelinemanager as pm
from nornir_buildmanager.volumemanager.sectionnode import SectionNode
from nornir_buildmanager.volumemanager.channelnode import ChannelNode
from nornir_buildmanager.volumemanager.filternode import FilterNode
from nornir_buildmanager.volumemanager.mappingnode import MappingNode


class TestElementTelemetryFields(unittest.TestCase):
    def test_section_node_label(self) -> None:
        section = SectionNode.Create(Number=63)
        fields = pm.PipelineManager._ElementTelemetryFields(section)
        self.assertEqual(fields["section"], 63)
        self.assertEqual(fields["label"], "section_node - 0063")

    def test_channel_node_label(self) -> None:
        channel = ChannelNode.Create("TEM")
        fields = pm.PipelineManager._ElementTelemetryFields(channel)
        self.assertEqual(fields["element"], "TEM")
        self.assertEqual(fields["label"], "ChannelNode - TEM")

    def test_filter_node_label(self) -> None:
        filt = FilterNode.Create("Leveled")
        fields = pm.PipelineManager._ElementTelemetryFields(filt)
        self.assertEqual(fields["label"], "filter node - Leveled")

    def test_mapping_node_label(self) -> None:
        mapping = MappingNode.Create(1334, [1333])
        fields = pm.PipelineManager._ElementTelemetryFields(mapping)
        self.assertEqual(fields["section"], 1334)
        self.assertEqual(fields["element"], "1334 <- 1333")
        self.assertEqual(fields["label"], "MappingNode - 1334 <- 1333")

    def test_variable_name_does_not_override_descriptive_label(self) -> None:
        section = SectionNode.Create(Number=1)
        node = ElementTree.Element("Iterate", VariableName="SectionNode")
        fields = pm.PipelineManager._ElementTelemetryFields(section, node)
        self.assertEqual(fields["label"], "section_node - 0001")


class TestProcessIterateMqtt(unittest.TestCase):
    def test_iterate_publishes_filtered_totals_and_depth(self) -> None:
        manager = pm.PipelineManager(
            pipelinesRoot=ElementTree.Element("Root"),
            pipelineData=ElementTree.Element("Pipeline"),
        )
        iterate_node = ElementTree.Element(
            "Iterate", VariableName="SectionNode", XPath="Block/Section")
        arg_set = mock.Mock()
        volume = mock.Mock()
        root = mock.Mock()
        candidates = [SectionNode.Create(Number=i + 1) for i in range(2)]

        with mock.patch.object(pm.PipelineManager, "_PipelineManager__extractXPathFromNode",
                               return_value="Block/Section"):
            with mock.patch.object(pm.PipelineManager, "GetSearchRoot", return_value=root):
                with mock.patch(
                        "nornir_buildmanager.pipelinemanager.resolve_iterate_candidates",
                        return_value=candidates) as resolve:
                    with mock.patch.object(pm.PipelineManager, "_ElementNeedsValidation",
                                           return_value=False):
                        with mock.patch.object(manager, "ExecuteChildPipelines", return_value=1):
                            with mock.patch(
                                    "nornir_buildmanager.pipelinemanager.publish_run_event"
                            ) as publish:
                                manager.ProcessIterateNode(arg_set, volume, iterate_node)

        resolve.assert_called_once()
        event_names = [c.args[0] for c in publish.call_args_list]
        self.assertEqual(event_names[0], "iterate_progress")
        self.assertEqual(publish.call_args_list[0].kwargs["total"], 2)
        self.assertEqual(publish.call_args_list[0].kwargs["current"], 0)
        self.assertEqual(publish.call_args_list[0].kwargs["depth"], 0)
        self.assertEqual(publish.call_args_list[1].kwargs["current"], 1)
        self.assertEqual(publish.call_args_list[2].kwargs["current"], 2)
        self.assertEqual(manager._iterate_depth, 0)


class TestProcessPythonCallMqtt(unittest.TestCase):
    def test_stage_start_and_end_published(self) -> None:
        manager = pm.PipelineManager(
            pipelinesRoot=ElementTree.Element("Root"),
            pipelineData=ElementTree.Element("Pipeline"),
        )
        manager.VolumeTree = mock.Mock()
        pipeline_node = ElementTree.Element(
            "PythonCall", Module="nornir_buildmanager.operations.tile",
            Function="AssembleTransform")
        volume = SectionNode.Create(Number=7)
        arg_set = mock.Mock()
        arg_set.Arguments = {"verbose": False, "debug": False}
        arg_set.KeyWordArgs.return_value = {"Parameters": {}}
        arg_set.AddAttributes = mock.Mock()
        arg_set.AddParameters = mock.Mock()
        arg_set.ClearAttributes = mock.Mock()
        arg_set.ClearParameters = mock.Mock()

        stage_func = mock.Mock(return_value=None)

        with mock.patch("nornir_shared.reflection.get_module_class", return_value=stage_func):
            with mock.patch.object(pm.PipelineManager, "_SaveNodes"):
                with mock.patch("nornir_buildmanager.pipelinemanager.publish_run_event") as publish:
                    with mock.patch("nornir_buildmanager.pipelinemanager.prettyoutput.CurseString"):
                        manager.ProcessPythonCall(arg_set, volume, pipeline_node)

        names = [c.args[0] for c in publish.call_args_list]
        self.assertEqual(names, ["stage_start", "stage_end"])
        self.assertEqual(publish.call_args_list[0].kwargs["function"], "AssembleTransform")
        self.assertEqual(publish.call_args_list[0].kwargs["section"], 7)


if __name__ == "__main__":
    unittest.main()
