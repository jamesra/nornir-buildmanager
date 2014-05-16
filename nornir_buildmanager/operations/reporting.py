'''
Created on Oct 3, 2012

@author: u0490822
'''

import logging
import shutil
import os
import nornir_shared.images
import nornir_pools as Pools
import nornir_imageregistration.core
import datetime
import nornir_buildmanager.importers.idoc as idoc
import nornir_shared.plot
import nornir_shared.prettyoutput
from nornir_buildmanager.pipelinemanager import PipelineManager, ArgumentSet
import nornir_shared.files as nfiles
# import Pipelines.VolumeManagerETree as VolumeManager

if __name__ == '__main__':
    pass


class RowList(list):
    '''class used for HTML to place into rows'''
    pass


class ColumnList(list):
    '''Class used for HTML to place into columns'''

    @property
    def caption(self):
        if hasattr(self, '_caption'):
            return self._caption

        return None

    @caption.setter
    def caption(self, val):
        self._caption = val


class UnorderedItemList(list):
    '''Class used for HTML to create unordered list from items'''
    pass


class HTMLBuilder(list):
    '''A list of strings that contain HTML'''

    @property
    def IndentLevel(self):
        return self.__IndentLevel

    def Indent(self):
        self.__IndentLevel += 1

    def Dedent(self):
        self.__IndentLevel -= 1

    def __init__(self, indentlevel=None):
        super(HTMLBuilder, self).__init__()

        if indentlevel is None:
            self.__IndentLevel = 0
        else:
            self.__IndentLevel = indentlevel

    def __IndentString(self, indent):

        if indent is None:
            return ''

        return ' ' * indent

    def Add(self, value):
        if isinstance(value, list):
            self.extend(value)
        elif isinstance(value, str):
            self.append(self.__IndentString(self.IndentLevel) + value)

    def __str__(self):
        return ''.join([x for x in self])


class HTMLPaths(object):

    @property
    def SourceRootDir(self):
        return self._SourceRootDir

    @property
    def OutputDir(self):
        return self._OutputDir

    @property
    def OutputFile(self):
        return self._OutputFile

    @property
    def ThumbnailDir(self):
        return self._ThumbnailDir

    @property
    def ThumbnailRelative(self):
        return self._ThumbnialRootRelative

    def __init__(self, RootElementPath, OutputFileFullPath):

        if OutputFileFullPath is None:
            OutputFileFullPath = "DefaultReport.html"

        self._SourceRootDir = RootElementPath
        if not isinstance(RootElementPath, str):
            self._SourceRootDir = RootElementPath.Path


        self._OutputDir = os.path.dirname(OutputFileFullPath)
        self._OutputDir.strip()
        if len(self.OutputDir) == 0:
            self._OutputDir = RootElementPath
            self._OutputFile = os.path.basename(OutputFileFullPath)
        else:
            self._OutputFile = os.path.basename(OutputFileFullPath)

        (self._ThumbnialRootRelative, self._ThumbnailDir) = self.__ThumbnailPaths()


    def CreateOutputDirs(self):

        if not os.path.exists(self.OutputDir):
            os.makedirs(self.OutputDir)

        if not os.path.exists(self.ThumbnailDir):
            os.makedirs(self.ThumbnailDir)

    @classmethod
    def __StripLeadingPathSeperator(cls, path):
        while(path[0] == os.sep or path[0] == os.altsep):
            path = path[1:]

        return path


    def GetSubNodeRelativePath(self, subpath):

        fullpath = subpath
        if not isinstance(fullpath, str):
            fullpath = subpath.FullPath

        RelativePath = fullpath.replace(self.SourceRootDir, '')
        RelativePath = os.path.dirname(RelativePath)
        RelativePath = HTMLPaths.__StripLeadingPathSeperator(RelativePath)

        return RelativePath

    def GetSubNodeFullPath(self, subpath):

        RelPath = self.GetSubNodeRelativePath(subpath)
        if not RelPath is None:
            FullPath = os.path.join(RelPath, subpath.Path)
            FullPath = HTMLPaths.__StripLeadingPathSeperator(FullPath)
        else:
            return subpath.FullPath

        return FullPath

    def __ThumbnailPaths(self):
        '''Return relative and absolute thumnails for an OutputFile'''
        (ThumbnailDirectory, ext) = os.path.splitext(self.OutputFile)
        ThumbnailDirectoryFullPath = os.path.join(self.OutputDir, ThumbnailDirectory)

        return (ThumbnailDirectory, ThumbnailDirectoryFullPath)

    def GetFileAnchorHTML(self, Node, text):
        '''Returns an <A> anchor node pointing at the nodes file.  If the file does not exist an empty string is returned'''
        # Temp patch
        FullPath = None
        patchedPath = _patchupNotesPath(Node)
        FullPath = os.path.join(Node.Parent.FullPath, patchedPath)

        RelPath = self.GetSubNodeRelativePath(FullPath)

        (junk, ext) = os.path.splitext(patchedPath)
        ext = ext.lower()

        HTML = ""

        if os.path.exists(FullPath):
            SrcFullPath = os.path.join(RelPath, patchedPath)
            HTML += HTMLAnchorTemplate % {'href' : SrcFullPath, 'body' : text}

        return HTML


