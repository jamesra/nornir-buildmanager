import tempfile
import unittest
import xml.etree.ElementTree as etree
from pathlib import Path
from typing import Any, cast
from unittest import mock

from nornir_buildmanager import XPath
from nornir_buildmanager import argparsexml
from nornir_buildmanager import pipelinemanager
from nornir_buildmanager.importers import LoadHistogramCutoffs
from nornir_buildmanager.volumemanager.histogrambasenode import HistogramBase
from nornir_buildmanager.volumemanager.mosaicbasenode import MosaicBaseNode
from nornir_buildmanager.volumemanager.transformnode import TransformNode
from nornir_buildmanager.volumemanager.validation import ValidateAttributesAreStrings
from nornir_buildmanager.volumemanager.xelementwrapper import XElementWrapper


class _DummyElement:
    def __init__(self, attrib):
        self.attrib = attrib


class _FakeIterChild:
    def __init__(self):
        self.Parent = None
        self.NeedsValidation = True
        self.clean_calls = 0

    def CleanIfInvalid(self):
        self.clean_calls += 1
        return True, "Invalid child"


class _FakeIterRoot:
    def __init__(self, child):
        self._child = child
        self.NeedsValidation = False

    def findall(self, _xpath):
        return [self._child]


class AuditRegressionTests(unittest.TestCase):
    def _create_pipeline_manager(self):
        root = etree.Element("Pipelines")
        pipeline = etree.Element("Pipeline", Name="UnitTestPipeline")
        return pipelinemanager.PipelineManager(etree.ElementTree(root), pipeline)

    def test_validate_attributes_keeps_keys_and_stringifies_values(self):
        element = _DummyElement({"Threshold": 123, "Gamma": None, "Name": "ok"})
        ValidateAttributesAreStrings(element)

        self.assertEqual(set(element.attrib.keys()), {"Threshold", "Gamma", "Name"})
        self.assertEqual(element.attrib["Threshold"], "123")
        self.assertEqual(element.attrib["Gamma"], "")
        self.assertEqual(element.attrib["Name"], "ok")

    def test_transform_needsvalidation_is_boolean(self):
        node = TransformNode.Create(Name="T", Type="Grid", Path="t.mosaic")
        node._validity_checked = True
        self.assertIs(node.NeedsValidation, False)

        node._validity_checked = None
        with mock.patch.object(MosaicBaseNode, "NeedsValidation", new_callable=mock.PropertyMock, return_value=False):
            with mock.patch(
                "nornir_buildmanager.volumemanager.transformnode.InputTransformHandler.InputTransformNeedsValidation",
                return_value=(False, "No input transform to validate"),
            ):
                self.assertIsInstance(node.NeedsValidation, bool)
                self.assertFalse(node.NeedsValidation)

    def test_histogram_needsvalidation_uses_boolean_from_tuple(self):
        node = HistogramBase(tag="Histogram", attrib={})
        fake_data_node = mock.Mock()
        fake_data_node.NeedsValidation = False
        with mock.patch.object(
            HistogramBase,
            "InputTransformNeedsValidation",
            return_value=(False, "No input transform to validate"),
        ):
            with mock.patch.object(HistogramBase, "DataNode", new_callable=mock.PropertyMock, return_value=fake_data_node):
                self.assertFalse(node.NeedsValidation)

    def test_iterate_node_validates_each_child_not_parent(self):
        manager = self._create_pipeline_manager()
        execute_calls = {"count": 0}

        def _execute_child(_arg_set, _child, _pipeline_node):
            execute_calls["count"] += 1
            return 1

        manager.ExecuteChildPipelines = _execute_child  # type: ignore[method-assign]

        child = _FakeIterChild()
        root = _FakeIterRoot(child)
        iterate_node = etree.Element("Iterate", XPath="Channel")
        arg_set = pipelinemanager.ArgumentSet()

        with self.assertRaises(pipelinemanager.PipelineSearchFailed):
            manager.ProcessIterateNode(arg_set, cast(Any, root), iterate_node)

        self.assertEqual(child.clean_calls, 1)
        self.assertEqual(execute_calls["count"], 0)

    def test_python_call_raises_pipeline_error_when_stage_throws(self):
        manager = self._create_pipeline_manager()
        manager.VolumeTree = XElementWrapper(tag="Volume", attrib={"Path": "/tmp/volume"})
        arg_set = pipelinemanager.ArgumentSet()
        arg_set.AddArguments({"debug": False, "verbose": False})
        pipeline_node = etree.Element("PythonCall", Module="module.path", Function="FunctionName")
        volume_elem = XElementWrapper(tag="Volume", attrib={})

        def _raise_stage(**_kwargs):
            raise RuntimeError("Synthetic stage failure")

        with mock.patch(
            "nornir_buildmanager.pipelinemanager.nornir_shared.reflection.get_module_class",
            return_value=_raise_stage,
        ):
            with self.assertRaises(pipelinemanager.PipelineError):
                manager.ProcessPythonCall(arg_set, volume_elem, pipeline_node)

    def test_xpath_iterator_preserves_quoted_value(self):
        parts = list(XPath.XPathIterator("/Channel[@Name='TEM']"))
        self.assertEqual(parts[0].Value, "TEM")

    def test_histogram_cutoff_loader_handles_missing_gamma_column(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = Path(tmpdir) / "ContrastOverrides.txt"
            filename.write_text("#Section Min Max Gamma\n1 10 200\n2 11 201 0.75\n", encoding="utf-8")
            loaded = LoadHistogramCutoffs(str(filename))

            self.assertEqual(loaded[1].Gamma, 1.0)
            self.assertEqual(loaded[2].Gamma, 0.75)

    def test_argparsexml_supports_builtin_type_names(self):
        argument_xml = etree.XML(
            '<Arguments><Argument flag="-Value" dest="Value" type="int" required="True"/></Arguments>'
        )
        parser = argparsexml.CreateOrExtendParserForArguments(argument_xml.findall("Argument"))
        args = parser.parse_args(["-Value", "42"])
        self.assertEqual(args.Value, 42)

    def test_argparsexml_does_not_double_escape_existing_percent_pairs(self):
        help_text = "already escaped percent 99%% and placeholder %(default)s"
        escaped = argparsexml._EscapeLiteralPercentInHelp(help_text)
        self.assertEqual(escaped, help_text)


if __name__ == "__main__":
    unittest.main()
