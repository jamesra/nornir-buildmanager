# #! /Library/Frameworks/Python.framework/Versions/Current/bin python

import sys
import os
import argparse
import nornir_shared.prettyoutput as prettyoutput

from nornir_buildmanager import ImportManager
from nornir_buildmanager import pipelinemanager

def ProcessArgs():
    parser = argparse.ArgumentParser('Buildscript');

    parser.add_argument('-input',
                        action = 'store',
                        required = True,
                        type = str,
                        default = None,
                        help = 'The path to the data to import',
                        dest = 'inputpath'
                        );

    parser.add_argument('-importers',
                        action = 'store',
                        required = True,
                        type = str,
                        default = None,
                        help = 'The importer XML file to use',
                        dest = 'ImporterFilename'
                        );

    parser.add_argument('-config',
                        action = 'store',
                        required = True,
                        type = str,
                        default = None,
                        help = 'The pipeline XML file to use',
                        dest = 'PipelineFilename'
                        );

    parser.add_argument('-pipeline',
                        action = 'store',
                        required = False,
                        type = str,
                        default = None,
                        help="The name of the pipeline to use, defaults to the first <nornir_buildmanager> element in the config file",
                        dest = 'PipelineName'
                        );

    parser.add_argument('-inputbpp',
                        action = 'store',
                        required = False,
                        type = int,
                        default = None,
                        help = 'How many useable bits per pixel the data has, otherwise calculated from the data',
                        dest = 'InputBpp'
                        );

    parser.add_argument('-output',
                        action = 'store',
                        required = True,
                        default = None,
                        type = str,
                        help = 'The path to the data to export',
                        dest = 'outputpath'
                        );

    parser.add_argument('-overlap',
                        action = 'store',
                        required = False,
                        default = 0.1,
                        type = float,
                        help = 'The percentage of overlap from 0 to 1',
                        dest = 'overlap'
                        );

    parser.add_argument('-to',
                        nargs = '+',
                        action = 'store',
                        default = ['james.r.anderson@utah.edu'],
                        required = False,
                        type = str,
                        help = 'Additional addresses to send notification E-mail to',
                        dest = 'toAddresses'
                        );

    parser.add_argument('-cc',
                        nargs = '+',
                        action = 'store',
                        required = False,
                        type = str,
                        help = 'The addresses to cc the E-mail to',
                        dest = 'ccAddresses'
                        );

    parser.add_argument('-prune',
                        action = 'store',
                        required = False,
                        default = None,
                        type = float,
                        help = 'Value to use with ir-prune',
                        dest = 'pruneCutoff'
                        );

    parser.add_argument('-translatelevel',
                        action = 'store',
                        required = False,
                        default = 4,
                        type = int,
                        help = 'The pyramid level which ir-refine-translate is run at, default 1.  Should be power of 2.',
                        dest = 'translateDownsample'
                        );

    parser.add_argument('-gridlevels',
                        action = 'store',
                        required = False,
                        nargs = '+',
                        default = 4,
                        type = int,
                        help = 'The pyramid level which ir-refine-grid is run at, default 1.  Should be power of 2.',
                        dest = 'gridDownsample'
                        );

    parser.add_argument('-assemblelevels',
                        action = 'store',
                        nargs = '+',
                        required = False,
                        default = [16],
                        metavar = 'N',
                        type = int,
                        help = 'List of downsampled levels used for assemble, default is 16.  Should be power of 2.',
                        dest = 'assembleDownsample'
                        );

    parser.add_argument('-assembledownstreamchannels',
                        action = 'store_true',
                        required = False,
                        default = False,
                        help = 'Set flag if downstream channels should have image pyramids and assembled mosaics output',
                        dest = 'assembleDownstreamChannels'
                        );

    parser.add_argument('-blobs',
                        action = 'store_true',
                        required = False,
                        default = False,
                        help = 'Set flag to assemble blobs for output assemble images',
                        dest = 'assembleBlobs'
                        );

    parser.add_argument('-volume',
                        action = 'store_true',
                        required = False,
                        default = False,
                        help = 'Set -volume flag to align sections into a volume',
                        dest = 'buildVolume'
                        );

    parser.add_argument('-evaluatestats',
                        action = 'store_true',
                        required = False,
                        default = False,
                        help = 'Set -EvaluateStats to calculate min, max, mean, median for each channel',
                        dest = 'evaluateStats'
                        );

    parser.add_argument('-correctshading',
                        action = 'store_true',
                        required = False,
                        default = False,
                        help = 'Set -correctshading to correct for shading artifacts, implies EvaluateStats',
                        dest = 'correctShading'
                        );

    parser.add_argument('-optimizeviewing',
                        action = 'store_true',
                        required = False,
                        default = False,
                        help = 'Set -optimizeviewing to obtain the ideal Viewer performance.',
                        dest = 'optimizeViewing'
                        );

    parser.add_argument('-brutelevel',
                        required = False,
                        type = int,
                        default = 1,
                        help = 'The downsample level to execute brute at',
                        dest = 'brutedownsample'
                        );

    parser.add_argument('-gridlevel',
                        required = False,
                        type = int,
                        nargs = '+',
                        default = 1,
                        help = 'The downsample level to execute stos-grid at, must be lower or equal number to brutedownsample',
                        dest = 'griddownsamplelist'
                        );

    args = parser.parse_args();
    return args;

