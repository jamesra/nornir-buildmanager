'''
Created on Jul 3, 2012

@author: Jamesan
'''

import copy
import functools
import os

import nornir_buildmanager.VolumeManagerETree
from nornir_imageregistration.files import *
from nornir_shared.files import RecurseSubdirectories
import nornir_shared.prettyoutput as prettyoutput
import xml.etree.ElementTree as ETree

ECLIPSE = 'ECLIPSE' in os.environ

def CreateXMLIndex(path, server=None):

    VolumeXMLDirs = RecurseSubdirectories(Path=path, RequiredFiles='Volume.xml')

    for directory in VolumeXMLDirs:

        InputVolumeNode = nornir_buildmanager.VolumeManagerETree.VolumeManager.Load(directory, Create=False)
        if not InputVolumeNode is None:
            CreateVikingXML(VolumeNode=InputVolumeNode)

def CreateVikingXML(StosMapName=None, StosGroupName=None, OutputFile=None, Host=None, **kwargs):
    '''When passed a volume node, creates a VikingXML file'''
    InputVolumeNode = kwargs.get('VolumeNode')
    path = InputVolumeNode.Path

    if OutputFile is None:
        OutputFile = "Volume.VikingXML"

    if not OutputFile.lower().endswith('.vikingxml'):
        OutputFile = OutputFile + ".VikingXML"

    # Create our XML File
    OutputXMLFilename = os.path.join(path, OutputFile)

    # Load the inputXML file and begin parsing

    # Create the root output node
    OutputVolumeNode = ETree.Element('Volume', {'name' : InputVolumeNode.Name,
                                                'num_stos' : '0',
                                                'num_sections' : '0',
                                                'InputChecksum' : InputVolumeNode.Checksum})

    VikingXMLETree = ETree.ElementTree(OutputVolumeNode)

    ParseScale(InputVolumeNode, OutputVolumeNode)
    ParseSections(InputVolumeNode, OutputVolumeNode)
    ParseStos(InputVolumeNode, OutputVolumeNode, StosMapName, StosGroupName)

    OutputXML = ETree.tostring(OutputVolumeNode).decode('utf-8')
    # prettyoutput.Log(OutputXML)

    hFile = open(OutputXMLFilename, 'w')
    hFile.write(OutputXML)
    hFile.close()

    # Walk down to the path from the root directory, merging about.xml's as we go
    Url = RecursiveMergeAboutXML(path, OutputXMLFilename)

    if not Host is None and len(Host) > 0:
        OutputVolumeNode.attrib['host'] = Host

    prettyoutput.Log("Launch string:")
    prettyoutput.Log(Url)
    finalUrl = url_join(Url, OutputFile)
    vikingUrl = "http://connectomes.utah.edu/Software/Viking4/viking.application?" + finalUrl

    prettyoutput.Log(vikingUrl)
    return


def RecursiveMergeAboutXML(path, xmlFileName, sourceXML="About.xml"):

    if(path is None or  len(path) == 0):
        return

    [Parent, tail] = os.path.split(path)

    if(tail is None or len(tail) == 0):
        return

    Url = RecursiveMergeAboutXML(Parent, xmlFileName, sourceXML)

    NewUrl = MergeAboutXML(xmlFileName, os.path.join(path, "About.xml"))
    if NewUrl is not None:
        if len(NewUrl) > 0:
            Url = NewUrl

    return Url

    #####################