HTMLImageTemplate = '<img src="%(src)s" alt="%(AltText)s" width="%(ImageWidth)s" height="%(ImageHeight)s" />'
HTMLAnchorTemplate = '<a href="%(href)s">%(body)s</a>'

# We add this to thumbnails and other generated files to prevent name collisions
TempFileSalt = 0

def GetTempFileSaltString():
    global TempFileSalt

    saltString = str(TempFileSalt) + "_"
    TempFileSalt = TempFileSalt + 1

    return saltString



def CopyFiles(DataNode, OutputDir=None, Move=False, **kwargs):

    if OutputDir is None:
        return

    logger = kwargs.get('Logger', logging.getLogger('CopyFiles'))

    if not os.path.exists(OutputDir):
        os.makedirs(OutputDir)

    if os.path.exists(DataNode.FullPath):

        if os.path.isfile(DataNode.FullPath):
            OutputFileFullPath = os.path.join(OutputDir, DataNode.Path)
            nfiles.RemoveOutdatedFile(DataNode.FullPath, OutputFileFullPath)

            if not os.path.exists(OutputFileFullPath):

                logger.info(DataNode.FullPath + " -> " + OutputFileFullPath)
                shutil.copyfile(DataNode.FullPath, OutputFileFullPath)
        else:
            # Just copy the directory over, this is an odd case
            logger.info("Copy directory " + DataNode.FullPath + " -> " + OutputDir)
            shutil.copy(DataNode.FullPath, OutputDir)


def _AbsoluePathFromRelativePath(node, path):
    '''If path is relative then make it relative from the directory containing the volume_node'''

    if not os.path.isabs(path):
        volume_dir = node.Root.FullPath
        return os.path.join(volume_dir, '..', path)
    else:
        return path


def CopyImage(FilterNode, Downsample=1.0, OutputDir=None, Move=False, **kwargs):

    if OutputDir is None:
        return

    OutputDir = _AbsoluePathFromRelativePath(FilterNode, OutputDir)
    logger = kwargs.get('Logger', logging.getLogger('CopyImage'))

    if not os.path.exists(OutputDir):
        os.makedirs(OutputDir)

    # Find the imageset for the DataNode
    ImageNode = FilterNode.GetOrCreateImage(Downsample)

    if os.path.exists(ImageNode.FullPath):

        if os.path.isfile(ImageNode.FullPath):
            OutputFileFullPath = os.path.join(OutputDir, ImageNode.Path)
            nfiles.RemoveOutdatedFile(ImageNode.FullPath, OutputFileFullPath)

            if not os.path.exists(OutputFileFullPath):

                logger.info(ImageNode.FullPath + " -> " + OutputFileFullPath)
                shutil.copyfile(ImageNode.FullPath, OutputFileFullPath)
        else:
            # Just copy the directory over, this is an odd case
            logger.info("Copy directory " + ImageNode.FullPath + " -> " + OutputDir)
            shutil.copy(ImageNode.FullPath, OutputDir)

def MoveFiles(DataNode, OutputDir, Move=False, **kwargs):
    if OutputDir is None:
        return

    if not os.path.exists(OutputDir):
        os.makedirs(OutputDir)

    if os.path.exists(DataNode.FullPath):
        shutil.move(DataNode.FullPath, OutputDir)

    return None


def _patchupNotesPath(NotesNode):
    '''Temp fix for old notes elements without a path attribute'''
    if 'SourceFilename' in NotesNode.attrib:
        return NotesNode.SourceFilename
    elif 'Path' in NotesNode.attrib:
        return NotesNode.Path
    else:
        raise Exception('No path data for notes node')


def __RemoveRTFBracket(rtfStr):
    '''Removes brackets from rtfStr in pairs'''

    assert(rtfStr[0] == '{')
    rtfStr = rtfStr[1:]
    rbracket = rtfStr.find('}')
    lbracket = rtfStr.find('{')

    if lbracket < 0:
        return rtfStr[rbracket + 1:]

    if rbracket < 0:
        '''No paired bracket, remove left bracket and return'''
        return rtfStr

    if lbracket < rbracket:
        rtfStr = rtfStr[:lbracket] + __RemoveRTFBracket(rtfStr[lbracket:])
        return __RemoveRTFBracket('{' + rtfStr)
    else:
        return rtfStr[rbracket + 1:]

