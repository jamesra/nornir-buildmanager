'''

--------------------
Command line usage
--------------------

Note: Certain arguments support regular expressions.  See the python :py:mod:`re` module for instructions on how to construct appropriate regular expressions.

.. argparse:: 
   :module: nornir_buildmanager.build
   :func: BuildParserRoot
   :prog: nornir_build

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

#     try:
#         path = os.path.dirname(__file__)
#     except:
#         path = os.getcwd()
#
#     return os.path.join(path, 'con fig')


# def _BuildPipelineParser(parser):

#     parser.add_argument('-update',
#                         action='store_true',
#                         required=False,
#                         default=False,
#                         help='If directories have been copied directly into the volume this flag is required to detect them',
#                         dest='update'
#                         )
#
#     parser.add_argument('-pipeline',
#                         action='store',
#                         required=False,
#                         default=None,
#                         nargs='+',
#                         type=str,
#                         help='The names of the pipeline to use',
#                         dest='pipelinenames'
#                         )

def _BuildImportParser(parser):
    parser.add_argument('inputpath',
                        metavar='import',
                        action='store',
                        type=str,
                        default=None,
                        help='The path of data to import, if any',
                        )

    parser.set_defaults(func=call_importer)


def AddVolumeArgumentToParser(parser):
    parser.add_argument('-volume',
                        action='store',
                        required=True,
                        default=None,
                        type=str,
                        help='The path to the volume',
                        dest='volumepath'
                        )


def _AddParserRootArguments(parser):

    parser.add_argument('-debug',
                        action='store_true',
                        required=False,
                        default=False,
                        help='If true any exceptions raised by pipelines are not handled.',
                        dest='debug')


    parser.add_argument('-normalpriority', '-np',
                        action='store_true',
                        required=False,
                        default=False,
                        help='Run the build without trying to lower the priority.  Faster builds but the machine may be less responsive.',
                        dest='normpriority')

    parser.add_argument('-verbose',
                        action='store_true',
                        required=False,
                        default=False,
                        help='Provide additional output',
                        dest='verbose')


#     parser.add_argument('-pipelinexml',
#                         action='store',
#                         required=False,
#                         default='Pipelines.xml',
#                         type=str,
#                         help='The path to the xml file containing the pipeline XML file',
#                         dest='pipelinexmlpath'
#                         )
#
#     parser.add_argument('-importmapxml',
#                         action='store',
#                         required=False,
#                         type=str,
#                         default='Importers.xml',
#                         help='The importer XML file to use',
#                         dest='importmapxmlpath'
#                         )

def _GetPipelineXMLPath():
    return os.path.join(ConfigDataPath(), 'Pipelines.xml')

def _GetImporterXMLPath():
    return os.path.join(ConfigDataPath(), 'Importers.xml')

def BuildParserRoot():

    # conflict_handler = 'resolve' replaces old arguments with new if both use the same option flag
    parser = argparse.ArgumentParser('Buildscript', conflict_handler='resolve', description='Options available to all build commands.  Specific pipelines may extend the argument list.')



    subparsers = parser.add_subparsers(title='help')
    help_parser = subparsers.add_parser('help', help='Print help information')

    help_parser.set_defaults(func=print_help, parser=parser)
    help_parser.add_argument('pipelinename',
                        default=None,
                        nargs='?',
                        type=str,
                        help='Print help for a pipeline, or all pipelines if unspecified')

    CommandParserDict['help'] = help_parser

    import_parser = subparsers.add_parser('import', help='Import new data into a volume')
    AddVolumeArgumentToParser(import_parser)
    _BuildImportParser(import_parser)

    CommandParserDict['import'] = import_parser

    update_parser = subparsers.add_parser('update', help='If directories have been copied directly into the volume this flag is required to detect them')
    AddVolumeArgumentToParser(update_parser)

    _AddPipelineParsers(subparsers)

    # pipeline_parser = subparsers.add_parser('pipeline', dest='pipeline')
    # _BuildPipelineParser(pipeline_parser)

    _AddParserRootArguments(parser)

    # parser.add_argument('args', nargs=argparse.REMAINDER)
    return parser


def _AddPipelineParsers(subparsers):

    PipelineXML = _GetPipelineXMLPath()
    for pipeline_name in pipelinemanager.PipelineManager.ListPipelines(PipelineXML):
        pipeline = pipelinemanager.PipelineManager.Load(PipelineXML, pipeline_name)

        pipeline_parser = subparsers.add_parser(pipeline_name, help=pipeline.Description, description=pipeline.Description, epilog=pipeline.Epilog)

        AddVolumeArgumentToParser(pipeline_parser)

        pipeline.GetArgParser(pipeline_parser, IncludeGlobals=True)

        pipeline_parser.set_defaults(func=call_pipeline, PipelineXmlFile=_GetPipelineXMLPath(), PipelineName=pipeline_name)

        CommandParserDict[pipeline_name] = pipeline_parser


def print_help(args):

    if args.pipelinename is None:
        args.parser.print_help()
        # pipelinemanager.PipelineManager.PrintPipelineEnumeration(_GetPipelineXMLPath())
    elif args.pipelinename in CommandParserDict:
        parser = CommandParserDict[args.pipelinename]
        parser.print_help()
    else:
        args.parser.print_help()

def call_update(args):
    volumeObj = VolumeManagerETree.load(args.volumepath)
    volumeObj.UpdateSubElements()

def call_importer(args):

    ImporterXMLPath = _GetImporterXMLPath()
    print "Loading import map: " + ImporterXMLPath
    Importer = ImportManager.ImportManager.Load(ImporterXMLPath)
    if(Importer is None):
        prettyoutput.LogErr("Specified Importer.xml not found: " + args.importmapxmlpath)
        sys.exit()

    Importer.ConvertAll(args.inputpath, args.volumepath)

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

    # print "Current Working Directory: " + os.getcwd()

    vikingURL = ""

    dargs = dict()
    # dargs.update(args.__dict__)

    TimingOutput = 'Timing.txt'

    Timer = TaskTimer()

    parser = BuildParserRoot()

    args = parser.parse_args(buildArgs)

    PipelineXML = _GetPipelineXMLPath()

    if not args.normpriority:
        lowpriority()

    # SetupLogging(args.volumepath)

    try:
        Timer.Start('Total Runtime')

#        Timer.Start('ADoc To Mosaic')

#         Importer = None
#         if not args.inputpath is None:
#             ImporterXMLPath = os.path.join(ConfigDataPath(), args.importmapxmlpath)
#             print "Loading import map: " + ImporterXMLPath
#             Importer = ImportManager.ImportManager.Load(ImporterXMLPath)
#             if(Importer is None):
#
#                 prettyoutput.LogErr("Specified Importer.xml not found: " + args.importmapxmlpath)
#                 sys.exit()
#
#             Importer.ConvertAll(args.inputpath, args.volumepath)

        args.func(args)

#         if args.pipelinenames is None:
#             return
#
#         for pipelinename in args.pipelinenames:
#
#             pipeline = pipelinemanager.PipelineManager.Load(PipelineXMLPath, pipelinename)
#             pipeline.Execute(parser, buildArgs)



    finally:
        OutStr = str(Timer)
        prettyoutput.Log(OutStr)
        try:
            OutputFile = open(os.path.join(args.volumepath, 'Timing.txt'), 'w')
            OutputFile.writelines(OutStr)
            OutputFile.close()
        except:
            prettyoutput.Log('Could not write timing.txt')

#
# def SetupLogging(OutputPath):
#
#    LogPath = os.path.join(OutputPath, 'Logs')
#
#    if not os.path.exists(LogPath):
#        os.makedirs(LogPath)
#
#    formatter = logging.Formatter('%(levelname)s - %(name)s - %(message)s')
#
#    logFileName = time.strftime('log-%M.%d.%y_%H.%M.txt', time.localtime())
#    logFileName = os.path.join(LogPath, logFileName)
#    errlogFileName = time.strftime('log-%M.%d.%y_%H.%M-Errors.txt', time.localtime())
#    errlogFileName = os.path.join(LogPath, errlogFileName)
#    logging.basicConfig(filename=logFileName, level='DEBUG', format='%(levelname)s - %(name)s - %(message)s')
#
#    eh = logging.FileHandler(errlogFileName)
#    eh.setLevel(logging.INFO)
#    eh.setFormatter(formatter)
#
#    ch = logging.StreamHandler()
#    ch.setLevel(logging.WARNING)
#    ch.setFormatter(formatter)
#
#    logger = logging.getLogger()
#    logger.addHandler(eh)
#    logger.addHandler(ch)
#
#    eh.setFormatter(formatter)



if __name__ == '__main__':
    Execute()

