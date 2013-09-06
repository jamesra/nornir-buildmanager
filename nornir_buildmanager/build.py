#! /Library/Frameworks/Python.framework/Versions/Current/bin python

import sys
import os

import time
import argparse
import LowPriority
import nornir_shared.prettyoutput as prettyoutput
from nornir_shared.tasktimer import TaskTimer
import logging
from nornir_buildmanager import *
from nornir_imageregistration.io import *


def ConfigDataPath():

    try:
        path = os.path.dirname(__file__)
    except:
        path = os.getcwd()

    return os.path.join(path, 'config')


def ProcessArgs():

    # conflict_handler = 'resolve' replaces old arguments with new if both use the same option flag
    parser = argparse.ArgumentParser('Buildscript', conflict_handler='resolve')

    parser.add_argument('-input',
                        action='store',
                        required=False,
                        type=str,
                        default=None,
                        help='The path to the data to import',
                        dest='inputpath'
                        )

    parser.add_argument('-volume',
                        action='store',
                        required=True,
                        default=None,
                        type=str,
                        help='The path to the data to export',
                        dest='volumepath'
                        )

    parser.add_argument('-update',
                        action='store_true',
                        required=False,
                        default=False,
                        help='If directories have been copied directly into the volume this flag is required to detect them',
                        dest='update'
                        )

    parser.add_argument('-importmapxml',
                        action='store',
                        required=False,
                        type=str,
                        default='Importers.xml',
                        help='The importer XML file to use',
                        dest='importmapxmlpath'
                        )

    parser.add_argument('-pipelinexml',
                        action='store',
                        required=False,
                        default='Pipelines.xml',
                        type=str,
                        help='The path to the xml file containing the pipeline XML file',
                        dest='pipelinexmlpath'
                        )

    parser.add_argument('-pipeline',
                        action='store',
                        required=False,
                        default=None,
                        nargs='+',
                        type=str,
                        help='The names of the pipeline to use',
                        dest='pipelinenames'
                        )

    parser.add_argument('-debug',
                        action='store_true',
                        required=False,
                        default=False,
                        help='For debugging purposes exceptions are not handled',
                        dest='debug')

    parser.add_argument('-verbose',
                        action='store_true',
                        required=False,
                        default=False,
                        help='Provide additional output for debugging purposes.',
                        dest='verbose')

    # parser.add_argument('args', nargs=argparse.REMAINDER)
    return parser

def Execute(buildArgs=None):

    if buildArgs is None:
        buildArgs = sys.argv

    vikingURL = ""

    dargs = dict()
    # dargs.update(args.__dict__)

    TimingOutput = 'Timing.txt'

    LowPriority.lowpriority()

    Timer = TaskTimer()

    parser = ProcessArgs()

    (args, extraargs) = parser.parse_known_args(buildArgs)

    # SetupLogging(args.volumepath)

    try:
        Timer.Start('Total Runtime')

        Timer.Start('ADoc To Mosaic')

        Importer = None
        if not args.inputpath is None:
            ImporterXMLPath = os.path.join(ConfigDataPath(), args.importmapxmlpath)
            print "Loading import map: " + ImporterXMLPath
            Importer = ImportManager.ImportManager.Load(ImporterXMLPath)
            if(Importer is None):

                prettyoutput.LogErr("Specified Importer.xml not found: " + args.importmapxmlpath)
                sys.exit()

            Importer.ConvertAll(args.inputpath, args.volumepath)

        if args.update:
            volumeObj = VolumeManagerETree.load(args.volumepath)
            volumeObj.UpdateSubElements()

        if args.pipelinenames is None:
            return

        for pipelinename in args.pipelinenames:
            PipelineXMLPath = os.path.join(ConfigDataPath(), args.pipelinexmlpath)
            pipeline = pipelinemanager.PipelineManager.Load(PipelineXMLPath, pipelinename)
            pipeline.Execute(parser, buildArgs)

            print "Current Working Directory: " + os.getcwd()

    finally:
        OutStr = str(Timer)
        prettyoutput.Log(OutStr)
        try:
            OutputFile = open(os.path.join(args.volumepath, 'Timing.txt'), 'w')
            OutputFile.writelines(OutStr)
            OutputFile.close()
        except:
            prettyoutput.Log('Could not write timing.txt')
            pass


def SetupLogging(OutputPath):

    LogPath = os.path.join(OutputPath, 'Logs')

    if not os.path.exists(LogPath):
        os.makedirs(LogPath)

    formatter = logging.Formatter('%(levelname)s - %(name)s - %(message)s')

    logFileName = time.strftime('log-%M.%d.%y_%H.%M.txt', time.localtime())
    logFileName = os.path.join(LogPath, logFileName)
    errlogFileName = time.strftime('log-%M.%d.%y_%H.%M-Errors.txt', time.localtime())
    errlogFileName = os.path.join(LogPath, errlogFileName)
    logging.basicConfig(filename=logFileName, level='DEBUG', format='%(levelname)s - %(name)s - %(message)s')

    eh = logging.FileHandler(errlogFileName)
    eh.setLevel(logging.INFO)
    eh.setFormatter(formatter)

    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(formatter)

    logger = logging.getLogger()
    logger.addHandler(eh)
    logger.addHandler(ch)

    eh.setFormatter(formatter)


