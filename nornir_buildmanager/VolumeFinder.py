# This is to find all the Viking XML paths on a drive and create an HTML page to view statistics and link to volumes.

import glob
import os

import nornir_imageregistration.core
import nornir_shared.files
import nornir_shared.images

import nornir_shared.prettyoutput as PrettyOutput
import xml.etree.ElementTree as ElementTree


HTMLImageTemplate = '<img src="%(src)s" alt="%(AltText)s" width="%(ImageWidth)s" height="%(ImageHeight)s" />'

def FindServerFromAboutXML(path, sourceXML=None):
    if sourceXML is None:
        sourceXML = "About.xml"

    if(path is None or  len(path) == 0):
        return None

    [Parent, tail] = os.path.split(path)

    if(tail is None or len(tail) == 0):
        return None

    AboutXMLPath = os.path.join(path, sourceXML)
    if os.path.exists(AboutXMLPath):
        AboutXML = ElementTree.parse(AboutXMLPath)
        ServerNode = AboutXML.getroot()
        if 'host' in ServerNode.attrib:
            hostname = ServerNode.attrib['host']
            # Cut off the directory path above where the hostname is specified
            InterestingPath = path[len(Parent):]
            return hostname

    path = FindServerFromAboutXML(Parent, sourceXML)

    if path is None:
        return ""

    if len(path) == 0:
        return "/"

    if(path[-1] != '/'):
        path = path + '/'
    path = path + tail

    path = path.replace('\\', '/')
    return path

def HTMLTableForImageList(Path, ColumnsForRow, RowOrderList=None, **kwargs):
    '''Returns an HTML table for a dictionary of row names containing a list of image paths.
       If RowOrderList is not none it contains a list of row keys determining the ordering of rows.
       The path parameter string is removed from the src= paths to produce relative paths'''

    assert(isinstance(ColumnsForRow, dict))

    if(RowOrderList is None):
        RowOrderList = ColumnsForRow.keys()
        RowOrderList = RowOrderList.sort()

    assert(isinstance(RowOrderList, list))

    try:
        ImageWidth = int(kwargs.get('ImageWidth', None))
    except:
        ImageWidth = None

    try:
        ImageHeight = int(kwargs.get('ImageHeight', None))
    except:
        ImageHeight = None

    HTMLTableHeader = '<table border ="1">'
    HTMLTableVolumeTemplate = '<tr>' + '<td>%(src)s </td>' + '</tr>'
    HTMLTableRowBegin = '<tr>'
    HTMLTableDirTemplate = '<td>%(RowHeader)s' + '</td>'
    HTMLTableImageTemplate = '<td>%(image)s' + '</td>'
    HTMLTableEndRow = '</tr>'
    HTMLTableFooter = '</table>'

    HTMLString = HTMLTableHeader

    for row in RowOrderList:
        listImages = ColumnsForRow[row]
        HTMLTableDir = HTMLTableDirTemplate % {'RowHeader' : row}
        HTMLString = HTMLString + '\n\t' + HTMLTableRowBegin
        HTMLString = HTMLString + '\n\t\t' + HTMLTableDir
        for imageFullPath in listImages:

            Height = ImageHeight
            Width = ImageWidth
            if ImageWidth is None or ImageHeight is None:
                [Height, Width] = nornir_imageregistration.core.GetImageSize(imageFullPath)

                if Width > 1024 or Height > 1024:
                    MaxVal = max([Width, Height])
                    Divisor = float(MaxVal) / 1024.0
                    Width = int(Width / Divisor)
                    Height = int(Height / Divisor)

            imageFilename = os.path.basename(imageFullPath)
            imageRelativePath = imageFullPath.replace(Path, '')
            if(imageRelativePath[0] == os.sep or
               imageRelativePath[0] == os.altsep):
                imageRelativePath = imageRelativePath[1:]

            HTMLImage = HTMLImageTemplate % {'src' : imageRelativePath, 'AltText' : imageFilename, 'ImageWidth' : Width, 'ImageHeight' : Height}
            HTMLTableImage = HTMLTableImageTemplate % {'image' : HTMLImage}
            HTMLString = HTMLString + '\n\t\t' + HTMLTableImage

        HTMLString = HTMLString + '\n\t' + HTMLTableEndRow

    HTMLString = HTMLString + '\n' + HTMLTableFooter

    return HTMLString





