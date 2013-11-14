'''
Created on Oct 3, 2012

@author: u0490822
'''

import logging
import shutil
import os
import nornir_shared.images
import nornir_pools as Pools
import datetime
import nornir_buildmanager.importers.idoc as idoc
import nornir_shared.plot
import nornir_shared.prettyoutput
from nornir_buildmanager.pipelinemanager import PipelineManager
# import Pipelines.VolumeManagerETree as VolumeManager

if __name__ == '__main__':
    pass


def HTMLBuilder(list):
    '''A list of strings that contain HTML'''
    
    @property
    def IndentLevel(self):
        return self.__IndentLevel
    
    def Indent(self):
        self.__IndentLevel += 1
        
    def Dedent(self):
        self.__IndentLevel += 1
    
    def __init__(self, indentlevel = None):
        super(HTMLBuilder,self).__init__()
        
        if indentlevel is None:
            self.__IndentLevel = 0
        else:  
            self.__IndentLevel = indentlevel
    
    def __IndentString(self, indent):
        
        if indent is None:
            return ''
        
        return ' ' * indent
    
    def Add(self, value):
        if isinstance(value,list):
            self.extend(value)
        elif isinstance(value, str):
            self.append(self.__IndentString(self.IndentLevel) + value)
        
    def __str__(self):
        return ''.join([x for x in self])
    

HTMLImageTemplate = '<img src="%(src)s" alt="%(AltText)s" width="%(ImageWidth)s" height="%(ImageHeight)s" />'
HTMLAnchorTemplate = '<a href="%(href)s">%(body)s</a>'

# We add this to thumbnails and other generated files to prevent name collisions
TempFileSalt = 0

def GetTempFileSaltString():
    global TempFileSalt

    saltString = str(TempFileSalt) + "_"
    TempFileSalt = TempFileSalt + 1

    return saltString

def CopyFiles(DataNode, OutputDir, Move=False, **kwargs):
    if OutputDir is None:
        return

    if not os.path.exists(OutputDir):
        os.makedirs(OutputDir)

    if os.path.exists(DataNode.FullPath):
        shutil.copy(DataNode.FullPath, OutputDir)

def MoveFiles(DataNode, OutputDir, Move=False, **kwargs):
    if OutputDir is None:
        return

    if not os.path.exists(OutputDir):
        os.makedirs(OutputDir)

    if os.path.exists(DataNode.FullPath):
        shutil.move(DataNode.FullPath, OutputDir)

    return None

def ScatterPlotStageDrift(Parameters, DataNode, **kwargs):
    logFilePath = DataNode.FullPath

    DriftImageName = "StageDrift" + DataNode.Name + ".png"

    # ImageNode = DataNode.GetOrAddChildByAttrib(VolumeManager.ImageNode(Path=DriftImageName))

    if os.path.exists(logFilePath):
        Data = idoc.SerialEMLog.Load(logFilePath)

        lines = []
        maxdrift = None
        for t in Data.tileData.values():
            if not (t.dwellTime is None or t.drift is None):
                time = []
                drift = []

                for s in t.driftStamps:
                    time.append(s[0])
                    drift.append(s[1])

                maxdrift = max(maxdrift, t.driftStamps[-1][1])
                lines.append((time, drift))

        nornir_shared.plot.PolyLine(lines, Title="Stage settle time, max drift %g" % maxdrift, XAxisLabel='Dwell time (sec)', YAxisLabel="Drift (nm/sec)", OutputFilename=ThumbnailOutputFullPath)


def HTMLFromNotesNode(DataNode, RelPath, **kwargs):
    notesFilePath = DataNode.FullPath
    if os.path.exists(notesFilePath):
        NotesSrcFullPath = os.path.join(RelPath, DataNode.Path)
        return HTMLAnchorTemplate % {'href' : NotesSrcFullPath, 'body' : "Notes" }
    else:
        return None


