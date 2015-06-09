'''
  
*Note*: Certain arguments support regular expressions.  See the python :py:mod:`re` module for instructions on how to construct appropriate regular expressions.

.. argparse::
   :module: nornir_buildmanager.build
   :func: BuildParserRoot
   :prog: nornir_build volumepath

'''

import sys
import os

import time
import argparse
import nornir_shared.prettyoutput as prettyoutput
from nornir_shared.misc import SetupLogging, lowpriority
from nornir_shared.tasktimer import TaskTimer
import logging
from nornir_buildmanager import *
from nornir_imageregistration.files import *

from pkg_resources import resource_filename

CommandParserDict = {}
 
def ConfigDataPath():
    return resource_filename(__name__, 'config')


def AddVolumeArgumentToParser(parser):
    parser.add_argument('volumepath',
                        action='store',
                        type=str,
                        help='The path to the volume',
                        )


def _AddParserRootArguments(parser):
    
    parser.add_argument('volumepath',
                        action='store',
                        type=str,
                        help='Directory containing volume to execute command on',
                        )
    
    parser.add_argument('-debug',
                        action='store_true',
                        required=False,
                        default=False,
                        help='If true any exceptions raised by pipelines are not handled.',
                        dest='debug')

    parser.add_argument('-lowpriority', '-lp',
                        action='store_true',
                        required=False,
                        default=False,
                        help='Run the build with lower priority.  The machine may be more responsive at the expense of much slower builds. 3x-5x slower in tests.',
                        dest='lowpriority')

    parser.add_argument('-verbose',
                        action='store_true',
                        required=False,
                        default=False,
                        help='Provide additional output',
                        dest='verbose')

def _GetPipelineXMLPath():
    return os.path.join(ConfigDataPath(), 'Pipelines.xml')

def BuildParserRoot():

    # conflict_handler = 'resolve' replaces old arguments with new if both use the same option flag
    parser = argparse.ArgumentParser('Buildscript', conflict_handler='resolve', description='Options available to all build commands.  Specific pipelines may extend the argument list.')
    _AddParserRootArguments(parser)

    #subparsers = parser.add_subparsers(title='help')
    #help_parser = subparsers.add_parser('help', help='Print help information')
# 
    #help_parser.set_defaults(func=print_help, parser=parser)
    #help_parser.add_argument('pipelinename',
                        #default=None,
                        #nargs='?',
                        #type=str,
                        #help='Print help for a pipeline, or all pipelines if unspecified')

    #CommandParserDict['help'] = help_parser

    #update_parser = subparsers.add_parser('update', help='If directories have been copied directly into the volume this flag is required to detect them')
    
    pipeline_subparsers = parser.add_subparsers(title='Commands')
    _AddPipelineParsers(pipeline_subparsers)
    
    return parser


def _AddPipelineParsers(subparsers):

    PipelineXML = _GetPipelineXMLPath()
    for pipeline_name in pipelinemanager.PipelineManager.ListPipelines(PipelineXML):
        pipeline = pipelinemanager.PipelineManager.Load(PipelineXML, pipeline_name)

        pipeline_parser = subparsers.add_parser(pipeline_name, help=pipeline.Description, epilog=pipeline.Epilog)

        pipeline.GetArgParser(pipeline_parser, IncludeGlobals=True)

        pipeline_parser.set_defaults(func=call_pipeline, PipelineXmlFile=_GetPipelineXMLPath(), PipelineName=pipeline_name)

        CommandParserDict[pipeline_name] = pipeline_parser


def print_help(args):

    if args.pipelinename is None:
        args.parser.print_help() 
    elif args.pipelinename in CommandParserDict:
        parser = CommandParserDict[args.pipelinename]
        parser.print_help()
    else:
        args.parser.print_help()

def call_update(args):
    volumeObj = VolumeManagerETree.load(args.volumepath)
    volumeObj.UpdateSubElements()

def call_pipeline(args):
    pipelinemanager.PipelineManager.RunPipeline(PipelineXmlFile=args.PipelineXmlFile, PipelineName=args.PipelineName, args=args)
    
def _GetFromNamespace(ns, attribname, default=None):
    if attribname in ns:
        return getattr(ns,attribname)
    else:
        return default

def InitLogging(buildArgs):

#    nornir_shared.Misc.RunWithProfiler('Execute()', "C:/Temp/profile.pr")

    parser = BuildParserRoot()

    (args, extraargs) = parser.parse_known_args(buildArgs)

    if 'volumepath' in args:
        if _GetFromNamespace(args, 'debug', False):
            SetupLogging(args.volumepath, Level=logging.DEBUG)
        else:
            SetupLogging(Level=logging.WARN)
    else:
        SetupLogging(Level=logging.WARN)

def Execute(buildArgs=None):

    if buildArgs is None:
        buildArgs = sys.argv[1:]

    InitLogging(buildArgs)

    Timer = TaskTimer()

    parser = BuildParserRoot()

    args = parser.parse_args(buildArgs)
   
    if args.lowpriority:
        
        lowpriority()
        print("Warning, using low priority flag.  This can make builds much slower")
        
    # SetupLogging(args.volumepath)
    
    try:  
        Timer.Start(args.PipelineName)

        args.func(args)

        Timer.End(args.PipelineName)
        
  
    finally:
        OutStr = str(Timer)
        prettyoutput.Log(OutStr)
        timeTextFullPath = os.path.join(args.volumepath, 'Timing.txt') 
        try:
            with open(timeTextFullPath, 'w+') as OutputFile:
                OutputFile.writelines(OutStr)
                OutputFile.close()
        except:
            prettyoutput.Log('Could not write %s' % (timeTextFullPath))

if __name__ == '__main__':
    Execute()