#
#    WriteXML(OutputXMLFile, "<?xml version=\"1.0\"?>")
#
#
#
#    OutputXMLFile = open(OutputXMLFilename, "w")
#
#
#
#    StosCount = 0
#    SectionCount = 0
#
#    #Find all the stos files:
#    stosfiles = glob.glob(os.path.join(path, "*.stos"))
#    StosCount = 0
#
#    for f in stosfiles:
#        if(IsStosIncluded(f)):
#            StosCount += 1
#
#
#    #Count all the sections
#    SectionDirs = os.listdir(path)
#    for sectionDir in SectionDirs:
#        #Skip if it contains a .
#        if sectionDir.find('.') > -1:
#            continue
#
#        (SectionNumber, SectionName, Downsample) = ir.GetSectionInfo(sectionDir)
#        if(SectionNumber < 0):
#            continue
#
#        SectionCount = SectionCount + 1
#
#    VolumeTag = "<Volume name=\"" + basename + "\" " + \
#                " num_stos=\"" + str(StosCount) + "\" " + \
#                " num_sections=\"" + str(SectionCount) + "\""
#
#    if(server is not None):
#        VolumeTag = VolumeTag + " path=\"" + str(server) + "\" "
#
#    VolumeTag = VolumeTag + " xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\" xsi:noNamespaceSchemaLocation=\"http://connectomes.utah.edu/VikingXML.xsd\">"
#
#    WriteXML(OutputXMLFile, VolumeTag)
#
#    for sfile in stosfiles:
#        if(IsStosIncluded(sfile)):
#            AddStosTransform(OutputXMLFile, sfile)
#
#    for sectionDir in SectionDirs:
#
#        #Skip if it contains a .
#        if sectionDir.find('.') > -1:
#            continue
#
#        (SectionNumber, SectionName, Downsample) = ir.GetSectionInfo(sectionDir)
#        if(SectionNumber < 0):
#            continue
#
#        prettyoutput.Log(sectionDir)
#        AddSection(OutputXMLFile, sectionDir)
#
#    WriteXML(OutputXMLFile, "</Volume>")
#    OutputXMLFile.close()
#
#    #Walk down to the path from the root directory, merging about.xml's as we go
#    Url = RecursiveMergeAboutXML(path, OutputXMLFilename)
#
#    os.chdir(StartingPath)
#
#    prettyoutput.Log("Launch string:")
#    prettyoutput.Log(Url)
#    finalUrl = url_join(Url, "Volume.VikingXML")
#    vikingUrl = "http://connectomes.utah.edu/Software/Viking4/viking.application?" + finalUrl
#
#    prettyoutput.Log(vikingUrl)
#    return vikingUrl
def ParseScale(InputVolumeNode, OutputVolumeNode):

    ScaleNode = InputVolumeNode.find('Block/Section/Channel/Scale')
    if ScaleNode is None:
        return

    XNode = ScaleNode.find('X')

    OutputSectionNode = ETree.SubElement(OutputVolumeNode, 'Scale', {'UnitsOfMeasure' : XNode.UnitsOfMeasure,
                                                           'UnitsPerPixel' : XNode.UnitsPerPixel})

def ParseStos(InputVolumeNode, OutputVolumeNode, StosMapName, StosGroupName):

    global ECLIPSE

    lastCreated = None
    bestGroup = None

    if StosMapName is None:
        print "No StosMapName specified, not adding stos"
        return
    if StosGroupName is None:
        print "No StosGroupName specified, not adding stos"
        return

    num_stos = 0
    print("Adding Slice-to-slice transforms\n")
    UpdateTemplate = "%(mapped)d -> %(control)d"
    for BlockNode in InputVolumeNode.findall('Block'):
        StosMapNode = BlockNode.GetChildByAttrib("StosMap", 'Name', StosMapName)
        if StosMapNode is None:
            continue

        # StosGroups = BlockNode.findall("StosGroup[@Downsample='16']")
        # if StosGroups is None:
            # continue

        # for StosGroup in StosGroups:

        # if 'Brute' in StosGroup.Name:
            # continue

        StosGroup = BlockNode.GetChildByAttrib("StosGroup", "Name", StosGroupName)
        if StosGroup is None:
            print "StosGroup %s not found.  No slice-to-slice transforms are being included" % StosGroupName
            continue

        for Mapping in StosMapNode.findall('Mapping'):

            for MappedSection in Mapping.Mapped:
                MappingString = UpdateTemplate % {'mapped' : int(MappedSection),
                                                              'control' : int(Mapping.Control)}

                SectionMappingNode = StosGroup.GetChildByAttrib('SectionMappings', 'MappedSectionNumber', MappedSection)
                if SectionMappingNode is None:
                    print "No Section Mapping found for " + MappingString
                    continue

                transform = SectionMappingNode.GetChildByAttrib('Transform', 'ControlSectionNumber', Mapping.Control)
                if transform is None:
                    print "No Section Mapping Transform found for " + MappingString
                    continue

                OutputStosNode = ETree.SubElement(OutputVolumeNode, 'stos', {'GroupName' : StosGroup.Name,
                                                                      'controlSection' : str(transform.ControlSectionNumber),
                                                                      'mappedSection' :  str(transform.MappedSectionNumber),
                                                                      'path' : os.path.join(BlockNode.Path, StosGroup.Path, transform.Path),
                                                                      'pixelspacing' : '%g' % StosGroup.Downsample,
                                                                      'type' : transform.Type })

                UpdateString = UpdateTemplate % {'mapped' : int(transform.MappedSectionNumber),
                                                              'control' : int(transform.ControlSectionNumber)}



                if not ECLIPSE:
                    print('\b' * 80)

                print(UpdateString)

                num_stos = num_stos + 1

    OutputVolumeNode.attrib["num_stos"] = '%g' % num_stos