if __name__ == '__main__':

#    nornir_shared.Misc.RunWithProfiler('Execute()', "C:/Temp/profile.pr")

    parser = ProcessArgs()

    (args, extraargs) = parser.parse_known_args()

    SetupLogging(args.volumepath)

    Execute()


#
#    import cProfile
#    import pstats
#    parser = ProcessArgs()
#    (args, remainingargs) = parser.parse_known_args()
#    ProfilePath = os.path.join(args.outputpath, 'BuildProfile.pr')
#
#    ProfileDir = os.path.dirname(ProfilePath)
#    if not os.path.exists(ProfileDir):
#
#        os.makedirs(ProfileDir)
#
#    try:
#        cProfile.run('Execute()', ProfilePath)
#    finally:
#        if not os.path.exists(ProfilePath):
#            prettyoutput.LogErr("No profile file found" + ProfilePath)
#            sys.exit()
#
#        pr = pstats.Stats(ProfilePath)
#        if not pr is None:
#            pr.sort_stats('time')
#            print str(pr.print_stats(.05))
#
#    ir.RemoveDirectorySpaces(args.outputpath)
#
#    ##Get Unique Channel Names
#    baseChannelList = []
#    for channelList in ChannelsForSections.itervalues():
#        for channel in channelList:
#            if channel not in baseChannelList:
#                baseChannelList.append(channel)
#
#    #Ensure we do not have missing tiles in our mosaic files
#    for channel in baseChannelList:
#        ir.ValidateMosaicFile(args.outputpath, channel)
#
#    if args.evaluateStats or args.correctShading:
#        Timer.Start('Evaluate Channels')
#        for channel in baseChannelList:
#            PostProcessBlurCmdTemplate = ' -virtual-pixel edge -gaussian-blur 0x%(sigma)f '
#
#            ir.EvaluateSequence(args.outputpath, 1, channel, SourceMosaicType="supertile", EvaluateSequenceArg='min', PostEvaluateSequenceArg=PostProcessBlurCmdTemplate % {'sigma' : 2})
#            ir.EvaluateSequence(args.outputpath, 1, channel, SourceMosaicType="supertile", EvaluateSequenceArg='max', PostEvaluateSequenceArg=PostProcessBlurCmdTemplate % {'sigma' : 2})
#            ir.EvaluateSequence(args.outputpath, 1, channel, SourceMosaicType="supertile", EvaluateSequenceArg='median', PostEvaluateSequenceArg=PostProcessBlurCmdTemplate % {'sigma' : 2})
#            ir.EvaluateSequence(args.outputpath, 1, channel, SourceMosaicType="supertile", EvaluateSequenceArg='mean', PostEvaluateSequenceArg=PostProcessBlurCmdTemplate % {'sigma' : 2})
#    #
#        Timer.End('Evaluate Channels')
#
#    #Map of channel names used for registration to the original channels
#    DownstreamChannelMap = dict()
#    TopLevelChannels = list()
#
#    if args.correctShading:
#        Timer.Start('Created Corrected Channels')
#
#        for channel in baseChannelList:
#
#            ShadingChannel = 'ShadingCorrected' + channel
#            ShadingChannel = ShadingChannel.lower()
#            ir.CreateCorrectedChannel(args.outpath, SourceChannel=channel, TargetChannel=ShadingChannel, CorrectionCandidates=['median'], InvertSourceForCandidates=[False], ComposeOperator=['minus'])
#
#            TopLevelChannels.append(ShadingChannel)
#
#            if(ShadingChannel in DownstreamChannelMap.keys()):
#                DownstreamChannelMap[ShadingChannel].append(channel)
#            else:
#                DownstreamChannelMap[ShadingChannel] = [channel]
#
#        #All of the channels we just processed are 'downstream' from the corrected channel now
#        DownstreamChannels = copy.deepcopy(baseChannelList)
#
#        Timer.End('Created Corrected Channels')
#    else:
#        TopLevelChannels = copy.deepcopy(baseChannelList)
#
#    if not args.pruneCutoff is None:
#        for channel in TopLevelChannels:
#            ir.Prune(args.outputpath, args.pruneCutoff)
#
#    Timer.Start('Create Histograms')
#    CreateHistogramStart = time.time()
#    for channel in TopLevelChannels:
#        ir.CreateHistogram(args.outputpath, Overlap=args.overlap, Channel=channel)
#
#    Timer.End('Create Histograms')
#
#    Timer.Start('Autolevel channels')
#    DownstreamChannels = list()
#    TempChannelList = copy.deepcopy(TopLevelChannels)
#    for channel in TempChannelList:
#        LeveledChannelName = 'Leveled' + channel
#        LeveledChannelName = LeveledChannelName.lower()
#        ir.AutoLevelHistogram(args.outputpath, SourceChannel=channel, TargetChannel=LeveledChannelName, MinCutoffPercent=0.005, MaxCutoffPercent=0.005)
#
#        if(LeveledChannelName in DownstreamChannelMap.keys()):
#            DownstreamChannelMap[LeveledChannelName].append(channel)
#        else:
#            DownstreamChannelMap[LeveledChannelName] = [channel]
#
#        TopLevelChannels.append(LeveledChannelName)
#        DownstreamChannels.append(channel)
#
#    TopLevelChannels = list(set(TopLevelChannels) - set(DownstreamChannels))
#
#
#    #Construct any image pyramids which are unbuilt
#    Timer.Start('Build pyramids')
#
#    ir.BuildPyramids(args.outputpath, Channels=TopLevelChannels)
#
#    if(args.assembleDownstreamChannels):
#        ir.BuildPyramids(args.outputpath, Channels=DownstreamChannels, PyramidLevels=DownstreamPyramidLevels)
#
#    #ir.BuildPyramids(args.outpath, Channels=uniqueChannelList not in TopLevelChannels, PyramidLevels=[1, 4])
#    Timer.End('Build pyramids')
#
#    #Translate refines the initial position of the tiles
#    Timer.Start('Translate mosiac')
#    for channel in TopLevelChannels:
#        ir.Translate(args.outputpath, args.translateDownsample, channel, 'supertile',  max_offset=[8,8])
#    Timer.End('Translate mosiac')
#
#    for channel in TopLevelChannels:
#        ir.CopyMosaicToDownstreamChannels(args.outputpath, TopLevelChannels, DownstreamChannelMap, MosaicType='translate')
#
#    vikingURL = VolumeToXML.CreateXML(args.outputpath)
#
#    #Correct local misalignments with refine-grid
#    Timer.Start('Refine mosaic')
#    for channel in TopLevelChannels:
#        ir.Refine(args.outputpath, channel, SourceMosaicType='translate', TargetMosaicType='grid', Downsample=args.gridDownsample, Cell=96, Mesh=[8, 8], It=4)
#    Timer.End('Refine mosaic')
#
#    for channel in TopLevelChannels:
#        ir.CopyMosaicToDownstreamChannels(args.outputpath, TopLevelChannels, DownstreamChannelMap, MosaicType='grid')
#
#    vikingURL = VolumeToXML.CreateXML(args.outputpath)
#
#    AssembleStartTime = time.time()
#
#    Timer.Start('Assemble')
#    createBlobs = False
#    ChannelsToAssemble = TopLevelChannels
#    if(args.assembleDownstreamChannels):
#        ChannelsToAssemble = list(set(TopLevelChannels) | set(DownstreamChannels))
#
#    for channel in ChannelsToAssemble:
#        DownsampleList =  args.assembleDownsample
#
#        UseBlendFeathering = channel.lower() == MostCommonChannel.lower()
#        feathering = 'binary'
#        if(UseBlendFeathering):
#            feathering = 'blend'
#
#        createBlobs = args.assembleBlobs
#
#        ir.Assemble(args.outputpath,
#                 DownsampleList,
#                 channel,
#                 'grid',
#                 AutoLevel=False,
#                 Interlace=True,
#                 CreateBlobs=True,
#                 BlobRadius=3,
#                 BlobMedian=5,
#                 Feathering=feathering)
#
#    Timer.End('Assemble')
#
#    vikingURL = VolumeToXML.CreateXML(args.outputpath)
#
#
#    if(args.optimizeViewing):
#        Timer.Start('Optimize Viewing')
#        for channel in TopLevelChannels:
#            ir.AssembleTiles(RootPath=args.outputpath,
#                          TileSize=[256,256],
#                          SourceChannel=channel,
#                          SourceMosaicType='grid',
#                          Destination=channel+'_Tiles',
#                          DownsampleList=None)
#        Timer.End('Optimize Viewing')
#
#    vikingURL = VolumeToXML.CreateXML(args.outputpath)
#
#    print vikingURL
#
#    if(args.buildVolume):
#
#        #Generate crude translation/rotation alignment of sections, use the
#        #largest downsample value passed to assemble
#        StosBruteStart = time.time()
#        ir.StosBrute(args.outputpath,16, ChannelsForSections=ChannelsForSections, UseMask=True)
#        StosBruteEnd = time.time()
#
#        vikingURL = VolumeToXML.CreateXML(args.outputpath)
#
#        #Refine the brute results.  Starting with highest downsample value
#        #and decreasing until desired quality is reached.
#        StosGridStart = time.time()
#        ir.StosGrid(args.outputpath,[16,32])
#        StosGridEnd = time.time()
#
#        vikingURL = VolumeToXML.CreateXML(args.outputpath)
#
#
#    Timer.End('Total Runtime')
#    ir.CompressTransforms(args.outputpath)
#
#    #Construct a descriptive XML for our viewer
 #   vikingURL = ''
#    vikingURL = VolumeToXML.CreateXML(args.outputpath)
#