def VolumeFinder(path=None, OutputFile=None, VolumeNode=None, requiredFiles=None, **kwargs):
    '''Expects a 'Path' and 'RequiredFiles' keyword argument'
       produces an HTML index of all volumes under the path'''

    if requiredFiles is None:
        requiredFiles = []

    if(path is None):
        VolumeNode = kwargs.get('VolumeNode', None)
        if VolumeNode is None:
            PrettyOutput.LogErr("Path attribute not found for VolumeFinder")
            return
        else:
            path = VolumeNode.attrib['Path']

    requiredFiles = kwargs.get('RequiredFiles', [])
    if not isinstance(requiredFiles, list):
        RequiredFileStrs = str(requiredFiles).strip().split(',')
        requiredFiles = list()
        for fileStr in  RequiredFileStrs:
            requiredFiles.append(fileStr)


    ServerHostname = FindServerFromAboutXML(path)
    if ServerHostname is None:
        ServerHostname = ""

    dirs = nornir_shared.files.RecurseSubdirectories(path, RequiredFiles=requiredFiles, ExcludeNames="")

    HTMLHeader = "<!DOCTYPE html> \n" + "<html>\n " + "<body>\n"

    HTMLFooter = "</body>\n" + "</html>\n"



    HTMLString = HTMLHeader

    dirDict = {}

    # Make sure required files is a list type if a single string was passed
    if isinstance(requiredFiles, str):
        requiredFiles = [requiredFiles]

    # Seperate out specifically named files from regular expressions
    RegExpPatterns = []
    i = 0
    while i < len(requiredFiles):
        filename = requiredFiles[i]
        if '*' in filename or '?' in filename:
            RegExpPatterns.append(filename)
            requiredFiles.pop(i)
        else:
            i = i + 1

    for directory in dirs:
        # Take the path from the list and find files.
        dirImageList = dirDict.get(directory, [])

        for pattern in RegExpPatterns:
            filesMatchingPattern = glob.glob(os.path.join(directory, pattern))

            for filename in filesMatchingPattern:
                filenameFullPath = os.path.join(directory, filename)
                dirImageList.append(filenameFullPath)  # Should check if it exists maybe

        for filename in requiredFiles:
            filenameFullPath = os.path.join(directory, filename)
            if(os.path.exists(filenameFullPath)):
                dirImageList.append(filenameFullPath)  # Should check if it exists maybe

        dirDict[directory] = dirImageList
        # Remove the path we were passed from the strings
        HTMLString = HTMLString.replace(path, '')

    RowNames = list(dirDict.keys())
    RowNames.sort()
    RowNames.reverse()

    HTMLTable = HTMLTableForImageList(path, dirDict, RowNames, **kwargs)
    HTMLString = HTMLString + HTMLTable + '\n'
    HTMLString = HTMLString + HTMLFooter

    if(len(RowNames) == 0):
        PrettyOutput.Log("Report generator could not find matching files " + str(requiredFiles))
        return ""

    if not OutputFile is None:
        webPageFullPath = os.path.join(path, OutputFile)
        f = open(webPageFullPath, 'w')
        f.write(HTMLString)
        f.close()

    return None  # HTMLString

def EmailIndex(path=None, subject=None, **kwargs):
    import nornir_shared.emaillib

    if(path is None):
        VolumeNode = kwargs.get('ReportingElement', None)
        if VolumeNode is None:
            PrettyOutput.LogErr("Path attribute not found for VolumeFinder")
            return

        while not VolumeNode.Parent is None:
            VolumeNode = VolumeNode.Parent

        path = VolumeNode.attrib['Path']

    ServerHostname = FindServerFromAboutXML(path)
    if ServerHostname is None:
        ServerHostname = ""

    VolumeName = None
    VolumeXMLPath = os.path.join(path, 'Volume.xml')
    if os.path.exists(VolumeXMLPath):
        VolumeXML = ElementTree.parse(VolumeXMLPath)
        VolumeRoot = VolumeXML.getroot()

        if 'Name' in VolumeRoot.attrib:
            VolumeName = VolumeRoot.attrib['Name']

    # Find all of the .html in the root directory
    reports = glob.glob(os.path.join(path, '*.html'))

    if not VolumeName is None:
        if not subject is None:
            subject = VolumeName + ": " + subject
            kwargs['subject'] = subject


    message = 'The following reports are available for this volume\n\n'

    for report in reports:
        RelativeReportPath = report[len(path) + 1:]
        reportURL = ServerHostname + '/' + RelativeReportPath
        message = message + "\n\t" + reportURL

    kwargs['message'] = message

    nornir_shared.emaillib.SendMail(**kwargs)
#            if     then write HTML
#                if there are more options in the list
#                then write HTML for this image
#                list + 1
#            os.path.exists
#            write the HTML to insert it

#    print files


if __name__ == '__main__':

    # path = 'D:\\Data\\rc2_micro_pipeline\\'
    path = 'C:\\Public\\'
    # webPageStr = VolumeFinder(path=path, requiredFiles=['thumbnail_g_51.png','thumbnail_LeveledShadingCorrectedg_51.png','thumbnail_shadingcorrectedg_51.png'])
    webPageStr = VolumeFinder(Path=path, RequiredFiles=['Histogram*.png', 'PruneScores*.png'])

    webPageFullPath = os.path.join(path, 'Index.html')
    f = open(webPageFullPath, 'w')
    f.write(webPageStr)
    f.close()

    print("All Done!")