def ParseSections(InputVolumeNode, OutputVolumeNode):

    # Find all of the section tags
    print("Adding Sections\n")
    for BlockNode in InputVolumeNode.findall('Block'):
        for SectionNode in BlockNode.Sections:

            if not ECLIPSE:
                print('\b' * 8)

            print('%g' % SectionNode.Number)

            # Create a section node, or create on if it doesn't exist
            OutputSectionNode = OutputVolumeNode.find("Section[@Number='%d']" % SectionNode.Number)
            if(OutputSectionNode is None):
                OutputSectionNode = ETree.SubElement(OutputVolumeNode, 'Section', {'Number' : str(SectionNode.Number),
                                                         'Path' : os.path.join(BlockNode.Path, SectionNode.Path),
                                                         'Name' : SectionNode.Name})

            ParseChannels(SectionNode, OutputSectionNode)

            NotesNodes = SectionNode.findall('Notes')
            for NoteNode in NotesNodes:
                # Copy over Notes elements verbatim
                OutputSectionNode.append(copy.deepcopy(NoteNode))

    AllSectionNodes = OutputVolumeNode.findall('Section')
    OutputVolumeNode.attrib['num_sections'] = str(len(AllSectionNodes))

def ParseChannels(SectionNode, OutputSectionNode):

    for ChannelNode in SectionNode.Channels:
        for TransformNode in ChannelNode.findall('Transform'):
            OutputTransformNode = ParseTransform(TransformNode, OutputSectionNode)
            if not OutputTransformNode is None:
                OutputTransformNode.attrib['Path'] = os.path.join(ChannelNode.Path, OutputTransformNode.attrib['Path'])

        for FilterNode in ChannelNode.Filters:
            for tilepyramid in FilterNode.findall('TilePyramid'):
                OutputPyramidNode = ParsePyramidNode(FilterNode, tilepyramid, OutputSectionNode)
                OutputPyramidNode.attrib['Path'] = os.path.join(ChannelNode.Path, FilterNode.Path, OutputPyramidNode.attrib['Path'])
            for tileset in FilterNode.findall('Tileset'):
                OutputTilesetNode = ParseTilesetNode(FilterNode, tileset, OutputSectionNode)
                OutputTilesetNode.attrib['path'] = os.path.join(ChannelNode.Path, FilterNode.Path, OutputTilesetNode.attrib['path'])
                print "Tileset found for section " + str(SectionNode.attrib["Number"])