def __RTFToHTML(rtfStr):
    '''Crudely convert a rich-text string to html'''

    translationTable = {'\pard' : '<br\>',
                        '\par' : '<br\>',
                        '\viewkind' : ''}

    if(rtfStr[0] == '{'):
        rtfStr = rtfStr[1:-2]

    HTMLOut = HTMLBuilder()

    HTMLOut.Add('<p>')

    translatekeys = translationTable.keys()
    translatekeys = sorted(translatekeys, key=len, reverse=True)

    while len(rtfStr) > 0:

        if '{' == rtfStr[0]:
            rtfStr = __RemoveRTFBracket(rtfStr)
            continue

        for key in translatekeys:
            if rtfStr.startswith(key):
                HTMLOut.Add(translationTable[key])
                rtfStr = rtfStr[len(key):]
                continue

        if rtfStr.startswith('\\'):
            rtfStr = rtfStr[1:]
            iSlash = rtfStr.find('\\')
            iSpace = rtfStr.find(' ')
            iBracket = rtfStr.find('{')

            indicies = [iSlash, iSpace, iBracket]
            goodIndex = []
            for i in indicies:
                if i > 0:
                    goodIndex.append(i)

            if len(goodIndex) == 0:
                # The string is empty, stop the loop
                break

            iClip = min(goodIndex)

            rtfStr = rtfStr[iClip:]
        else:
            HTMLOut.Add(rtfStr[0])
            rtfStr = rtfStr[1:]

    outStr = str(HTMLOut).strip()
    while outStr.endswith('<br\>'):
        outStr = outStr[:-len('<br\>')].strip()

    if len(HTMLOut) > 0:
        return '<p>' + outStr

    return ''


def HTMLFromNotesNode(DataNode, htmlPaths, **kwargs):

    # Temp patch
    HTML = htmlPaths.GetFileAnchorHTML(DataNode, "Notes: ")

    if not (DataNode.text is None or len(DataNode.text) == 0):
        (junk, ext) = os.path.splitext(_patchupNotesPath(DataNode))

        if 'rtf' in ext or 'doc' in ext:
            HTML += __RTFToHTML(DataNode.text)
        else:
            HTML += DataNode.text

    if len(HTML) == 0:
        return None

    return HTML


def __ExtractLogDataText(Data):

    DriftRows = RowList()
    TimeRows = RowList()
    MetaRows = RowList()
    Columns = ColumnList()


    if hasattr(Data, 'AverageTileDrift'):
        DriftRows.append(['Avg. tile drift:', '<b>%.3g nm/sec</b>' % float(Data.AverageTileDrift)])

    if hasattr(Data, 'MinTileDrift'):
        DriftRows.append(['Min tile drift:', '%.3g nm/sec' % float(Data.MinTileDrift)])

    if hasattr(Data, 'MaxTileDrift'):
        DriftRows.append(['Max tile drift:', '%.3g nm/sec' % float(Data.MaxTileDrift)])

    if hasattr(Data, 'AverageTileTime'):
        TimeRows.append(['Avg. tile time:', '<b>%.3g sec</b>' % float(Data.AverageTileTime)])

    if hasattr(Data, 'FastestTileTime'):
        TimeRows.append(['Fastest tile time:', '%.3g sec' % Data.FastestTileTime])

    if hasattr(Data, 'NumTiles'):
        TimeRows.append(['Number of tiles:', str(Data.NumTiles)])

    if hasattr(Data, 'TotalTime'):
        dtime = datetime.timedelta(seconds=float(Data.TotalTime))
        TimeRows.append(['Total time:', str(dtime)])

    if hasattr(Data, 'Startup'):
        MetaRows.append(['Capture Date:', '<b>' + str(Data.Startup) + '</b>'])

    if hasattr(Data, 'Version'):
        MetaRows.append(['Version:', str(Data.Version)])

    if not TimeRows is None:
        Columns.append(TimeRows)

    if not DriftRows is None:
        Columns.append(DriftRows)

    if not MetaRows is None:
        Columns.append(MetaRows)

    return Columns




def HTMLFromDataNode(DataNode, htmlpaths, MaxImageWidth=None, MaxImageHeight=None, **kwargs):

    if not hasattr(DataNode, 'Name'):
        return

    if DataNode.Name == 'Log':
        return HTMLFromLogDataNode(DataNode, htmlpaths, MaxImageWidth, MaxImageHeight, **kwargs)
    elif DataNode.Name == 'IDoc':
        return HTMLFromIDocDataNode(DataNode, htmlpaths, MaxImageWidth, MaxImageHeight, **kwargs)
    else:
        return HTMLFromUnknownDataNode(DataNode, htmlpaths, MaxImageWidth, MaxImageHeight, **kwargs)


def HTMLFromUnknownDataNode(DataNode, htmlpaths, MaxImageWidth=None, MaxImageHeight=None, **kwargs):

    Name = "Data"
    if hasattr(DataNode, 'Name'):
        Name = DataNode.Name

    return htmlpaths.GetFileAnchorHTML(DataNode, Name)