def HTMLFromLogDataNode(DataNode, ThumbnailDirectory, RelPath, ThumbnailDirectoryRelPath, MaxImageWidth=None, MaxImageHeight=None, **kwargs):

    if MaxImageWidth is None:
        MaxImageWidth = 1024

    if MaxImageHeight is None:
        MaxImageHeight = 1024

    if not DataNode.Name == 'Log':
        return None

    TableEntries = []
    if 'AverageTileDrift' in DataNode.attrib:
        TableEntries.append(['Average tile drift:', '%.3g nm/sec' % float(DataNode.AverageTileDrift)])
        
    if 'MinTileDrift' in DataNode.attrib:
        TableEntries.append(['Min tile drift:', '%.3g nm/sec' % float(DataNode.MinTileDrift)])
        
    if 'MaxTileDrift' in DataNode.attrib:
        TableEntries.append(['Max tile drift:', '%.3g nm/sec' % float(DataNode.MaxTileDrift)])

    if 'AverageTileTime' in DataNode.attrib:
        TableEntries.append(['Average tile time:', '%.3g' % float(DataNode.AverageTileTime)])
        
    if 'FastestTileTime' in DataNode.attrib:
        dtime = datetime.timedelta(seconds=float(DataNode.CaptureTime))
        TableEntries.append(['Fastest tile time:', str(dtime)])

    if 'CaptureTime' in DataNode.attrib:
        dtime = datetime.timedelta(seconds=float(DataNode.CaptureTime))
        TableEntries.append(['Total capture time:', str(dtime)])
        
    logFilePath = DataNode.FullPath
    if os.path.exists(logFilePath):
        
        Data = idoc.SerialEMLog.Load(logFilePath)
        
        TPool = Pools.GetGlobalMultithreadingPool()

        LogSrcFullPath = os.path.join(RelPath, DataNode.Path)
 
        DriftSettleThumbnailFilename = GetTempFileSaltString() + "DriftSettle.png"
        DriftSettleImgSrcPath = os.path.join(ThumbnailDirectoryRelPath, DriftSettleThumbnailFilename)
        DriftSettleThumbnailOutputFullPath = os.path.join(ThumbnailDirectory, DriftSettleThumbnailFilename)

        TPool.add_task(DriftSettleThumbnailFilename, idoc.PlotDriftSettleTime(Data, DriftSettleThumbnailOutputFullPath))
        
        DriftGridThumbnailFilename = GetTempFileSaltString() + "DriftGrid.png"
        DriftGridImgSrcPath = os.path.join(ThumbnailDirectoryRelPath, DriftGridThumbnailFilename)
        DriftGridThumbnailOutputFullPath = os.path.join(ThumbnailDirectory, DriftGridThumbnailFilename)
        
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

        TableEntries.append(['Number of Tiles', str(Data.NumTiles)])
                # PlotHistogram.PolyLinePlot(lines, Title="Stage settle time, max drift %g" % maxdrift, XAxisLabel='Dwell time (sec)', YAxisLabel="Drift (nm/sec)", OutputFilename=ThumbnailOutputFullPath)
        HTMLDriftSettleImage = HTMLImageTemplate % {'src' : DriftSettleImgSrcPath, 'AltText' : 'Drift scatterplot', 'ImageWidth' : MaxImageWidth, 'ImageHeight' : MaxImageHeight}
        HTMLDriftSettleAnchor = HTMLAnchorTemplate % {'href' : DriftSettleImgSrcPath, 'body' : HTMLDriftSettleImage }
        
        HTMLDriftGridImage = HTMLImageTemplate % {'src' : DriftGridImgSrcPath, 'AltText' : 'Drift scatterplot', 'ImageWidth' : MaxImageWidth, 'ImageHeight' : MaxImageHeight}
        HTMLDriftGridAnchor = HTMLAnchorTemplate % {'href' : DriftGridImgSrcPath, 'body' : HTMLDriftGridImage }

        TableEntries.append(HTMLAnchorTemplate % {'href' : LogSrcFullPath, 'body' : "Log File" })
        TableEntries.append(HTMLDriftSettleAnchor)
        TableEntries.append(HTMLDriftGridAnchor)



    if len(TableEntries) == 0:
        return None

    HTML = MatrixToTable(TableEntries)
    return HTML


def AddImageToTable(TableEntries, ThumbnailDirectoryRelPath, ThumbnailDirectory, DriftSettleThumbnailFilename):
    DriftSettleThumbnailFilename = GetTempFileSaltString() + "DriftSettle.png"
    DriftSettleImgSrcPath = os.path.join(ThumbnailDirectoryRelPath, DriftSettleThumbnailFilename)
    DriftSettleThumbnailOutputFullPath = os.path.join(ThumbnailDirectory, DriftSettleThumbnailFilename)

    


