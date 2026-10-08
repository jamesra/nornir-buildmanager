'''
Created on Jan 6, 2014

@author: u0490822
'''
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as etree
from unittest import mock

from hypothesis import given, settings, strategies as st

import nornir_buildmanager.argparsexml as argparsexml
import nornir_buildmanager.pipelinemanager as pm
import nornir_shared.tasktimer
from nornir_buildmanager.volumemanager.blocknode import BlockNode
from nornir_buildmanager.volumemanager.sectionnode import SectionNode
from nornir_buildmanager.volumemanager.volumenode import VolumeNode

ArgumentXML = '<Arguments> \
                 <Argument flag="-Gamma" dest="Gamma" help="Gamma value for intensity auto-level" required="False"/> \
                 <Argument flag="-MinCutoff" dest="MinCutoff" default="0.1" help="Min pixel intensity cutoff as a percentage, 0 to 100" required="False"/> \
                 <Argument flag="-MaxCutoff" dest="MaxCutoff" default="0.5" help="Max pixel intensity cutoff as a percentage, 0 to 100. Specifying 1 puts the cutoff at 99% of the maximum pixel intensity value." required="False"/> \
               </Arguments>'

ParamsXML = '<Root><Parameters> \
                <Entry Name="Gamma" Value="#Gamma"/> \
                <Entry Name="MinCutoff" Value="#MinCutoff"/> \
                <Entry Name="MaxCutoff" Value="#MaxCutoff"/> \
            </Parameters></Root>'

PipelineXML = '''<Iterate VariableName="ChannelNode" XPath="Block/Section/Channel">
                 <Select VariableName="TransformNode" XPath="Transform[@Name='Prune']"/>
                 <Select VariableName="LevelNode" Root="ChannelNode" XPath="Filter[@Name='#InputFilter']/TilePyramid/Level[@Downsample='1']"/>
                 <PythonCall Function="tile.AutolevelTiles" OutputFilterName="Leveled">
                    <Parameters>
                        <Entry Name="Gamma" Value="#Gamma"/>
                        <Entry Name="MinCutoff" Value="#MinCutoff"/> 
                        <Entry Name="MaxCutoff" Value="#MaxCutoff"/>
                    </Parameters>
                </PythonCall>
                <Select VariableName="PyramidNode" Root="ChannelNode"  XPath="Filter[@Name='Leveled']/TilePyramid"/>
                <PythonCall Function="tile.BuildTilePyramids"/>
              </Iterate>'''

PipelineNode = '<Iterate VariableName="ChannelNode" XPath="Block/Section/Channel"/>'


def CreateVariableNameNode(value):
    pipelineNode = etree.Element()
    pipelineNode.attrib['VariableName'] = value
    return pipelineNode


def CreateParametersNode(**kwargs):
    arguments = etree.Element()

    for k, v in list(kwargs.items()):
        argNode = etree.Element()
        argNode.attrib['Name'] = k
        argNode.attrib['Value'] = v

        arguments.append(argNode)

    return arguments


def CreatePipelineNode(**kwargs):
    node = etree.Element()

    for k, v in list(kwargs.items()):
        node.attrib[k] = v

    return node


def LoadParams(xml):
    return etree.XML(xml)


def LoadArguments(xml):
    return etree.XML(xml)


def LoadPipeline(xml):
    return etree.XML(xml)