def __ExtractIDocDataText(DataNode):

    rows = RowList()

    if 'ExposureTime' in DataNode.attrib:
        rows.append(['Exposure Time:', '%.4g sec' % float(DataNode.ExposureTime)])

    if 'ExposureDose' in DataNode.attrib:
        rows.append(['Exposure Dose:', '%.4g nm/sec' % float(DataNode.ExposureDose)])

    if 'Magnification' in DataNode.attrib:
        rows.append(['Magnification:', '%.4g X' % float(DataNode.Magnification)])

    if 'PixelSpacing' in DataNode.attrib:
        rows.append(['Pixel Spacing:', '%.4g' % float(DataNode.PixelSpacing)])

    if 'SpotSize' in DataNode.attrib:
        rows.append(['Spot Size:', '%d' % int(DataNode.SpotSize)])

    if 'TargetDefocus' in DataNode.attrib:
        rows.append(['Target Defocus:', '%.4g' % float(DataNode.TargetDefocus)])

    return rows

#     ExposureList = []
#     MagList = []
#     SettingList = []
#     Columns = ColumnList()
#
#     if 'ExposureTime' in DataNode.attrib:
#         ExposureList.append(['Exposure Time:', '%.4g sec' % float(DataNode.ExposureTime)])
#
#     if 'ExposureDose' in DataNode.attrib:
#         ExposureList.append(['Exposure Dose:', '%.4g nm/sec' % float(DataNode.ExposureDose)])
#
#     if 'Magnification' in DataNode.attrib:
#         MagList.append(['Magnification:', '%.4g X' % float(DataNode.Magnification)])
#
#     if 'PixelSpacing' in DataNode.attrib:
#         MagList.append(['Pixel Spacing:', '%.4g' % float(DataNode.PixelSpacing)])
#
#     if 'SpotSize' in DataNode.attrib:
#         SettingList.append(['Spot Size:', '%d' % int(DataNode.SpotSize)])
#
#     if 'TargetDefocus' in DataNode.attrib:
#         SettingList.append(['Target Defocus:', '%.4g' % float(DataNode.TargetDefocus)])
#
#     if len(ExposureList) > 0:
#         Columns.append(ExposureList)
#
#     if len(MagList) > 0:
#         Columns.append(MagList)
#
#     if len(SettingList) > 0:
#         Columns.append(SettingList)
#
#     return Columns


def HTMLFromIDocDataNode(DataNode, htmlpaths, MaxImageWidth=None, MaxImageHeight=None, **kwargs):
    '''
    <Data CreationDate="2013-12-16 11:58:15" DataMode="6" ExposureDose="0" ExposureTime="0.5" Image="10000.tif"
     ImageSeries="1" Intensity="0.52256" Magnification="5000" Montage="1" Name="IDoc" Path="1.idoc" PixelSpacing="21.76" 
     RotationAngle="-178.3" SpotSize="3" TargetDefocus="-0.5" TiltAngle="0.1" Version="1.0" />
    '''


    rows = __ExtractIDocDataText(DataNode)
    rows.insert(0, htmlpaths.GetFileAnchorHTML(DataNode, "Capture Settings Summary"))

    return rows


def HTMLFromLogDataNode(DataNode, htmlpaths, MaxImageWidth=None, MaxImageHeight=None, **kwargs):

    if MaxImageWidth is None:
        MaxImageWidth = 1024

    if MaxImageHeight is None:
        MaxImageHeight = 1024

    if not DataNode.Name == 'Log':
        return None

    TableEntries = {}

    logFilePath = DataNode.FullPath
    if os.path.exists(logFilePath):

        Data = idoc.SerialEMLog.Load(logFilePath)

        RelPath = htmlpaths.GetSubNodeRelativePath(DataNode)

        TableEntries["2"] = __ExtractLogDataText(Data)

        TPool = Pools.GetGlobalMultithreadingPool()

        LogSrcFullPath = os.path.join(RelPath, DataNode.Path)

        DriftSettleThumbnailFilename = GetTempFileSaltString() + "DriftSettle.png"
        DriftSettleImgSrcPath = os.path.join(htmlpaths.ThumbnailRelative, DriftSettleThumbnailFilename)
        DriftSettleThumbnailOutputFullPath = os.path.join(htmlpaths.ThumbnailDir, DriftSettleThumbnailFilename)

        # nfiles.RemoveOutdatedFile(logFilePath, DriftSettleThumbnailOutputFullPath)
        # if not os.path.exists(DriftSettleThumbnailOutputFullPath):
        TPool.add_task(DriftSettleThumbnailFilename, idoc.PlotDriftSettleTime(Data, DriftSettleThumbnailOutputFullPath))

        DriftGridThumbnailFilename = GetTempFileSaltString() + "DriftGrid.png"
        DriftGridImgSrcPath = os.path.join(htmlpaths.ThumbnailRelative, DriftGridThumbnailFilename)
        DriftGridThumbnailOutputFullPath = os.path.join(htmlpaths.ThumbnailDir, DriftGridThumbnailFilename)

        # nfiles.RemoveOutdatedFile(logFilePath, DriftGridThumbnailFilename)
        # if not os.path.exists(DriftGridThumbnailFilename):
        TPool.add_task(DriftGridThumbnailFilename, idoc.PlotDriftGrid(Data, DriftGridThumbnailOutputFullPath))

        # Build a histogram of drift settings
