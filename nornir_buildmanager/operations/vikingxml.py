"""
Created on Jul 3, 2012

@author: Jamesan
"""

import functools
import os
import zipfile

import nornir_buildmanager.volumemanager
from nornir_buildmanager.progress import report_iterate, report_iterate_complete
from nornir_imageregistration.files import *
from nornir_shared.files import RecurseSubdirectoriesGenerator
import nornir_shared.prettyoutput as prettyoutput
import xml.etree.ElementTree as ETree

ECLIPSE = 'ECLIPSE' in os.environ


def _normalize_stos_group_names(stos_group_name: str | list[str] | None) -> list[str]:
    """Coerce a single group name or CLI append list into a list of names."""
    if stos_group_name is None:
        return []
    if isinstance(stos_group_name, str):
        return [stos_group_name]
    return [name for name in stos_group_name if name]


def _stos_group_zip_relpath(block_node, stos_group) -> str:
    """Return volume-relative zip path ``{Block.Path}/{StosGroup.Name}.zip``."""
    return os.path.join(block_node.Path, f'{stos_group.Name}.zip')


def _stos_group_zip_fullpath(block_node, stos_group) -> str:
    """Return on-disk zip path under the block directory."""
    return os.path.join(block_node.FullPath, f'{stos_group.Name}.zip')


def _pending_stos_members(pending: list[tuple]) -> list[tuple[str, str]]:
    """Build ``(source_fullpath, arcname)`` for pending transforms that exist on disk.

    Arcnames are the ``.stos`` basename only (archive root, no subfolders) so Viking
    can open members by the same name as ``stos/@path``.
    """
    members: list[tuple[str, str]] = []
    for _block_node, _stos_group, transform in pending:
        source = transform.FullPath
        arcname = os.path.basename(transform.Path)
        if not os.path.isfile(source):
            prettyoutput.Log(f"Skipping missing .stos for VikingXML zip: {source}")
            continue
        members.append((source, arcname))
    return members


def _normalize_zip_member_name(name: str) -> str:
    """Normalize zip member paths for set comparison across platforms."""
    return name.replace('\\', '/')


def _stos_group_zip_is_fresh(zip_fullpath: str, members: list[tuple[str, str]]) -> bool:
    """True when zip exists, contains exactly *members*, and is not older than any source."""
    if not members or not os.path.isfile(zip_fullpath):
        return False

    zip_mtime = os.path.getmtime(zip_fullpath)
    expected = set()
    for source, arcname in members:
        if os.path.getmtime(source) > zip_mtime:
            return False
        expected.add(_normalize_zip_member_name(arcname))

    try:
        with zipfile.ZipFile(zip_fullpath, 'r') as archive:
            actual = {_normalize_zip_member_name(name) for name in archive.namelist()}
    except zipfile.BadZipFile:
        return False

    return actual == expected


