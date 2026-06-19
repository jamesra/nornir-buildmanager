'''
Created on Jan 6, 2014

@author: u0490822
'''
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as etree

import nornir_buildmanager.argparsexml as argparsexml
import nornir_buildmanager.pipelinemanager as pm
import nornir_shared.tasktimer

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


if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