#        x = []
#        y = []
#        for t in Data.tileData.values():
#            if not (t.dwellTime is None or t.drift is None):
#                x.append(t.dwellTime)
#                y.append(t.drift)
#
#        ThumbnailFilename = GetTempFileSaltString() + "Drift.png"
#        ImgSrcPath = os.path.join(ThumbnailDirectoryRelPath, ThumbnailFilename)
#        ThumbnailOutputFullPath = os.path.join(ThumbnailDirectory, ThumbnailFilename)


                # PlotHistogram.PolyLinePlot(lines, Title="Stage settle time, max drift %g" % maxdrift, XAxisLabel='Dwell time (sec)', YAxisLabel="Drift (nm/sec)", OutputFilename=ThumbnailOutputFullPath)
        HTMLDriftSettleImage = HTMLImageTemplate % {'src' : DriftSettleImgSrcPath, 'AltText' : 'Drift scatterplot', 'ImageWidth' : MaxImageWidth, 'ImageHeight' : MaxImageHeight}
        HTMLDriftSettleAnchor = HTMLAnchorTemplate % {'href' : DriftSettleImgSrcPath, 'body' : HTMLDriftSettleImage }

        HTMLDriftGridImage = HTMLImageTemplate % {'src' : DriftGridImgSrcPath, 'AltText' : 'Drift scatterplot', 'ImageWidth' : MaxImageWidth, 'ImageHeight' : MaxImageHeight}
        HTMLDriftGridAnchor = HTMLAnchorTemplate % {'href' : DriftGridImgSrcPath, 'body' : HTMLDriftGridImage }

        TableEntries["1"] = HTMLAnchorTemplate % {'href' : LogSrcFullPath, 'body' : "Log File" }
        TableEntries["3"] = ColumnList([HTMLDriftSettleAnchor, HTMLDriftGridAnchor])
    else:
        if 'AverageTileDrift' in DataNode.attrib:
            TableEntries.append(['Average tile drift:', '%.3g nm/sec' % float(DataNode.AverageTileDrift)])

        if 'MinTileDrift' in DataNode.attrib:
            TableEntries.append(['Min tile drift:', '%.3g nm/sec' % float(DataNode.MinTileDrift)])

        if 'MaxTileDrift' in DataNode.attrib:
            TableEntries.append(['Max tile drift:', '%.3g nm/sec' % float(DataNode.MaxTileDrift)])

        if 'AverageTileTime' in DataNode.attrib:
            TableEntries.append(['Average tile time:', '%.3g' % float(DataNode.AverageTileTime)])

        if 'FastestTileTime' in DataNode.attrib:
            dtime = datetime.timedelta(seconds=float(DataNode.FastestTileTime))
            TableEntries.append(['Fastest tile time:', str(dtime)])

        if 'CaptureTime' in DataNode.attrib:
            dtime = datetime.timedelta(seconds=float(DataNode.CaptureTime))
            TableEntries.append(['Total capture time:', str(dtime)])

    if len(TableEntries) == 0:
        return None

    # HTML = MatrixToTable(TableEntries)
    return TableEntries


def AddImageToTable(TableEntries, htmlPaths, DriftSettleThumbnailFilename):
    DriftSettleThumbnailFilename = GetTempFileSaltString() + "DriftSettle.png"
    DriftSettleImgSrcPath = os.path.join(htmlPaths.ThumbnailRelative, DriftSettleThumbnailFilename)
    DriftSettleThumbnailOutputFullPath = os.path.join(htmlPaths.ThumbnailDir, DriftSettleThumbnailFilename)