def ParseTransform(TransformNode, OutputSectionNode):

    mFile = mosaicfile.MosaicFile.Load(TransformNode.FullPath)

    if(mFile is None):
        prettyoutput.LogErr("Unable to load transform: " + TransformNode.FullPath)
        return

    if(mFile.NumberOfImages < 1):
        prettyoutput.LogErr("Not including empty .mosaic file")
        return

    # Figure out what the tile prefix and postfix are for this mosaic file by extrapolating from the first tile filename
    for k in mFile.ImageToTransformString.keys():
        TileFileName = k
        break

    # TileFileName = mFile.ImageToTransformString.keys()[0]

    # Figure out prefix and postfix parts of filenames
    parts = TileFileName.split('.')

    Postfix = parts[len(parts) - 1]

    # Two conventions are commonly used Section#.Tile#.png or Tile#.png
    if(len(parts) == 3):
        Prefix = parts[0]
    else:
        Prefix = ''

    UseForVolume = 'false'
    if('grid' in TransformNode.Name.lower()):
        UseForVolume = 'true'


    TransformName = TransformNode.Name
    if('Type' in TransformNode.attrib):
        TransformName = TransformName + TransformNode.attrib['Type']
    # By default grid.mosaic files are marked as the reference for volume transforms

    return ETree.SubElement(OutputSectionNode, 'Transform', {'FilePostfix' : Postfix,
                                                          'FilePrefix' : Prefix,
                                                         'Path' : TransformNode.Path,
                                                         'Name' : TransformName,
                                                         'UseForVolume' : UseForVolume})

def ParsePyramidNode(FilterNode, InputPyramidNode, OutputSectionNode):
    OutputPyramidNode = ETree.SubElement(OutputSectionNode, 'Pyramid', {
                                                         'Path' : InputPyramidNode.Path,
                                                         'Name' : FilterNode.Name,
                                                         'LevelFormat' : InputPyramidNode.LevelFormat})

    for LevelNode in InputPyramidNode.Levels:
        ETree.SubElement(OutputPyramidNode, 'Level', {'Path' : LevelNode.Path,
                                                      'Downsample' : '%g' % LevelNode.Downsample})

    return OutputPyramidNode

def ParseTilesetNode(FilterNode, InputTilesetNode, OutputSectionNode):
    OutputTilesetNode = ETree.SubElement(OutputSectionNode, 'Tileset', {
                                                         'path' : InputTilesetNode.Path,
                                                         'name' : FilterNode.Name,
                                                         'TileXDim' : str(InputTilesetNode.TileXDim),
                                                         'TileYDim' : str(InputTilesetNode.TileYDim),
                                                         'FilePrefix' : InputTilesetNode.FilePrefix,
                                                         'FilePostfix' : InputTilesetNode.FilePostfix,
                                                         'CoordFormat' : InputTilesetNode.CoordFormat})

    for LevelNode in InputTilesetNode.Levels:
        ETree.SubElement(OutputTilesetNode, 'Level', {'path' : LevelNode.Path,
                                                      'Downsample' : '%g' % LevelNode.Downsample,
                                                      'GridDimX' : str(LevelNode.GridDimX),
                                                      'GridDimY' : str(LevelNode.GridDimY)})

    return OutputTilesetNode



# Merge the created VolumeXML with the general definitions in about.XML
def MergeAboutXML(volumeXML, aboutXML):

    import xml.dom.minidom

    prettyoutput.Log('MergeAboutXML ' + str(volumeXML) + ' ' + str(aboutXML))
    if(os.path.exists(volumeXML) == False):
        return
    if(os.path.exists(aboutXML) == False):
        return

    aboutDom = xml.dom.minidom.parse(aboutXML)
    volumeDom = xml.dom.minidom.parse(volumeXML)

    # Figure out which elements are contained in the about dom which need to be injected into the volumeXML
    # If element names match, attributes are added which are missing from the volumeXML.
    # If element names do not match, they are injected into the volumeXML at the appropriate level

    aboutNode = aboutDom.documentElement
    volumeNode = volumeDom.documentElement

    Url = None
    # Volume path is a special case so we append the path to the host name
    if(volumeNode.nodeName == "Volume" and aboutNode.nodeName == "Volume"):
        baseVolumeDir = os.path.dirname(volumeXML)
        baseAboutDir = os.path.dirname(aboutXML)
        relPath = baseVolumeDir.replace(baseAboutDir, '')
        prettyoutput.Log("Relative path: " + relPath)
        Url = UpdateVolumePath(volumeNode, aboutNode, relPath)



    MergeElements(volumeNode, aboutNode)

    prettyoutput.Log("")