class TestStageTimings(unittest.TestCase):
    """Verify stage timing export writes structured JSON."""

    def test_write_stage_timings_appends_entry(self):
        """_WriteStageTimings should append a JSON record with stage durations."""
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = pm.PipelineManager(pipelinesRoot=etree.Element('Root'), pipelineData=etree.Element('Pipeline'))
            manager._VolumePath = temp_dir
            manager._PipelineName = 'TestPipeline'
            manager._StageTimer = nornir_shared.tasktimer.TaskTimer()
            manager._StageTimer.Start('stage.one')
            manager._StageTimer.End('stage.one', print_elapsed=False)

            manager._WriteStageTimings()

            output_path = os.path.join(temp_dir, 'StageTimings.json')
            self.assertTrue(os.path.exists(output_path))
            with open(output_path, 'r', encoding='utf-8') as input_file:
                records = json.load(input_file)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['pipeline'], 'TestPipeline')
            self.assertEqual(records[0]['stages'][0]['stage'], 'stage.one')

    def test_write_stage_timings_survives_truncated_json(self):
        """Corrupt StageTimings.json must not raise out of _WriteStageTimings (#139)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = pm.PipelineManager(pipelinesRoot=etree.Element('Root'), pipelineData=etree.Element('Pipeline'))
            manager._VolumePath = temp_dir
            manager._PipelineName = 'TestPipeline'
            manager._StageTimer = nornir_shared.tasktimer.TaskTimer()
            manager._StageTimer.Start('stage.one')
            manager._StageTimer.End('stage.one', print_elapsed=False)

            output_path = os.path.join(temp_dir, 'StageTimings.json')
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write('[{"pipeline":')

            manager._WriteStageTimings()

            with open(output_path, 'r', encoding='utf-8') as input_file:
                records = json.load(input_file)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['pipeline'], 'TestPipeline')

    def test_stage_volume_label_for_mapping_node(self):
        """Mapping nodes lack FullPath; stage keys should use a descriptive label."""
        from nornir_buildmanager.volumemanager.mappingnode import MappingNode

        mapping = MappingNode.Create(693, [692, 694])
        label = pm.PipelineManager._StageVolumeLabel(mapping)
        self.assertNotIn('FullPath', label)
        self.assertIn('693', label)


class Test(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_Arg(self):
        argset = pm.ArgumentSet()

        parser = argparsexml.CreateOrExtendParserForArguments(LoadArguments(ArgumentXML).findall('Argument'))

        args = parser.parse_args(['-Gamma', '1.0', '-MinCutoff', '0.1', '-MaxCutoff', '0.5'])
        argset.AddArguments(args)
        argset.AddParameters(LoadParams(ParamsXML))

        self.assertTrue('Gamma' in argset.Parameters)
        self.assertTrue('MinCutoff' in argset.Parameters)
        self.assertTrue('MaxCutoff' in argset.Parameters)

        argset.AddVariable(pm._GetVariableName(LoadPipeline(PipelineNode)), "Test Volume Element")
        self.assertTrue('ChannelNode' in argset.Variables)

        kwargs = argset.KeyWordArgs()
        print((repr(kwargs)))


class TestPipelineProgressLogging(unittest.TestCase):
    """Per-iterate pipeline dumps stay off unless verbose or the env flag is set."""

    def setUp(self) -> None:
        self._old = os.environ.pop(pm._PIPELINE_PROGRESS_ENV, None)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop(pm._PIPELINE_PROGRESS_ENV, None)
        else:
            os.environ[pm._PIPELINE_PROGRESS_ENV] = self._old

    def test_off_by_default(self) -> None:
        argset = pm.ArgumentSet()
        argset.AddArguments({"verbose": False, "debug": True})
        self.assertFalse(pm._should_log_pipeline_progress(argset))

    def test_verbose_flag_enables(self) -> None:
        argset = pm.ArgumentSet()
        argset.AddArguments({"verbose": True})
        self.assertTrue(pm._should_log_pipeline_progress(argset))

    def test_env_flag_enables(self) -> None:
        os.environ[pm._PIPELINE_PROGRESS_ENV] = "1"
        argset = pm.ArgumentSet()
        argset.AddArguments({"verbose": False})
        self.assertTrue(pm._should_log_pipeline_progress(argset))


class TestEmptyVolumeFailFast(unittest.TestCase):
    """Non-import pipelines must exit when Create=True yields a volume with no Blocks."""

    def test_execute_exits_when_created_volume_has_no_blocks(self) -> None:
        """Fail-fast path counts Blocks without materializing the full findall list."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline = etree.Element("Pipeline", Name="AlignSections")
            manager = pm.PipelineManager(etree.Element("Root"), pipeline)
            args = mock.Mock(
                volumepath=temp_dir, outputpath=temp_dir, debug=False, PipelineName=None,
            )
            with mock.patch("nornir_buildmanager.pipelinemanager.prettyoutput.Log"), \
                    mock.patch("nornir_buildmanager.pipelinemanager.prettyoutput.LogErr"):
                with self.assertRaises(SystemExit) as ctx:
                    manager.Execute(args)
                self.assertEqual(ctx.exception.code, 2)

    def test_non_import_pipeline_runs_when_volume_has_blocks(self) -> None:
        """Under-counting Blocks must not trigger fail-fast when VolumeData.xml existed."""
        volume = VolumeNode.Create(Name="V", Path="/tmp/v")
        volume.append(BlockNode.Create(Name="B0"))
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline = etree.Element("Pipeline", Name="AlignSections")
            manager = pm.PipelineManager(etree.Element("Root"), pipeline)
            args = mock.Mock(
                volumepath=temp_dir, outputpath=temp_dir, debug=False, PipelineName=None,
            )
            with mock.patch("nornir_buildmanager.pipelinemanager.os.path.exists", return_value=True), \
                    mock.patch(
                        "nornir_buildmanager.pipelinemanager.VolumeManager.Load",
                        return_value=volume,
                    ), \
                    mock.patch("nornir_buildmanager.pipelinemanager.prettyoutput.Log"), \
                    mock.patch.object(manager, "ExecuteChildPipelines", return_value=None):
                result = manager.Execute(args)
                self.assertIs(result, volume)

    def test_import_pipeline_skips_empty_volume_fail_fast(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline = etree.Element("Pipeline", Name="ImportVolume")
            manager = pm.PipelineManager(etree.Element("Root"), pipeline)
            args = mock.Mock(
                volumepath=temp_dir, outputpath=temp_dir, debug=False, PipelineName=None,
            )
            with mock.patch("nornir_buildmanager.pipelinemanager.prettyoutput.Log"), \
                    mock.patch.object(manager, "ExecuteChildPipelines", return_value=None):
                volume = manager.Execute(args)
                self.assertIsNotNone(volume)

    @given(st.integers(min_value=0, max_value=12))
    @settings(max_examples=25)
    def test_block_count_equivalent_to_list_materialization(self, n_blocks: int) -> None:
        volume = VolumeNode.Create(Name="V", Path="/tmp/v")
        for i in range(n_blocks):
            volume.append(BlockNode.Create(Name=f"B{i}"))
        listed = len(list(volume.findall("Block")))
        counted = sum(1 for _ in volume.findall("Block"))
        self.assertEqual(listed, counted)
        self.assertEqual(counted, n_blocks)


class TestProcessPythonCallElementArgs(unittest.TestCase):
    """ProcessPythonCall hands the PythonCall element's attributes and Parameters to the stage."""

    def test_stage_receives_element_attributes_and_parameters_then_clears_them(self) -> None:
        manager = pm.PipelineManager(pipelinesRoot=etree.Element("Root"), pipelineData=etree.Element("Pipeline"))
        manager.VolumeTree = mock.Mock()
        argset = pm.ArgumentSet()
        argset.AddArguments({"verbose": False, "debug": False, "Gamma": 2.5})
        node = etree.fromstring(
            '<PythonCall Module="m" Function="f" OutputFilterName="Leveled" Count="3" Ratio="0.5" Alias="#Gamma">'
            '<Parameters><Entry Name="MinCutoff" Value="7"/><Entry Name="Gamma" Value="#Gamma"/></Parameters>'
            '</PythonCall>')
        captured: dict = {}

        def stage(**kwargs):
            captured.update(kwargs)
            captured["Parameters"] = dict(kwargs["Parameters"])

        with mock.patch("nornir_shared.reflection.get_module_class", return_value=stage), \
                mock.patch.object(pm.PipelineManager, "_SaveNodes"), \
                mock.patch("nornir_buildmanager.pipelinemanager.publish_run_event"), \
                mock.patch("nornir_buildmanager.pipelinemanager.prettyoutput.CurseString"):
            manager.ProcessPythonCall(argset, SectionNode.Create(Number=1), node)

        self.assertEqual(captured["OutputFilterName"], "Leveled")
        self.assertEqual(captured["Count"], 3)
        self.assertEqual(captured["Ratio"], 0.5)
        self.assertEqual(captured["Alias"], 2.5)
        self.assertEqual(captured["Parameters"], {"MinCutoff": 7, "Gamma": 2.5})
        self.assertEqual(argset.Attribs, {})
        self.assertEqual(argset.Parameters, {})


if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