def ImgTagFromImageNode(ImageNode, HtmlPaths, MaxImageWidth=None, MaxImageHeight=None, Logger=None, **kwargs):
    '''Create the HTML to display an image with an anchor to the full image.
       If specified RelPath should be added to the elements path for references in HTML instead of using the fullpath attribute'''

    assert(not ImageNode is None)
    assert(not Logger is None)
    if MaxImageWidth is None:
        MaxImageWidth = 1024

    if MaxImageHeight is None:
        MaxImageHeight = 1024

    imageFilename = ImageNode.Path


    if not os.path.exists(ImageNode.FullPath):
        Logger.error("Missing image file: " + ImageNode.FullPath)
        return ""

    ImgSrcPath = HtmlPaths.GetSubNodeFullPath(ImageNode)

    [Height, Width] = nornir_imageregistration.core.GetImageSize(ImageNode.FullPath)

    # Create a thumbnail if needed
    if Width > MaxImageWidth or Height > MaxImageHeight:
        Scale = max(float(Width) / MaxImageWidth, float(Height) / MaxImageHeight)
        Scale = 1 / Scale

        if not os.path.exists(HtmlPaths.ThumbnailDir):
            os.makedirs(HtmlPaths.ThumbnailDir)

        ThumbnailFilename = GetTempFileSaltString() + ImageNode.Path
        ImgSrcPath = os.path.join(HtmlPaths.ThumbnailRelative, ThumbnailFilename)

        ThumbnailOutputFullPath = os.path.join(HtmlPaths.ThumbnailDir, ThumbnailFilename)

        # nfiles.RemoveOutdatedFile(ImageNode.FullPath, ThumbnailOutputFullPath)
        # if not os.path.exists(ThumbnailOutputFullPath):
        cmd = "Convert " + ImageNode.FullPath + " -resize " + str(Scale * 100) + "% " + ThumbnailOutputFullPath
        Pool = Pools.GetGlobalProcessPool()
        Pool.add_process(cmd, cmd + " && exit", shell=True)

        Width = int(Width * Scale)
        Height = int(Height * Scale)

    HTMLImage = HTMLImageTemplate % {'src' : ImgSrcPath, 'AltText' : imageFilename, 'ImageWidth' : Width, 'ImageHeight' : Height}
    HTMLAnchor = HTMLAnchorTemplate % {'href' : ImgSrcPath, 'body' : HTMLImage }

    return HTMLAnchor

def __anchorStringForHeader(Text):
    return '<a id="%(id)s"><b>%(id)s</b></a>' % {'id' : Text}


def HTMLFromTransformNode(ColSubElement, HtmlPaths, **kwargs):
    return '<a href="%s">%s</a>' % (HtmlPaths.GetSubNodeFullPath(ColSubElement), ColSubElement.Name)


def RowReport(RowElement, HTMLPaths, RowLabelAttrib=None, ColumnXPaths=None, Logger=None, **kwargs):
    '''Create HTML to describe an element'''
    if not isinstance(ColumnXPaths, list):
        xpathStrings = str(ColumnXPaths).strip().split(',')
        ColumnXPaths = xpathStrings

    if len(ColumnXPaths) == 0:
        return

    ColumnBodyList = ColumnList()

    if hasattr(RowElement, RowLabelAttrib):
        RowLabel = str(getattr(RowElement, RowLabelAttrib))

    if RowLabel is None:
        RowLabel = str(RowElement)

    # OK, build the columns
    astr = __anchorStringForHeader(RowLabel)
    ColumnBodyList.append(astr)

    ArgSet = ArgumentSet()

    ArgSet.AddArguments(kwargs)
    # CaptionHTML = None
    for ColXPath in ColumnXPaths:

        # ColXPath = ArgSet.SubstituteStringVariables(ColXPath)
        ColSubElements = RowElement.findall(ColXPath)
        # Create a new table inside if len(ColSubElements) > 1?
        for ColSubElement in ColSubElements:

            HTML = None
            if ColSubElement.tag == "Image":
                if ColSubElement.FindParent("ImageSet") is None:
                    kwargs['MaxImageWidth'] = 364
                    kwargs['MaxImageHeight'] = 364
                else:
                    kwargs['MaxImageWidth'] = 448
                    kwargs['MaxImageHeight'] = 448

                HTML = ImgTagFromImageNode(ImageNode=ColSubElement, HtmlPaths=HTMLPaths, Logger=Logger, **kwargs)
            elif ColSubElement.tag == "Data":
                kwargs['MaxImageWidth'] = 364
                kwargs['MaxImageHeight'] = 364

                HTML = HTMLFromDataNode(ColSubElement, HTMLPaths, Logger=Logger, **kwargs)

            elif ColSubElement.tag == "Transform":
                HTML = HTMLFromTransformNode(ColSubElement, HTMLPaths, Logger=Logger, **kwargs)

            elif ColSubElement.tag == "Notes":
                ColumnBodyList.caption = '<caption align=bottom>%s</caption>\n' % HTMLFromNotesNode(ColSubElement, HTMLPaths, Logger=Logger, **kwargs)

            if not HTML is None:
                ColumnBodyList.append(HTML)

    # if not CaptionHTML is None:
    #   ColumnBodyList.caption = '<caption align=bottom>%s</caption>' % CaptionHTML

    return ColumnBodyList