def ImgTagFromImageNode(ImageNode, ThumbnailDirectory, RelPath, ThumbnailDirectoryRelPath, MaxImageWidth=None, MaxImageHeight=None, Logger=None, **kwargs):
    '''Create the HTML to display an image with an anchor to the full image.
       If specified RelPath should be added to the elements path for references in HTML instead of using the fullpath attribute'''

    assert(not ImageNode is None)
    assert(not Logger is None)
    if MaxImageWidth is None:
        MaxImageWidth = 1024

    if MaxImageHeight is None:
        MaxImageHeight = 1024

    imageFilename = ImageNode.Path

    FullImgSrcPath = ImageNode.FullPath
    if not os.path.exists(FullImgSrcPath):
        Logger.error("Missing image file: " + FullImgSrcPath)
        return ""


    if not RelPath is None:
        FullImgSrcPath = os.path.join(RelPath, ImageNode.Path)
        if(FullImgSrcPath[0] == os.sep or
           FullImgSrcPath[0] == os.altsep):
            FullImgSrcPath = FullImgSrcPath[1:]

    ImgSrcPath = FullImgSrcPath

    [Width, Height] = nornir_shared.images.GetImageSize(ImageNode.FullPath)

    # Create a thumbnail if needed
    if Width > MaxImageWidth or Height > MaxImageHeight:
        Scale = max(float(Width) / MaxImageWidth, float(Height) / MaxImageHeight)
        Scale = 1 / Scale

        if not os.path.exists(ThumbnailDirectory):
            os.makedirs(ThumbnailDirectory)

        ThumbnailFilename = GetTempFileSaltString() + ImageNode.Path
        ImgSrcPath = os.path.join(ThumbnailDirectoryRelPath, ThumbnailFilename)

        ThumbnailOutputFullPath = os.path.join(ThumbnailDirectory, ThumbnailFilename)

        cmd = "Convert " + ImageNode.FullPath + " -resize " + str(Scale * 100) + "% " + ThumbnailOutputFullPath
        Pool = Pools.GetGlobalProcessPool()
        Pool.add_task(cmd, cmd + " && exit", shell=True)

        Width = int(Width * Scale)
        Height = int(Height * Scale)

    HTMLImage = HTMLImageTemplate % {'src' : ImgSrcPath, 'AltText' : imageFilename, 'ImageWidth' : Width, 'ImageHeight' : Height}
    HTMLAnchor = HTMLAnchorTemplate % {'href' : FullImgSrcPath, 'body' : HTMLImage }

    return HTMLAnchor