def _write_stos_group_zip(block_node, stos_group, pending: list[tuple]) -> str | None:
    """Write or reuse ``{Block}/{StosGroup.Name}.zip`` for StosMap-filtered pending transforms.

    Returns the volume-relative zip path on success, or None when nothing was packaged.
    Skips rewrite when the existing zip's mtime and member set already match sources.
    """
    members = _pending_stos_members(pending)
    if not members:
        return None

    zip_fullpath = _stos_group_zip_fullpath(block_node, stos_group)
    zip_relpath = _stos_group_zip_relpath(block_node, stos_group)
    os.makedirs(block_node.FullPath, exist_ok=True)

    if _stos_group_zip_is_fresh(zip_fullpath, members):
        prettyoutput.Log(f"VikingXML zip up to date: {zip_relpath}")
        return zip_relpath

    prettyoutput.Log(f"Writing VikingXML zip (stale or membership changed): {zip_relpath}")
    tmp_path = zip_fullpath + '.tmp'
    try:
        with zipfile.ZipFile(tmp_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for source, arcname in members:
                archive.write(source, arcname=arcname)
        os.replace(tmp_path, zip_fullpath)
    except Exception:
        if os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise

    return zip_relpath


def CreateXMLIndex(path, server=None):
    VolumeXMLDirs = RecurseSubdirectoriesGenerator(Path=path, RequiredFiles='Volume.xml')

    for directory in VolumeXMLDirs:

        InputVolumeNode = nornir_buildmanager.volumemanager.VolumeManager.Load(directory.path, Create=False)
        if not InputVolumeNode is None:
            CreateVikingXML(VolumeNode=InputVolumeNode)


def CreateVikingXML(StosMapName=None, StosGroupName: str | list[str] | None = None, OutputFile=None, Host=None,
                    **kwargs):
    """When passed a volume node, creates a VikingXML file"""
    InputVolumeNode = kwargs.get('VolumeNode')
    if InputVolumeNode is None:
        raise ValueError("VolumeNode is required")
    path = InputVolumeNode.Path

    if OutputFile is None:
        OutputFile = "Volume.VikingXML"

    if not OutputFile.lower().endswith('.vikingxml'):
        OutputFile += ".VikingXML"

    # Create our XML File
    OutputXMLFilename = os.path.join(path, OutputFile)

    # Create the root output node (Version 2: nested Sections / StosGroup)
    OutputVolumeNode = ETree.Element('Volume', {'Version': '2',
                                                'Name': InputVolumeNode.Name,
                                                'num_stos': '0',
                                                'num_sections': '0',
                                                'InputChecksum': InputVolumeNode.Checksum})

    (units_of_measure, units_per_pixel) = DetermineVolumeScale(InputVolumeNode)
    if units_of_measure is not None:
        AddScaleData(OutputVolumeNode, units_of_measure, units_per_pixel)

    ParseSections(InputVolumeNode, OutputVolumeNode)

    RemoveDuplicateScaleEntries(OutputVolumeNode, units_of_measure, units_per_pixel)

    ParseStos(InputVolumeNode, OutputVolumeNode, StosMapName, StosGroupName)

    ETree.indent(OutputVolumeNode, space='  ')
    OutputXML = ETree.tostring(OutputVolumeNode, encoding='unicode')

    with open(OutputXMLFilename, 'w', encoding='utf-8') as hFile:
        hFile.write(OutputXML)

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
    if (path is None or len(path) == 0):
        return

    [Parent, tail] = os.path.split(path)

    if (tail is None or len(tail) == 0):
        return

    Url = RecursiveMergeAboutXML(Parent, xmlFileName, sourceXML)

    NewUrl = MergeAboutXML(xmlFileName, os.path.join(path, "About.xml"))
    if NewUrl is not None:
        if len(NewUrl) > 0:
            Url = NewUrl

    return Url


def DetermineVolumeScale(InputVolumeNode):
    """
    Returns the highest resolution found in all sections of the volume
    """

    ScaleNodes = list(InputVolumeNode.findall('Block/Section/Channel/Scale'))
    if ScaleNodes is None or len(ScaleNodes) == 0:
        return (None, None)

    # This code assumes all units are the same
    units_of_measure = [s.X.UnitsOfMeasure for s in ScaleNodes]

    units_all_equal = all(x == units_of_measure[0] for x in units_of_measure)
    if not units_all_equal:
        raise AssertionError("Not all units are equal in the volume.  This can be supported, but is not added yet.")

    UnitsPerPixel = [s.X.UnitsPerPixel for s in ScaleNodes]

    min_UnitsPerPixel = min(UnitsPerPixel)

    return (units_of_measure[0], min_UnitsPerPixel)


def AddScaleData(OutputNode, units_of_measure, units_per_pixel):
    """Adds a scale node to the OutputNode"""

    OutputScaleNode = ETree.SubElement(OutputNode, 'Scale', {'UnitsOfMeasure': str(units_of_measure),
                                                             'UnitsPerPixel': str(units_per_pixel)})

    return OutputScaleNode


def AddChannelScale(InputChannelNode, OutputNode):
    """
    Add scale element to node based on the channel's scale information
    """

    ScaleNode = InputChannelNode.GetScale()
    if ScaleNode is None:
        return

    AddScaleData(OutputNode, ScaleNode.X.UnitsOfMeasure, ScaleNode.X.UnitsPerPixel)


def RemoveDuplicateScaleEntries(OutputNode, volume_units_of_measure, volume_units_per_pixel):
    """Remove scale elements that match the volume's default scale"""

    for subelem in OutputNode:
        scale_node = subelem.find('Scale')
        if scale_node is None:
            RemoveDuplicateScaleEntries(subelem, volume_units_of_measure, volume_units_per_pixel)
        else:
            # Determine if the scales match
            elem_units_of_measure = scale_node.attrib['UnitsOfMeasure']
            elem_units_per_pixel = float(scale_node.attrib['UnitsPerPixel'])

            if elem_units_of_measure == volume_units_of_measure and volume_units_per_pixel == elem_units_per_pixel:
                subelem.remove(scale_node)

            continue


def ParseStos(InputVolumeNode, OutputVolumeNode, StosMapName, StosGroupName: str | list[str] | None):
    """Parse all stos transforms from the volume and add them under ``StosGroup`` elements."""
    if StosMapName is None:
        prettyoutput.Log("No StosMapName specified, not adding stos")
        return

    stos_group_names = _normalize_stos_group_names(StosGroupName)
    if not stos_group_names:
        prettyoutput.Log("No StosGroupName specified, not adding stos")
        return

    num_stos = 0
    prettyoutput.Log("Adding Slice-to-slice transforms\n")
    for stos_group_name in stos_group_names:
        num_stos += _parse_stos_for_group(InputVolumeNode, OutputVolumeNode, StosMapName, stos_group_name)

    OutputVolumeNode.attrib["num_stos"] = '%g' % num_stos


def _parse_stos_for_group(InputVolumeNode, OutputVolumeNode, StosMapName: str, StosGroupName: str) -> int:
    """Add a ``StosGroup`` with nested ``stos`` children; returns the number of stos entries added."""
    num_stos = 0
    UpdateTemplate = "%(mapped)d -> %(control)d"

    # Pre-collect all pending transform records so we can report accurate totals.
    pending: list[tuple] = []
    group_meta: tuple | None = None  # (BlockNode, StosGroup) for zip / Name
    for BlockNode in InputVolumeNode.findall('Block'):
        StosMapNode = BlockNode.GetChildByAttrib("StosMap", 'Name', StosMapName)
        if StosMapNode is None:
            continue

        StosGroup = BlockNode.GetChildByAttrib("StosGroup", "Name", StosGroupName)
        if StosGroup is None:
            prettyoutput.Log("StosGroup %s not found.  No slice-to-slice transforms are being included" % StosGroupName)
            continue

        if group_meta is None:
            group_meta = (BlockNode, StosGroup)

        for Mapping in StosMapNode.findall('Mapping'):
            for MappedSection in Mapping.Mapped:
                SectionMappingNode = StosGroup.GetChildByAttrib('SectionMappings', 'MappedSectionNumber', MappedSection)
                if SectionMappingNode is None:
                    prettyoutput.Log("No Section Mapping found for " +
                                     UpdateTemplate % {'mapped': int(MappedSection), 'control': int(Mapping.Control)})
                    continue

                transform = SectionMappingNode.GetChildByAttrib('Transform', 'ControlSectionNumber', Mapping.Control)
                if transform is None:
                    prettyoutput.Log("No Section Mapping Transform found for " +
                                     UpdateTemplate % {'mapped': int(MappedSection), 'control': int(Mapping.Control)})
                    continue

                pending.append((BlockNode, StosGroup, transform))

    if not pending:
        return 0

    assert group_meta is not None
    block_for_zip, stos_group_for_name = group_meta
    group_attribs = {'Name': stos_group_for_name.Name}
    zip_relpath = _write_stos_group_zip(block_for_zip, stos_group_for_name, pending)
    if zip_relpath is not None:
        group_attribs['zip'] = zip_relpath
    output_group = ETree.SubElement(OutputVolumeNode, 'StosGroup', group_attribs)

    total_stos = len(pending)
    track_id = f"vikingxml:stos:{StosGroupName}"
    label = f"Stos {StosGroupName}"
    if total_stos:
        report_iterate(track_id, 0, total_stos, label, depth=1)

    try:
        for BlockNode, StosGroup, transform in pending:
            # Basename only: matches zip members at archive root (no subfolders).
            stos_path = os.path.basename(transform.Path)
            pair_label = UpdateTemplate % {
                'mapped': int(transform.SourceSectionNumber),
                'control': int(transform.TargetSectionNumber),
            }
            ETree.SubElement(output_group, 'stos', {
                'controlSection': str(transform.TargetSectionNumber),
                'mappedSection': str(transform.SourceSectionNumber),
                'path': stos_path,
                'pixelspacing': '%g' % StosGroup.Downsample,
                'type': transform.Type,
            })

            prettyoutput.Log(pair_label)
            num_stos += 1
            if total_stos:
                report_iterate(
                    track_id,
                    num_stos,
                    total_stos,
                    label,
                    depth=1,
                    element=pair_label,
                    path=stos_path,
                    section=int(transform.SourceSectionNumber),
                )
    finally:
        if total_stos:
            report_iterate_complete(track_id, total_stos)

    return num_stos


def ParseSections(InputVolumeNode, OutputVolumeNode):
    """Parse all sections from the volume and add them under a ``Sections`` wrapper."""
    sections_parent = ETree.SubElement(OutputVolumeNode, 'Sections')

    section_jobs: list[tuple[str, object]] = []
    prettyoutput.Log("Adding Sections\n")
    for BlockNode in InputVolumeNode.findall('Block'):
        for SectionNode in BlockNode.Sections:
            OutputSectionNode = sections_parent.find("Section[@Number='%d']" % SectionNode.Number)
            assert (OutputSectionNode is None)
            prettyoutput.Log('Queue %g' % SectionNode.Number)
            section_jobs.append((BlockNode.Path, SectionNode))

    total_sections = len(section_jobs)
    completed_sections = 0
    sections_label = "Sections"
    sections_track_id = "vikingxml:sections"
    if total_sections:
        report_iterate(sections_track_id, 0, total_sections, sections_label, depth=0)

    try:
        for BlockPath, SectionNode in section_jobs:
            section_id = str(SectionNode.Number)
            prettyoutput.Log('%s' % section_id)
            report_iterate(
                sections_track_id,
                completed_sections,
                total_sections,
                sections_label,
                depth=0,
                section=section_id,
                element=section_id,
            )

            OutputSectionNode = ParseSection(BlockPath, SectionNode)
            sections_parent.append(OutputSectionNode)

            completed_sections += 1
            report_iterate(
                sections_track_id,
                completed_sections,
                total_sections,
                sections_label,
                depth=0,
                section=section_id,
                element=section_id,
            )
    finally:
        if total_sections:
            report_iterate_complete(sections_track_id, total_sections)

    AllSectionNodes = list(sections_parent.findall('Section'))
    OutputVolumeNode.attrib['num_sections'] = str(len(AllSectionNodes))


def ParseSection(BlockPath, SectionNode):
    # Create a section node, or create on if it doesn't exist
    OutputSectionNode = ETree.Element('Section', {'Number': str(SectionNode.Number),
                                                  'Path': os.path.join(BlockPath, SectionNode.Path),
                                                  'Name': SectionNode.Name})

    ParseChannels(SectionNode, OutputSectionNode)

    NotesNodes = SectionNode.findall('Notes')
    for NoteNode in NotesNodes:
        # Copy over Notes elements verbatim
        OutputSectionNode.append(NoteNode.Copy())

    return OutputSectionNode


def ParseChannels(SectionNode, OutputSectionNode):
    """Parse channels for a section; publishes nested ``vikingxml:channels`` progress."""
    channels = list(SectionNode.Channels)
    total_channels = len(channels)
    channels_track_id = "vikingxml:channels"
    section_number = SectionNode.Number

    if total_channels:
        report_iterate(
            channels_track_id,
            0,
            total_channels,
            "Channels",
            depth=1,
            section=section_number,
        )

    completed_channels = 0
    try:
        for ChannelNode in channels:
            channel_name = getattr(ChannelNode, "Name", None) or "channel"
            report_iterate(
                channels_track_id,
                completed_channels,
                total_channels,
                f"Channel {channel_name}",
                depth=1,
                section=section_number,
                element=channel_name,
            )

            ScaleNode = ChannelNode.find('Scale')

            for TransformNode in ChannelNode.findall('Transform'):
                OutputTransformNode = ParseTransform(TransformNode, OutputSectionNode)
                if not OutputTransformNode is None:
                    OutputTransformNode.attrib['Path'] = os.path.join(ChannelNode.Path, OutputTransformNode.attrib['Path'])

            for FilterNode in ChannelNode.Filters:
                for tilepyramid in FilterNode.findall('TilePyramid'):
                    OutputPyramidNode = ParsePyramidNode(FilterNode, tilepyramid, OutputSectionNode)
                    OutputPyramidNode.attrib['Path'] = os.path.join(ChannelNode.Path, FilterNode.Path,
                                                                    OutputPyramidNode.attrib['Path'])
                    if ScaleNode is not None:
                        AddScaleData(OutputPyramidNode, ScaleNode.X.UnitsOfMeasure, ScaleNode.X.UnitsPerPixel)
                for tileset in FilterNode.findall('Tileset'):
                    OutputTilesetNode = ParseTilesetNode(FilterNode, tileset, OutputSectionNode)
                    OutputTilesetNode.attrib['path'] = os.path.join(ChannelNode.Path, FilterNode.Path,
                                                                    OutputTilesetNode.attrib['path'])
                    if ScaleNode is not None:
                        AddScaleData(OutputTilesetNode, ScaleNode.X.UnitsOfMeasure, ScaleNode.X.UnitsPerPixel)
                    prettyoutput.Log("Tileset found for section " + str(SectionNode.Number))

            for NoteNode in ChannelNode.findall('Notes'):
                # Copy over Notes elements verbatim
                OutputNotesNode = ETree.SubElement(OutputSectionNode, 'Notes')
                OutputNotesNode.text = NoteNode.text

            completed_channels += 1
            report_iterate(
                channels_track_id,
                completed_channels,
                total_channels,
                f"Channel {channel_name}",
                depth=1,
                section=section_number,
                element=channel_name,
            )
    finally:
        if total_channels:
            report_iterate_complete(channels_track_id, total_channels)


def ParseTransform(TransformNode, OutputSectionNode):
    mFile = mosaicfile.MosaicFile.Load(TransformNode.FullPath)

    if (mFile is None):
        prettyoutput.LogErr("Unable to load transform: " + TransformNode.FullPath)
        return

    if (mFile.NumberOfImages < 1):
        prettyoutput.LogErr("Not including empty .mosaic file")
        return

    # Figure out what the tile prefix and postfix are for this mosaic file by extrapolating from the first tile filename
    for k in list(mFile.ImageToTransformString.keys()):
        TileFileName = k
        break

    # TileFileName = mFile.ImageToTransformString.keys()[0]

    # Figure out prefix and postfix parts of filenames
    parts = TileFileName.split('.')

    Postfix = parts[len(parts) - 1]

    # Two conventions are commonly used Section#.Tile#.png or Tile#.png
    if (len(parts) == 3):
        Prefix = parts[0]
    else:
        Prefix = ''

    UseForVolume = 'false'
    if ('grid' in TransformNode.Name.lower()):
        UseForVolume = 'true'

    # Viking needs the transform names to be consistent, and if transforms are built with different spacings, for TEM and CMP, Viking can't display
    # So we simplify the transform name
    TransformName = TransformNode.Name

    return ETree.SubElement(OutputSectionNode, 'Transform', {'FilePostfix': Postfix,
                                                             'FilePrefix': Prefix,
                                                             'Path': TransformNode.Path,
                                                             'Name': TransformName,
                                                             'UseForVolume': UseForVolume})


def ParsePyramidNode(FilterNode, InputPyramidNode, OutputSectionNode):
    OutputPyramidNode = ETree.SubElement(OutputSectionNode, 'Pyramid', {
        'Path': InputPyramidNode.Path,
        'Name': FilterNode.Parent.Name + "." + FilterNode.Name + ".Pyramid",
        'LevelFormat': InputPyramidNode.LevelFormat})

    for LevelNode in InputPyramidNode.Levels:
        ETree.SubElement(OutputPyramidNode, 'Level', {'Path': LevelNode.Path,
                                                      'Downsample': '%g' % LevelNode.Downsample})

    return OutputPyramidNode


def ParseTilesetNode(FilterNode, InputTilesetNode, OutputSectionNode):
    OutputTilesetNode = ETree.SubElement(OutputSectionNode, 'Tileset', {
        'path': InputTilesetNode.Path,
        'name': FilterNode.Parent.Name + "." + FilterNode.Name,
        'TileXDim': str(InputTilesetNode.TileXDim),
        'TileYDim': str(InputTilesetNode.TileYDim),
        'FilePrefix': InputTilesetNode.FilePrefix,
        'FilePostfix': InputTilesetNode.FilePostfix,
        'CoordFormat': InputTilesetNode.CoordFormat})

    for LevelNode in InputTilesetNode.Levels:
        ETree.SubElement(OutputTilesetNode, 'Level', {'path': LevelNode.Path,
                                                      'Downsample': '%g' % LevelNode.Downsample,
                                                      'GridDimX': str(LevelNode.GridDimX),
                                                      'GridDimY': str(LevelNode.GridDimY)})

    return OutputTilesetNode


# Merge the created VolumeXML with the general definitions in about.XML
def MergeAboutXML(volumeXML, aboutXML):
    import xml.dom.minidom

    prettyoutput.Log('MergeAboutXML ' + str(volumeXML) + ' ' + str(aboutXML))
    if not os.path.exists(volumeXML):
        return
    if not os.path.exists(aboutXML):
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
    if (volumeNode is not None and aboutNode is not None
            and volumeNode.nodeName == "Volume" and aboutNode.nodeName == "Volume"):
        baseVolumeDir = os.path.dirname(volumeXML)
        baseAboutDir = os.path.dirname(aboutXML)
        relPath = baseVolumeDir.replace(baseAboutDir, '')
        prettyoutput.Log("Relative path: " + relPath)
        Url = UpdateVolumePath(volumeNode, aboutNode, relPath)

    MergeElements(volumeNode, aboutNode)

    prettyoutput.Log("")

    with open(volumeXML, "w", encoding="utf-8") as xmlFile:
        volumeDom.writexml(xmlFile, addindent="  ", newl="\n")

    return Url


def MergeElements(volumeNode, aboutNode):
    if (ElementsEqual(volumeNode, aboutNode)):
        CopyNewAttributes(volumeNode, aboutNode)
        MergeChildren(volumeNode, aboutNode)


# Both arguments should be matching elements
def MergeChildren(volumeParent, aboutParent):
    aboutElement = aboutParent.firstChild
    while (aboutElement is not None):
        if (aboutElement.nodeName is None):
            break

        volumeElements = volumeParent.getElementsByTagName(aboutElement.nodeName)
        # The volume doesn't have any elements like this.  Add them
        if (volumeElements.length == 0):
            newNode = aboutElement.cloneNode(True)
            prettyoutput.Log('NewNode' + newNode.toxml())
            volumeParent.insertBefore(newNode, volumeParent.firstChild)
        else:
            for volElement in volumeElements:
                MergeElements(volElement, aboutElement)

        aboutElement = aboutElement.nextSibling


# Compare the attributes of two elements and return true if they match
def ElementsEqual(volumeElement, aboutElement):
    """Return true if the elements have the same tag, and the attributes found in both elements have same value"""

    if (aboutElement.nodeName != volumeElement.nodeName):
        return False

    for attrib_key in list(aboutElement.attributes.keys()):
        if not (volumeElement.hasAttribute(attrib_key) and aboutElement.hasAttribute(attrib_key)):
            continue
        if not volumeElement.getAttribute(attrib_key) == aboutElement.getAttribute(attrib_key):
            return False

    return True

    # Volume is the root element so it is always a match
    if (aboutElement.nodeName == "Volume"):
        return True

    # Nodes only match if their attributes match
    if (aboutElement.nodeName == "Section"):
        aboutNumber = aboutElement.getAttribute("number")
        volNumber = volumeElement.getAttribute("number")
        if (aboutNumber != volNumber):
            return False
        else:
            prettyoutput.Log("Equal:")
            prettyoutput.Log('v: ' + volumeElement.nodeName + ' ' + str(volNumber))
            prettyoutput.Log('a: ' + aboutElement.nodeName + ' ' + str(aboutNumber))
            prettyoutput.Log('')

            return True

    return False


def CopyNewAttributes(volumeElement, aboutElement):
    """Copy the attributes from the aboutElement to the volumeElement"""
    #   print 'v: ' + volumeElement.toxml()
    #   print 'a: ' + aboutElement.toxml()

    #   print 'v: ' + volumeElement.toxml()
    #   print 'a: ' + aboutElement.toxml()

    if not aboutElement.hasAttributes():
        return

    attributeMap = aboutElement.attributes
    for i in range(0, attributeMap.length):
        attribute = attributeMap.item(i)

        if not volumeElement.hasAttribute(attribute.name):
            volumeElement.setAttribute(attribute.name, attribute.value)


def UpdateVolumePath(volumeElement, aboutElement, relPath):
    """
    Special case for updating the root element Volume path
    """
    if not aboutElement.hasAttributes():
        return

    if (len(relPath) > 0):
        relPath = relPath.lstrip('\\')
        relPath = relPath.lstrip('/')

    attributeMap = aboutElement.attributes
    for i in range(0, attributeMap.length):
        attribute = attributeMap.item(i)

        if (attribute.name == "host"):
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