def Main(args):

    if(not os.path.exists(args.inputpath)):
        prettyoutput.Log("Input directory does not exist");
        sys.exit();

    if(not os.path.exists(args.outputpath)):
        prettyoutput.Log("Output directory does not exist, creating it");
        os.makedirs(args.outputpath);

    TimingOutput = 'Timing.txt';

    if(args.overlap >= 1.0):
        prettyoutput.Log("Overlap greater than 1, assuming a percentage and dividing by 100");
        args.overlap /= 100;

    assert(args.overlap >= 0.0);
    assert(args.overlap <= 1.0);

    importerObj = ImportManager.ImportManager.Load(args.ImporterFilename);

    PipelineObj = PipelineManager.PipelineManager.Load(args.PipelineFilename, args.PipelineName);

    importerObj.ConvertAll(args.inputpath, args.outputpath, args.overlap);

    PipelineObj.Execute(args);

    prettyoutput.Log(str(PipelineObj));


# #Figure out the pyramid levels we need for assemble
# DownstreamPyramidLevels = [1];
#
# for level in args.assembleDownsample:
#    if(not level  in DownstreamPyramidLevels):
#        DownstreamPyramidLevels.append(float(level));
#
# if(not args.translateDownsample  in DownstreamPyramidLevels):
#    DownstreamPyramidLevels.append(float(args.translateDownsample));
#
# if(not args.gridDownsample  in DownstreamPyramidLevels):
#    DownstreamPyramidLevels.append(float(args.gridDownsample));
#
# LowPriority.lowpriority();
#
# Timer = TaskTimer.TaskTimer();
#
# try:
#    Timer.Start('Total Runtime');
#
#    Timer.Start('ADoc To Mosaic');
#    ExtensionToImporterMap = {'idoc' :  idoc.SerialEMIDocImport(),
#                              'adoc' :  idoc.SerialEMIDocImport(),
#                              'pmg'  :  PMG.PMGImport()};
#    [ChannelsForSections, MostCommonChannel] = Importer.Importer.ConvertAll(args.inputpath, args.outputpath, ExtensionToImporterMap, TileOverlap=args.overlap, TargetBpp=args.InputBpp);
#
#    ir.RemoveDirectorySpaces(args.outputpath);
#
#    ##Get Unique Channel Names
#    baseChannelList = [];
#    for channelList in ChannelsForSections.itervalues():
#        for channel in channelList:
#            if channel not in baseChannelList:
#                baseChannelList.append(channel);
#
#    #Ensure we do not have missing tiles in our mosaic files
#    for channel in baseChannelList:
#        ir.ValidateMosaicFile(args.outputpath, channel);
#
#    if args.evaluateStats or args.correctShading:
#        Timer.Start('Evaluate Channels');
#        for channel in baseChannelList:
#            PostProcessBlurCmdTemplate = ' -virtual-pixel edge -gaussian-blur 0x%(sigma)f ';
#
#            ir.EvaluateSequence(args.outputpath, 1, channel, SourceMosaicType="supertile", EvaluateSequenceArg='min', PostEvaluateSequenceArg=PostProcessBlurCmdTemplate % {'sigma' : 2});
#            ir.EvaluateSequence(args.outputpath, 1, channel, SourceMosaicType="supertile", EvaluateSequenceArg='max', PostEvaluateSequenceArg=PostProcessBlurCmdTemplate % {'sigma' : 2});
#            ir.EvaluateSequence(args.outputpath, 1, channel, SourceMosaicType="supertile", EvaluateSequenceArg='median', PostEvaluateSequenceArg=PostProcessBlurCmdTemplate % {'sigma' : 2});
#            ir.EvaluateSequence(args.outputpath, 1, channel, SourceMosaicType="supertile", EvaluateSequenceArg='mean', PostEvaluateSequenceArg=PostProcessBlurCmdTemplate % {'sigma' : 2});
#    #
#        Timer.End('Evaluate Channels');
#
#    #Map of channel names used for registration to the original channels
#    DownstreamChannelMap = dict();
#    TopLevelChannels = list();
#
#    if args.correctShading:
#        Timer.Start('Created Corrected Channels');
#
#        for channel in baseChannelList:
#
#            ShadingChannel = 'ShadingCorrected' + channel;
#            ShadingChannel = ShadingChannel.lower();
#            ir.CreateCorrectedChannel(args.outpath, SourceChannel=channel, TargetChannel=ShadingChannel, CorrectionCandidates=['median'], InvertSourceForCandidates=[False], ComposeOperator=['minus']);
#
#            TopLevelChannels.append(ShadingChannel);
#
#            if(ShadingChannel in DownstreamChannelMap.keys()):
#                DownstreamChannelMap[ShadingChannel].append(channel);
#            else:
#                DownstreamChannelMap[ShadingChannel] = [channel];
#
#        #All of the channels we just processed are 'downstream' from the corrected channel now
#        DownstreamChannels = copy.deepcopy(baseChannelList);
#
#        Timer.End('Created Corrected Channels');
#    else:
#        TopLevelChannels = copy.deepcopy(baseChannelList);
#
#    if not args.pruneCutoff is None:
#        for channel in TopLevelChannels:
#            ir.Prune(args.outputpath, args.pruneCutoff);
#
#    Timer.Start('Create Histograms');
#    CreateHistogramStart = time.time();
#    for channel in TopLevelChannels:
#        ir.CreateHistogram(args.outputpath, Overlap=args.overlap, Channel=channel);
#
#    Timer.End('Create Histograms');
#
#    Timer.Start('Autolevel channels');
#    DownstreamChannels = list();
#    TempChannelList = copy.deepcopy(TopLevelChannels);
#    for channel in TempChannelList:
#        LeveledChannelName = 'Leveled' + channel;
#        LeveledChannelName = LeveledChannelName.lower();
#        ir.AutoLevelHistogram(args.outputpath, SourceChannel=channel, TargetChannel=LeveledChannelName, MinCutoffPercent=0.005, MaxCutoffPercent=0.005);
#
#        if(LeveledChannelName in DownstreamChannelMap.keys()):
#            DownstreamChannelMap[LeveledChannelName].append(channel);
#        else:
#            DownstreamChannelMap[LeveledChannelName] = [channel];
#
#        TopLevelChannels.append(LeveledChannelName);
#        DownstreamChannels.append(channel);
#
#    TopLevelChannels = list(set(TopLevelChannels) - set(DownstreamChannels));
#
#
#    #Construct any image pyramids which are unbuilt
#    Timer.Start('Build pyramids');
#
#    ir.BuildPyramids(args.outputpath, Channels=TopLevelChannels);
#
#    if(args.assembleDownstreamChannels):
#        ir.BuildPyramids(args.outputpath, Channels=DownstreamChannels, PyramidLevels=DownstreamPyramidLevels);
#
#    #ir.BuildPyramids(args.outpath, Channels=uniqueChannelList not in TopLevelChannels, PyramidLevels=[1, 4]);
#    Timer.End('Build pyramids');
#
#    #Translate refines the initial position of the tiles
#    Timer.Start('Translate mosiac');
#    for channel in TopLevelChannels:
#        ir.Translate(args.outputpath, args.translateDownsample, channel, 'supertile',  max_offset=[8,8]);
#    Timer.End('Translate mosiac');
#
#    for channel in TopLevelChannels:
#        ir.CopyMosaicToDownstreamChannels(args.outputpath, TopLevelChannels, DownstreamChannelMap, MosaicType='translate');
#
#    vikingURL = VolumeToXML.CreateXML(args.outputpath);
#
#    #Correct local misalignments with refine-grid
#    Timer.Start('Refine mosaic');
#    for channel in TopLevelChannels:
#        ir.Refine(args.outputpath, channel, SourceMosaicType='translate', TargetMosaicType='grid_8x8x96', Downsample=args.gridDownsample, Cell=96, Mesh=[8, 8], It=4);
#        ir.Refine(args.outputpath, channel, SourceMosaicType='grid_8x8x96', TargetMosaicType='grid_20x20x32', Downsample=args.gridDownsample, Cell=32, Mesh=[20, 20], It=10);
#    Timer.End('Refine mosaic');
#
#    for channel in TopLevelChannels:
#        ir.CopyMosaicToDownstreamChannels(args.outputpath, TopLevelChannels, DownstreamChannelMap, MosaicType='grid');
#
#    vikingURL = VolumeToXML.CreateXML(args.outputpath);
#
#    AssembleStartTime = time.time();
#
#    Timer.Start('Assemble');
#    createBlobs = False;
#    ChannelsToAssemble = TopLevelChannels;
#    if(args.assembleDownstreamChannels):
#        ChannelsToAssemble = list(set(TopLevelChannels) | set(DownstreamChannels));
#
#    for channel in ChannelsToAssemble:
#        DownsampleList =  args.assembleDownsample;
#
#        UseBlendFeathering = channel.lower() == MostCommonChannel.lower();
#        feathering = 'binary';
#        if(UseBlendFeathering):
#            feathering = 'blend';
#
#        createBlobs = args.assembleBlobs;
#
#        ir.Assemble(args.outputpath,
#                 DownsampleList,
#                 channel,
#                 'grid_20x20x32',
#                 AutoLevel=False,
#                 Interlace=True,
#                 CreateBlobs=True,
#                 BlobRadius=3,
#                 BlobMedian=5,
#                 Feathering=feathering);
#
#    Timer.End('Assemble');
#
#    vikingURL = VolumeToXML.CreateXML(args.outputpath);
#
#
#    if(args.optimizeViewing):
#        Timer.Start('Optimize Viewing');
#        for channel in TopLevelChannels:
#            ir.AssembleTiles(RootPath=args.outputpath,
#                          TileSize=[256,256],
#                          SourceChannel=channel,
#                          SourceMosaicType='grid_20x20x32',
#                          Destination=channel+'_Tiles',
#                          DownsampleList=None);
#        Timer.End('Optimize Viewing');
#
#    vikingURL = VolumeToXML.CreateXML(args.outputpath);
#
#    print vikingURL;
#
#    if(args.buildVolume):
#
#        #Generate crude translation/rotation alignment of sections, use the
#        #largest downsample value passed to assemble
#        StosBruteStart = time.time();
#        ir.StosBrute(args.outputpath,16, ChannelsForSections=ChannelsForSections, UseMask=True);
#        StosBruteEnd = time.time();
#
#        vikingURL = VolumeToXML.CreateXML(args.outputpath);
#
#        #Refine the brute results.  Starting with highest downsample value
#        #and decreasing until desired quality is reached.
#        StosGridStart = time.time();
#        ir.StosGrid(args.outputpath,[16,32]);
#        StosGridEnd = time.time();
#
#        vikingURL = VolumeToXML.CreateXML(args.outputpath);
#
#
#    Timer.End('Total Runtime');
#    ir.CompressTransforms(args.outputpath);
#
#    #Construct a descriptive XML for our viewer
#    vikingURL = VolumeToXML.CreateXML(args.outputpath);
#
# finally:
#    OutStr = str(Timer);
#    prettyoutput.Log(OutStr);
#    try:
#        OutputFile = open(os.path.join(args.outpath, 'Timing.txt'), 'w');
#        OutputFile.writelines(OutStr);
#        OutputFile.close();
#    except:
#        prettyoutput.Log('Could not write timing.txt');
#
# #Email the completion notice
# class EmailArgs(object): pass
#
# Email = EmailArgs();
# Email.subject = "Build progress";
# Email.toAddresses = args.toAddresses;
# Email.ccAddresses = args.ccAddresses;
# Email.fromAddress = "james.r.anderson@utah.edu";
# Email.fromFriendlyAddress = "Build Notifications";
# Email.message = "A build has completed.  The output can be viewed at: " + vikingURL + "\n"+ '\n'.join(map(str, TimingOutput));
# Email.host = 'smtp.utah.edu';
# Email.port = 25;
#
# EmailLib.SendMail(Email);

if __name__ == "__main__":
    args = ProcessArgs();
    Main(args);
