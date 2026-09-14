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
        self.assertIsNone(publish.call_args_list[0].kwargs["total"])
        self.assertEqual(publish.call_args_list[0].kwargs["current"], 0)
        self.assertEqual(publish.call_args_list[0].kwargs["depth"], 0)
        self.assertEqual(publish.call_args_list[0].kwargs["label"], "SectionNode")
        self.assertEqual(publish.call_args_list[1].kwargs["current"], 1)
        self.assertIsNone(publish.call_args_list[1].kwargs["total"])
        self.assertEqual(publish.call_args_list[1].kwargs["label"], "SectionNode")
        self.assertEqual(publish.call_args_list[1].kwargs["section"], 1)
        self.assertEqual(publish.call_args_list[2].kwargs["current"], 2)
        self.assertEqual(publish.call_args_list[2].kwargs["label"], "SectionNode")
        self.assertEqual(event_names[-1], "iterate_progress_complete")
        self.assertEqual(
            publish.call_args_list[-1].kwargs["track_id"], "iterate:SectionNode")
        self.assertEqual(publish.call_args_list[-1].kwargs["total"], 2)
        self.assertEqual(manager._iterate_depth, 0)

    def test_iterate_streams_generator_without_preloading(self) -> None:
        """#140: first child runs before later candidates are yielded."""
        manager = pm.PipelineManager(
            pipelinesRoot=ElementTree.Element("Root"),
            pipelineData=ElementTree.Element("Pipeline"),
        )
        iterate_node = ElementTree.Element(
            "Iterate", VariableName="SectionNode", XPath="Block/Section")
        yield_count = 0

        def _gen():
            nonlocal yield_count
            for i in range(3):
                yield_count += 1
                yield SectionNode.Create(Number=i + 1)

        seen_yields_at_first_child: list[int] = []

        def _on_child(*_a, **_k):
            seen_yields_at_first_child.append(yield_count)
            return 1

        with mock.patch.object(pm.PipelineManager, "_PipelineManager__extractXPathFromNode",
                               return_value="Block/Section"):
            with mock.patch.object(pm.PipelineManager, "GetSearchRoot", return_value=mock.Mock()):
                with mock.patch(
                        "nornir_buildmanager.pipelinemanager.resolve_iterate_candidates",
                        return_value=_gen()):
                    with mock.patch.object(pm.PipelineManager, "_ElementNeedsValidation",
                                           return_value=False):
                        with mock.patch.object(manager, "ExecuteChildPipelines",
                                               side_effect=_on_child):
                            with mock.patch(
                                    "nornir_buildmanager.pipelinemanager.publish_run_event"):
                                manager.ProcessIterateNode(
                                    mock.Mock(), mock.Mock(), iterate_node)

        self.assertEqual(seen_yields_at_first_child[0], 1)
        self.assertEqual(seen_yields_at_first_child, [1, 2, 3])
        self.assertEqual(yield_count, 3)

    def test_iterate_publishes_complete_on_child_exception(self) -> None:
        manager = pm.PipelineManager(
            pipelinesRoot=ElementTree.Element("Root"),
            pipelineData=ElementTree.Element("Pipeline"),
        )
        iterate_node = ElementTree.Element(
            "Iterate", VariableName="SectionNode", XPath="Block/Section")
        candidates = [SectionNode.Create(Number=1)]

        with mock.patch.object(pm.PipelineManager, "_PipelineManager__extractXPathFromNode",
                               return_value="Block/Section"):
            with mock.patch.object(pm.PipelineManager, "GetSearchRoot", return_value=mock.Mock()):
                with mock.patch(
                        "nornir_buildmanager.pipelinemanager.resolve_iterate_candidates",
                        return_value=candidates):
                    with mock.patch.object(pm.PipelineManager, "_ElementNeedsValidation",
                                           return_value=False):
                        with mock.patch.object(
                                manager, "ExecuteChildPipelines",
                                side_effect=RuntimeError("boom")):
                            with mock.patch(
                                    "nornir_buildmanager.pipelinemanager.publish_run_event"
                            ) as publish:
                                with self.assertRaises(RuntimeError):
                                    manager.ProcessIterateNode(
                                        mock.Mock(), mock.Mock(), iterate_node)

        event_names = [c.args[0] for c in publish.call_args_list]
        self.assertEqual(event_names[-1], "iterate_progress_complete")
        self.assertEqual(manager._iterate_depth, 0)

    def test_channel_iterate_keeps_variable_name_label(self) -> None:
        manager = pm.PipelineManager(
            pipelinesRoot=ElementTree.Element("Root"),
            pipelineData=ElementTree.Element("Pipeline"),
        )
        iterate_node = ElementTree.Element(
            "Iterate", VariableName="ChannelNode", XPath="Channel")
        candidates = [ChannelNode.Create("TEM")]

        with mock.patch.object(pm.PipelineManager, "_PipelineManager__extractXPathFromNode",
                               return_value="Channel"):
            with mock.patch.object(pm.PipelineManager, "GetSearchRoot", return_value=mock.Mock()):
                with mock.patch(
                        "nornir_buildmanager.pipelinemanager.resolve_iterate_candidates",
                        return_value=candidates):
                    with mock.patch.object(pm.PipelineManager, "_ElementNeedsValidation",
                                           return_value=False):
                        with mock.patch.object(manager, "ExecuteChildPipelines", return_value=1):
                            with mock.patch(
                                    "nornir_buildmanager.pipelinemanager.publish_run_event"
                            ) as publish:
                                manager.ProcessIterateNode(
                                    mock.Mock(), mock.Mock(), iterate_node)

        opening = publish.call_args_list[0]
        self.assertEqual(opening.args[0], "iterate_progress")
        self.assertEqual(opening.kwargs["label"], "ChannelNode")
        self.assertEqual(opening.kwargs["current"], 0)
        self.assertIsNone(opening.kwargs["total"])
        self.assertNotIn("element", opening.kwargs)

        item = publish.call_args_list[1]
        self.assertEqual(item.kwargs["label"], "ChannelNode")
        self.assertEqual(item.kwargs["element"], "TEM")
        self.assertEqual(item.kwargs["current"], 1)
        self.assertIsNone(item.kwargs["total"])
        self.assertEqual(publish.call_args_list[-1].kwargs["total"], 1)