def GenerateTableReport(OutputFile, ReportingElement, RowXPath, RowLabelAttrib=None, ColumnXPaths=None, Logger=None, **kwargs):
    '''Create an HTML table that uses the RowXPath as the root for searches listed under ColumnXPaths
       ColumnXPaths are a list of comma delimited XPath searches.  Each XPath search results in a new column for the row
       Much more sophisticated reports would be possible by building a framework similiar to the pipeline manager, but time'''

    if RowLabelAttrib is None:
        RowLabelAttrib = "Name"

    if not isinstance(ColumnXPaths, list):
        xpathStrings = str(ColumnXPaths).strip().split(',')
        ColumnXPaths = xpathStrings

    if len(ColumnXPaths) == 0:
        return

    RootElement = ReportingElement
    while hasattr(RootElement, 'Parent'):
        if not RootElement.Parent is None:
            RootElement = RootElement.Parent
        else:
            break

    Paths = HTMLPaths(RootElement.FullPath, OutputFile)

    Paths.CreateOutputDirs()

    # OK, start walking the columns.  Then walk the rows
    RowElements = list(ReportingElement.findall(RowXPath))
    if RowElements is None:
        return None

    # Build a 2D list to build the table from later

    pool = Pools.GetGlobalThreadPool()
    tableDict = {}
    tasks = []

    NumRows = len(RowElements)
    for (iRow, RowElement) in enumerate(RowElements):

        if hasattr(RowElement, RowLabelAttrib):
            RowLabel = getattr(RowElement, RowLabelAttrib)

        if RowLabel is None:
            RowLabel = RowElement

        # task = pool.add_task(RowLabel, RowReport, RowElement, RowLabelAttrib=RowLabelAttrib, ColumnXPaths=ColumnXPaths, HTMLPaths=Paths, Logger=Logger, **kwargs)
        # tasks.append(task)
        # task.wait()

        # Threading this caused problems with Matplotlib being called from different threads.  Single threading again for now
        result = RowReport(RowElement, RowLabelAttrib=RowLabelAttrib, ColumnXPaths=ColumnXPaths, HTMLPaths=Paths, Logger=Logger, **kwargs)
        tableDict[RowLabel] = result

    for iRow, t in enumerate(tasks):
        try:
            tableDict[t.name] = t.wait_return()
            nornir_shared.prettyoutput.CurseProgress("Added row", iRow, Total=NumRows)
        except Exception as e:
            tableDict[t.name] = str(e)
            pass

    # HTML = MatrixToTable(RowBodyList=RowBodyList)
    HTML = DictToTable(tableDict)

    CreateHTMLDoc(os.path.join(Paths.OutputDir, Paths.OutputFile), HTMLBody=HTML)
    return None

def CreateHTMLDoc(OutputFile, HTMLBody):
    HTMLHeader = "<!DOCTYPE html> \n" + "<html>\n " + "<body>\n"
    HTMLFooter = "</body>\n" + "</html>\n"

    HTML = HTMLHeader + str(HTMLBody) + HTMLFooter

    if os.path.exists(OutputFile):
        os.remove(OutputFile)

    if not OutputFile is None:
        f = open(OutputFile, 'w')
        f.write(HTML)
        f.close()


def __IndentString(IndentLevel):
    return ' ' * IndentLevel

def __AppendHTML(html, newHtml, IndentLevel):
    html.append(__IndentString(IndentLevel) + newHtml)

def __ValueToTableCell(value, IndentLevel):
    '''Converts a value to a table cell'''
    HTML = HTMLBuilder(IndentLevel)

    if isinstance(value, str):
        HTML.Add('<td valign="top"> ')
        HTML.Add(value)
    elif isinstance(value, dict):
        HTML.Add('<td valign="top">\n')
        HTML.Indent()
        HTML.Add(DictToTable(value, HTML.IndentLevel))
        HTML.Dedent()
    elif isinstance(value, UnorderedItemList):
        HTML.Add('<td valign="top">\n ')
        HTML.Indent()
        HTML.Add(__ListToUnorderedList(value, HTML.IndentLevel))
        HTML.Dedent()
    elif isinstance(value, RowList):
        HTML.Add('<td valign="top">\n ')
        HTML.Indent()
        HTML.Add(__ListToTableRows(value, HTML.IndentLevel))
        HTML.Dedent()
    elif isinstance(value, list):
        HTML.Add('<td valign="top">\n ')
        HTML.Indent()
        HTML.Add(__ListToTableColumns(value, HTML.IndentLevel))
        HTML.Dedent()
    else:
        HTML.Add("Unknown type passed to __ValueToHTML")


    HTML.Add("</td>\n")

    return HTML


def __ListToTableColumns(listColumns, IndentLevel):
    '''Convert a list to a set of <tf> columns in a table'''

    HTML = HTMLBuilder(IndentLevel)

    HTML.Add("<table>\n")
    HTML.Indent()
    HTML.Add("<tr>\n")
    HTML.Indent()

    for entry in listColumns:
        HTML.Add(__ValueToTableCell(entry, HTML.IndentLevel))

    HTML.Dedent()
    HTML.Add("</tr>\n")

    if hasattr(listColumns, 'caption'):
        HTML.Add(listColumns.caption)

    HTML.Dedent()
    HTML.Add("</table>\n")

    return HTML