#   print volumeDom.toprettyxml()

    xmlFile = open(volumeXML, "w")
    xmlFile.write(volumeDom.toprettyxml())
    xmlFile.close()

    return Url


def MergeElements(volumeNode, aboutNode):

    if(ElementsEqual(volumeNode, aboutNode)):
        CopyAttributes(volumeNode, aboutNode)
        MergeChildren(volumeNode, aboutNode)


# Both arguments should be matching elements
def MergeChildren(volumeParent, aboutParent):

    aboutElement = aboutParent.firstChild
    while(aboutElement is not None):
        if(aboutElement.nodeName is None):
            break

        volumeElements = volumeParent.getElementsByTagName(aboutElement.nodeName)
        # The volume doesn't have any elements like this.  Add them
        if(volumeElements.length == 0):
            newNode = aboutElement.cloneNode(True)
            prettyoutput.Log('NewNode' + newNode.toxml())
            volumeParent.insertBefore(newNode, volumeParent.firstChild)
        else:
            for volElement in volumeElements:
                MergeElements(volElement, aboutElement)

        aboutElement = aboutElement.nextSibling

# Compare the attributes of two elements and return true if they match
def ElementsEqual(volumeElement, aboutElement):
    if(aboutElement.nodeName != volumeElement.nodeName):
        return False

    # Volume is the root element so it is always a match
    if(aboutElement.nodeName == "Volume"):
        return True

    # Sections only match if their numbers match
    if(aboutElement.nodeName == "Section"):
        aboutNumber = aboutElement.getAttribute("number")
        volNumber = volumeElement.getAttribute("number")
        if(aboutNumber != volNumber):
            return False
        else:
            prettyoutput.Log("Equal:")
            prettyoutput.Log('v: ' + volumeElement.nodeName + ' ' + str(volNumber))
            prettyoutput.Log('a: ' + aboutElement.nodeName + ' ' + str(aboutNumber))
            prettyoutput.Log('')

            return True

    return False

def CopyAttributes(volumeElement, aboutElement):
#   print 'v: ' + volumeElement.toxml()
#   print 'a: ' + aboutElement.toxml()

    if(aboutElement.hasAttributes() == False):
        return

    attributeMap = aboutElement.attributes
    for i in range(0, attributeMap.length):
        attribute = attributeMap.item(i)

        # if(volumeElement.hasAttribute(attribute.name) == False):
        volumeElement.setAttribute(attribute.name, attribute.value)


def UpdateVolumePath(volumeElement, aboutElement, relPath):
    '''
    Special case for updating the root element Volume path
    '''
    if(aboutElement.hasAttributes() == False):
        return

    if(len(relPath) > 0):
        relPath = relPath.lstrip('\\')
        relPath = relPath.lstrip('/')

    attributeMap = aboutElement.attributes
    for i in range(0, attributeMap.length):
        attribute = attributeMap.item(i)

        if(attribute.name == "host"):
            PathURL = url_join(attribute.value, relPath)
            volumeElement.setAttribute("path", PathURL)
            return PathURL

def url_join(*args):
    """Join any arbitrary strings into a forward-slash delimited list.
    Do not strip leading / from first element, nor trailing / from last element."""
    if len(args) == 0:
        return ""

    if len(args) == 1:
        return str(args[0])

    else:
        args = [str(arg).replace("\\", "/") for arg in args]

        work = [args[0]]
        for arg in args[1:]:
            if arg.startswith("/"):
                work.append(arg[1:])
            else:
                work.append(arg)

        joined = functools.reduce(os.path.join, work)

    return joined.replace("\\", "/")


if __name__ == '__main__':
    CreateXMLIndex('D:/Data/RC2_Mini_Pipeline')

    pass