class TestProcessPythonCallMqtt(unittest.TestCase):
    def _make_python_call_fixture(self):
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
        return manager, pipeline_node, volume, arg_set

    def test_stage_start_and_end_published(self) -> None:
        manager, pipeline_node, volume, arg_set = self._make_python_call_fixture()
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

    def test_top_level_stage_curse_start_and_completed(self) -> None:
        manager, pipeline_node, volume, arg_set = self._make_python_call_fixture()
        stage_func = mock.Mock(return_value=None)

        with mock.patch("nornir_shared.reflection.get_module_class", return_value=stage_func):
            with mock.patch.object(pm.PipelineManager, "_SaveNodes"):
                with mock.patch("nornir_buildmanager.pipelinemanager.publish_run_event"):
                    with mock.patch(
                            "nornir_buildmanager.pipelinemanager.prettyoutput.CurseString") as curse:
                        manager.ProcessPythonCall(arg_set, volume, pipeline_node)

        texts = [c.args[1] for c in curse.call_args_list]
        self.assertEqual(len(texts), 2)
        self.assertTrue(texts[0].endswith("AssembleTransform"))
        self.assertTrue(texts[1].endswith("AssembleTransform completed"))

    def test_nested_fast_stage_suppresses_status_curse(self) -> None:
        manager, pipeline_node, volume, arg_set = self._make_python_call_fixture()
        manager._iterate_depth = 1
        stage_func = mock.Mock(return_value=None)

        with mock.patch("nornir_shared.reflection.get_module_class", return_value=stage_func):
            with mock.patch.object(pm.PipelineManager, "_SaveNodes"):
                with mock.patch("nornir_buildmanager.pipelinemanager.publish_run_event"):
                    with mock.patch(
                            "nornir_buildmanager.pipelinemanager.prettyoutput.CurseString") as curse:
                        manager.ProcessPythonCall(arg_set, volume, pipeline_node)

        curse.assert_not_called()

    def test_nested_slow_stage_emits_completed_status(self) -> None:
        manager, pipeline_node, volume, arg_set = self._make_python_call_fixture()
        manager._iterate_depth = 1

        def slow_stage(**_kwargs):
            return None

        stage_func = mock.Mock(side_effect=slow_stage)

        with mock.patch("nornir_shared.reflection.get_module_class", return_value=stage_func):
            with mock.patch.object(pm.PipelineManager, "_SaveNodes"):
                with mock.patch("nornir_buildmanager.pipelinemanager.publish_run_event"):
                    with mock.patch(
                            "nornir_buildmanager.pipelinemanager.prettyoutput.CurseString") as curse:
                        with mock.patch(
                                "nornir_buildmanager.pipelinemanager.time.perf_counter",
                                side_effect=[0.0, pm._NESTED_STAGE_STATUS_MIN_SEC + 0.1]):
                            manager.ProcessPythonCall(arg_set, volume, pipeline_node)

        texts = [c.args[1] for c in curse.call_args_list]
        self.assertEqual(len(texts), 1)
        self.assertTrue(texts[0].endswith("AssembleTransform completed"))


if __name__ == "__main__":
    unittest.main()