def __ListToTableRows(listColumns, IndentLevel):
    '''Convert a list to a set of <tf> columns in a table'''

    HTML = HTMLBuilder(IndentLevel)

    HTML.Add("<table>\n")
    HTML.Indent()

    for entry in listColumns:
        HTML.Add("<tr>\n")
        HTML.Indent()

        HTML.Add(__ValueToTableCell(entry, HTML.IndentLevel))

        HTML.Dedent()
        HTML.Add("</tr>\n")

    if hasattr(listColumns, 'caption'):
        HTML.Add(listColumns.caption)

    HTML.Dedent()
    HTML.Add("</table>\n")

    return HTML



def __ListToUnorderedList(listEntries, IndentLevel):
    '''Convert a list to a set of <tf> columns in a table'''

    HTML = HTMLBuilder(IndentLevel)

    HTML.Add("<ul>\n")
    HTML.Indent()

    for entry in listEntries:
        HTML.Add('<li>' + str(entry) + '</li>\n', HTML.IndentLevel)

    HTML.Dedent()
    HTML.Add("</ul>\n")

    return HTML



def DictToTable(RowDict=None, IndentLevel=None):

    HTML = HTMLBuilder(IndentLevel)

    if IndentLevel is None:
        HTML.Add('<table border="border">\n')
    else:
        HTML.Add("<table>\n")
    HTML.Indent()

    keys = RowDict.keys()
    keys.sort(reverse=True)

    for row in keys:
        value = RowDict[row]

        HTML.Add('<tr>\n')
        HTML.Indent()


        HTML.Add(__ValueToTableCell(value, HTML.IndentLevel))

        HTML.Dedent()
        HTML.Add("</tr>\n")

    if hasattr(RowDict, 'caption'):
        HTML.Add(RowDict.caption)

    HTML.Dedent()
    HTML.Add("</table>\n")



    return HTML


def MatrixToTable(RowBodyList=None, IndentLevel=None):
    '''Convert a list of lists containing HTML fragments into a table'''

    if IndentLevel is None:
        IndentLevel = 0

    HTML = ' ' * IndentLevel + "<table>\n"


    for columnList in RowBodyList:
        HTML = HTML + ' ' * IndentLevel + '<tr>\n'

        IndentLevel = IndentLevel + 1



        if isinstance(columnList, str):
            HTML = HTML + '<td>'
            HTML = HTML + columnList
            HTML = HTML + "</td>\n"
        else:
            FirstColumn = True
            for column in columnList:
                HTML = HTML + ' ' * IndentLevel
                if FirstColumn:
                    HTML = HTML + '<td valign="top">'
                    FirstColumn = False
                else:
                    HTML = HTML + '<td align="left">'

                HTML = HTML + column
                HTML = HTML + "</td>\n"

        IndentLevel = IndentLevel - 1
        HTML = HTML + ' ' * IndentLevel + "</tr>\n"

    HTML = HTML + ' ' * IndentLevel + "</table>\n"

    return HTML

def GenerateImageReport(xpaths, VolumeElement, Logger, OutputFile=None, **kwargs):

    if(OutputFile is None):
        OutputFile = os.path.join(VolumeElement.FullPath, 'Report.html')

    if isinstance(xpaths, str):
        xpaths = [xpaths]

    if not isinstance(xpaths, list):
        xpathStrings = str(xpaths).strip().split(',')
        requiredFiles = list()
        for fileStr in  xpathStrings:
            requiredFiles.append(fileStr)

    # OK, build a tree recursively composed of matches to the xpath strings
    Dictionary = dict()
    HTMLString = RecursiveReportGenerator(VolumeElement, xpaths, Logger)

    print HTMLString

def RecursiveReportGenerator(VolumeElement, xpaths, Logger=None):
    List = []
    for xpath in xpaths:
        MatchingChildren = VolumeElement.findall(xpath)
        if(len(MatchingChildren) == 0):
            continue

        for element in MatchingChildren:
            if not hasattr(element, 'FullPath'):
                Logger.warning('No fullpath property on element: ' + str(element))
                continue

            Name = None
            if hasattr(element, 'Name'):
                Name = element.Name
            else:
                Name = element.GetAttribFromParent('Name')

            if Name is None:
                Logger.warning('No name property on element: ' + str(element))
                continue

            List.append((element.tag, Name, element.FullPath, element))

    for element in VolumeElement:
        childList = RecursiveReportGenerator(element, xpaths, Logger.getChild('element.Name'))
        if(len(childList) > 0):
            List.append((element.tag, element.Name, childList))

    return List