def GenerateTableReport(OutputFile, ReportingElement, RowXPath, RowLabelAttrib=None, ColumnXPaths=None, Logger=None, **kwargs):
    '''Create an HTML table that uses the RowXPath as the root for searches listed under ColumnXPaths
       ColumnXPaths are a list of comma delimited XPath searches.  Each XPath search results in a new column for the row
       Much more sophisticated reports would be possible by building a framework similiar to the pipeline manager, but time'''

    if(OutputFile is None):
        OutputFile = os.path.join(ReportingElement.FullPath, 'Report.html')

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

    OutputPath = os.path.dirname(OutputFile)
    if OutputPath is None:
        OutputPath = RootElement.FullPath
        OutputFile = os.path.join(OutputPath, OutputFile)
    elif len(OutputPath) == 0:
        OutputPath = RootElement.FullPath
        OutputFile = os.path.join(OutputPath, OutputFile)

    # Determine the directory to use if images require thumbnails
    ThumbnailDirectory = os.path.basename(OutputFile)
    (ThumbnailDirectory, ext) = os.path.splitext(ThumbnailDirectory)
    ThumbnailDirectoryFullPath = os.path.join(OutputPath, ThumbnailDirectory)

    # OK, start walking the columns.  Then walk the rows
    RowElements = list(ReportingElement.findall(RowXPath))
    if RowElements is None:
        return None

    # Build a 2D list to build the table from later

    RowBodyList = []
    NumRows = len(RowElements)
    iRow = 0
    for RowElement in RowElements:
        ColumnBodyList = []

        nornir_shared.prettyoutput.CurseProgress("Adding row", iRow, Total=NumRows)

        if hasattr(RowElement, RowLabelAttrib):
            RowLabel = str(getattr(RowElement, RowLabelAttrib))

        if RowLabel is None:
            RowLabel = str(RowElement)

        # OK, build the columns
        ColumnBodyList.append('<a id="%(id)s"><b>%(id)s</b></a>' % {'id' : RowLabel})
        for ColXPath in ColumnXPaths:

            ColXPath = PipelineManager.SubstituteStringVariables(ColXPath, kwargs)
            ColSubElements = RowElement.findall(ColXPath)
            # Create a new table inside if len(ColSubElements) > 1?
            for ColSubElement in ColSubElements:

                RelativePath = ColSubElement.FullPath.replace(RootElement.Path, '')
                RelativePath = os.path.dirname(RelativePath)
                if(RelativePath[0] == os.sep or
                   RelativePath[0] == os.altsep):
                    RelativePath = RelativePath[1:]

                HTML = ""
                if ColSubElement.tag == "Image":
                    if not 'assemble' in ColXPath:
                        kwargs['MaxImageWidth'] = 364
                        kwargs['MaxImageHeight'] = 364
                    else:
                        kwargs['MaxImageWidth'] = 512
                        kwargs['MaxImageHeight'] = 512

                    HTML = ImgTagFromImageNode(ImageNode=ColSubElement, ThumbnailDirectory=ThumbnailDirectoryFullPath, ThumbnailDirectoryRelPath=ThumbnailDirectory, RelPath=RelativePath, Logger=Logger, **kwargs)
                elif ColSubElement.tag == "Data":
                    kwargs['MaxImageWidth'] = 364
                    kwargs['MaxImageHeight'] = 364
                    HTML = HTMLFromLogDataNode(ColSubElement, ThumbnailDirectory=ThumbnailDirectoryFullPath, ThumbnailDirectoryRelPath=ThumbnailDirectory, RelPath=RelativePath, Logger=Logger, **kwargs)

                elif ColSubElement.tag == "Notes":
                    HTML = HTMLFromNotesNode(ColSubElement, RelPath=RelativePath, **kwargs)

                if not HTML is None:
                    ColumnBodyList.append(HTML)

        RowBodyList.append(ColumnBodyList)

        iRow = iRow + 1

    HTML = MatrixToTable(RowBodyList=RowBodyList)

    CreateHTMLDoc(OutputFile, HTMLBody=HTML)
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
    return ' '  * IndentLevel

def __AppendHTML(html, newHtml, IndentLevel):
    html.append(__IndentString(IndentLevel) + newHtml)
    
def __ValueToTableCell(value, IndentLevel):
    '''Converts a value to a table cell'''
    HTML = HTMLBuilder(IndentLevel)
     
    if isinstance(value, str):
        HTML.Add('<td valign="top"> ')
        HTML.Add(value)
    elif isinstance(value, dict):
        HTML.Add('<td valign="left">\n')
        HTML.Indent()
        HTML.Add(DictToTable(value, IndentLevel))
        HTML.Dedent() 
    elif isinstance(value, list):
        HTML.Add('<td valign="left">\n ')
        HTML.Indent()
        HTML.Add(__ListToTableColumns(value, IndentLevel))
        HTML.Dedent()
    else:
        HTML.Add("Unknown type passed to __ValueToHTML")
        
    
    HTML.Add("</td>\n") 
        
    return HTML
        
        
def __ListToTableColumns(listColumns, IndentLevel):
    '''Convert a list to a set of <tf> columns in a table'''
    
    HTML = HTMLBuilder(IndentLevel)
    
    for entry in listColumns:
        HTML.Add(__ValueToTableCell(entry, HTML.IndentLevel))
    
    return HTML
        
    

def DictToTable(RowDict=None, IndentLevel=None):
    
    HTML = HTMLBuilder(IndentLevel)
        
    HTML.Add("<table>\n")
    HTML.Indent()
    
    keys = RowDict.keys()
    keys.sort()
    
    for row in keys:
        value = RowDict[row]
        
        HTML.Add('<tr>\n')
        HTML.Indent()
        
        HTML.Add(__ValueToTableCell(value), HTML.IndentLevel)
        
        HTML.Dedent()
        HTML.Add("</tr>\n")
        
    HTML.Add("</table>\n")
    
    HTML.Dedent()
    
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
            HTML = HTML + '<td valign="top">'
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
