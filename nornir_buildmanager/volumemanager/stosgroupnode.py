from __future__ import annotations

import logging
import os
import shutil

import nornir_buildmanager
from nornir_buildmanager.volumemanager import *

import nornir_shared.files
from nornir_shared import prettyoutput as prettyoutput


class StosGroupNode(XNamedContainerElementWrapped):

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'StosGroup'

        super(StosGroupNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, Name: str, Downsample: int, **extra) -> StosGroupNode:
        Path = Name

        obj = super(StosGroupNode, cls).Create(tag='StosGroup', Name=Name, attrib=None, Path=Path, **extra)
        obj.Downsample = Downsample
        return obj

    @property
    def Downsample(self) -> float:
        return float(self.attrib.get('Downsample', 'NaN'))

    @Downsample.setter
    def Downsample(self, val: float | int):
        """The default key used for sorting elements"""
        self.attrib['Downsample'] = '%g' % val

    @property
    def ManualInputDirectory(self) -> str:
        """Directory that manual override stos files are placed in"""
        return os.path.join(self.FullPath, 'Manual')

    def CreateDirectories(self):
        """Ensures the manual input directory exists"""
        os.makedirs(self.FullPath, exist_ok=True)
        os.makedirs(self.ManualInputDirectory, exist_ok=True)

    def PathToManualTransform(self, InputTransformFullPath):
        """Check the manual directory for the existence of a user-supplied file we should use.
           Returns the path to the file if it exists, otherwise None"""

        transform_filename = os.path.basename(InputTransformFullPath)
        # Copy the input stos or converted stos to the input directory
        ManualInputStosFullPath = os.path.join(self.ManualInputDirectory, transform_filename)
        if os.path.exists(ManualInputStosFullPath):
            return ManualInputStosFullPath

        return None

    @property
    def SectionMappings(self) -> Generator[SectionMappingsNode]:
        return self.findall('SectionMappings')

    def GetSectionMapping(self, MappedSectionNumber: int) -> SectionMappingsNode:
        return self.GetChildByAttrib('SectionMappings', 'MappedSectionNumber', MappedSectionNumber)

    def GetOrCreateSectionMapping(self, MappedSectionNumber: int) -> (bool, SectionMappingsNode):
        (added, sectionMappings) = self.UpdateOrAddChildByAttrib(
            nornir_buildmanager.volumemanager.SectionMappingsNode.Create(MappedSectionNumber=MappedSectionNumber),
            'MappedSectionNumber')
        return added, sectionMappings

    def TransformsForMapping(self, MappedSectionNumber: int, ControlSectionNumber: int) -> [TransformNode]:
        sectionMapping = self.GetSectionMapping(MappedSectionNumber)
        if sectionMapping is None:
            return []

        return sectionMapping.TransformsToSection(ControlSectionNumber)

    @property
    def NeedsValidation(self) -> bool:
        return True

    def GetStosTransformNode(self,
                             ControlFilter: FilterNode,
                             MappedFilter: FilterNode) -> TransformNode | None:
        MappedSectionNode = MappedFilter.FindParent("Section")
        MappedChannelNode = MappedFilter.FindParent("Channel")
        ControlSectionNode = ControlFilter.FindParent("Section")
        ControlChannelNode = ControlFilter.FindParent("Channel")

        section_mappings_node = self.GetSectionMapping(MappedSectionNode.Number)
        if section_mappings_node is None:
            return None

        # assert(not nornir_buildmanager.volumemanager.SectionMappingsNode is None) #We expect the caller to arrange for a section mappings node in advance

        stosNode = section_mappings_node.FindStosTransform(ControlSectionNode.Number,
                                                           ControlChannelNode.Name,
                                                           ControlFilter.Name,
                                                           MappedSectionNode.Number,
                                                           MappedChannelNode.Name,
                                                           MappedFilter.Name)

        return stosNode

    def GetOrCreateStosTransformNode(self, ControlFilter: FilterNode, MappedFilter: FilterNode,
                                     OutputType: str, OutputPath: str) -> tuple[bool, TransformNode]:
        added = False
        stosNode = self.GetStosTransformNode(ControlFilter, MappedFilter)

        if stosNode is None:
            added = True
            stosNode = self.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType, OutputPath)
        else:
            self.__LegacyUpdateStosNode(stosNode, ControlFilter, MappedFilter, OutputPath)

        return added, stosNode

    def AddChecksumsToStos(self, stosNode, ControlFilter: FilterNode, MappedFilter: FilterNode):

        stosNode._AttributesChanged = True
        if MappedFilter.Imageset.HasImage(self.Downsample) or MappedFilter.Imageset.CanGenerate(self.Downsample):
            stosNode.attrib['MappedImageChecksum'] = MappedFilter.Imageset.GetOrCreateImage(self.Downsample).Checksum
        else:
            stosNode.attrib['MappedImageChecksum'] = ""

        if ControlFilter.Imageset.HasImage(self.Downsample) or ControlFilter.Imageset.CanGenerate(self.Downsample):
            stosNode.attrib['ControlImageChecksum'] = ControlFilter.Imageset.GetOrCreateImage(self.Downsample).Checksum
        else:
            stosNode.attrib['ControlImageChecksum'] = ""

        if MappedFilter.HasMask and ControlFilter.HasMask:
            if MappedFilter.MaskImageset.HasImage(self.Downsample) or MappedFilter.MaskImageset.CanGenerate(
                    self.Downsample):
                stosNode.attrib['MappedMaskImageChecksum'] = MappedFilter.MaskImageset.GetOrCreateImage(
                    self.Downsample).Checksum
            else:
                stosNode.attrib['MappedMaskImageChecksum'] = ""

            if ControlFilter.MaskImageset.HasImage(self.Downsample) or ControlFilter.MaskImageset.CanGenerate(
                    self.Downsample):
                stosNode.attrib['ControlMaskImageChecksum'] = ControlFilter.MaskImageset.GetOrCreateImage(
                    self.Downsample).Checksum
            else:
                stosNode.attrib['ControlMaskImageChecksum'] = ""

    def CreateStosTransformNode(self,
                                ControlFilter: FilterNode,
                                MappedFilter: FilterNode,
                                OutputType: str,
                                OutputPath: str):
        """
        :param OutputPath:
        :param FilterNode ControlFilter: Filter for control image
        :param FilterNode MappedFilter: Filter for mapped image
        :param str OutputType: Type of stosNode
        :Param str OutputPath: Full path to .stos file
        """

        MappedSectionNode = MappedFilter.FindParent("Section")
        MappedChannelNode = MappedFilter.FindParent("Channel")
        ControlSectionNode = ControlFilter.FindParent("Section")
        ControlChannelNode = ControlFilter.FindParent("Channel")

        section_mappings_node = self.GetSectionMapping(MappedSectionNode.Number)
        assert (
                section_mappings_node is not None)  # We expect the caller to arrange for a section mappings node in advance

        stosNode = nornir_buildmanager.volumemanager.TransformNode.Create(str(ControlSectionNode.Number), OutputType,
                                                                          OutputPath,
                                                                          {'ControlSectionNumber': str(
                                                                              ControlSectionNode.Number),
                                                                           'MappedSectionNumber': str(
                                                                               MappedSectionNode.Number),
                                                                           'MappedChannelName': str(
                                                                               MappedChannelNode.Name),
                                                                           'MappedFilterName': str(MappedFilter.Name),
                                                                           'ControlChannelName': str(
                                                                               ControlChannelNode.Name),
                                                                           'ControlFilterName': str(
                                                                               ControlFilter.Name)})

        self.AddChecksumsToStos(stosNode, ControlFilter, MappedFilter)
        #        WORKAROUND: The etree implementation has a serious shortcoming in that it cannot handle the 'and' operator in XPath queries.
        #        (added, stosNode) = nornir_buildmanager.volumemanager.SectionMappingsNode.UpdateOrAddChildByAttrib(stosNode, ['ControlSectionNumber',
        #                                                                                    'ControlChannelName',
        #                                                                                    'ControlFilterName',
        #                                                                                    'MappedSectionNumber',
        #                                                                                    'MappedChannelName',
        #                                                                                    'MappedFilterName'])

        section_mappings_node.append(stosNode)

        return stosNode

    @staticmethod
    def GenerateStosFilename(ControlFilter: FilterNode, MappedFilter: FilterNode) -> str:

        ControlSectionNode = ControlFilter.FindParent('Section')  # type: SectionNode
        MappedSectionNode = MappedFilter.FindParent('Section')  # type: SectionNode

        OutputFile = f'{MappedSectionNode.Number}-{ControlSectionNode.Number}_ctrl-{ControlFilter.Parent.Name}_{ControlFilter.Name}_map-{MappedFilter.Parent.Name}_{MappedFilter.Name}.stos'
        return OutputFile

    @classmethod
    def _IsStosInputImageOutdated(cls, stosNode, ChecksumAttribName: str, imageNode: ImageNode | None):
        """
        :param nornir_buildmanager.volumemanager.TransformNode stosNode: Stos Transform Node to test
        :param str ChecksumAttribName: Name of attribute with checksum value on image node
        :param ImageNode imageNode: Image node to test
        """

        if imageNode is None:
            return True

        IsInvalid = False

        if len(stosNode.attrib.get(ChecksumAttribName, "")) > 0:
            IsInvalid = IsInvalid or not nornir_buildmanager.validation.transforms.IsValueMatched(stosNode,
                                                                                                  ChecksumAttribName,
                                                                                                  imageNode.Checksum)
        else:
            if not os.path.exists(imageNode.FullPath):
                IsInvalid = IsInvalid or True
            else:
                IsInvalid = IsInvalid or nornir_shared.files.IsOutdated(imageNode.FullPath, stosNode.FullPath)

        return IsInvalid

    def AreStosInputImagesOutdated(self,
                                   stosNode: TransformNode,
                                   ControlFilter: FilterNode,
                                   MappedFilter: FilterNode,
                                   MaskRequired: bool) -> bool:
        """
        :param TransformNode stosNode: Stos Transform Node to test
        :param FilterNode ControlFilter: Filter for control image
        :param FilterNode MappedFilter: Filter for mapped image
        :param bool MaskRequired: Require the use of masks
        """

        if stosNode is None or ControlFilter is None or MappedFilter is None:
            return True

        ControlImageNode = None
        MappedImageNode = None
        try:
            ControlImageNode = ControlFilter.GetOrCreateImage(self.Downsample)
            MappedImageNode = MappedFilter.GetOrCreateImage(self.Downsample)
        except nornir_buildmanager.NornirUserException as e:
            logger = logging.getLogger(__name__ + '.' + 'AreStosInputImagesOutdated')
            logger.warning(
                "Reporting .stos file {0} is outdated after exception raised when finding images:\n{0}".format(
                    stosNode.FullPath, str(e)))
            prettyoutput.LogErr(
                "Reporting {0} is outdated after exception raised when finding images:\n{0}".format(stosNode.FullPath,
                                                                                                    str(e)))
            return True

        is_invalid = False

        is_invalid = is_invalid or StosGroupNode._IsStosInputImageOutdated(stosNode,
                                                                           ChecksumAttribName='ControlImageChecksum',
                                                                           imageNode=ControlImageNode)
        is_invalid = is_invalid or StosGroupNode._IsStosInputImageOutdated(stosNode,
                                                                           ChecksumAttribName='MappedImageChecksum',
                                                                           imageNode=MappedImageNode)

        if MaskRequired:
            ControlMaskImageNode = ControlFilter.GetMaskImage(self.Downsample)
            MappedMaskImageNode = MappedFilter.GetMaskImage(self.Downsample)
            is_invalid = is_invalid or StosGroupNode._IsStosInputImageOutdated(stosNode,
                                                                               ChecksumAttribName='ControlMaskImageChecksum',
                                                                               imageNode=ControlMaskImageNode)
            is_invalid = is_invalid or StosGroupNode._IsStosInputImageOutdated(stosNode,
                                                                               ChecksumAttribName='MappedMaskImageChecksum',
                                                                               imageNode=MappedMaskImageNode)

        return is_invalid

    @classmethod
    def __LegacyUpdateStosNode(cls, stosNode, ControlFilter: FilterNode, MappedFilter: FilterNode, OutputPath: str):

        if stosNode is None:
            return

        if not hasattr(stosNode, "ControlChannelName") or not hasattr(stosNode, "MappedChannelName"):
            MappedChannelNode = MappedFilter.FindParent("Channel")
            ControlChannelNode = ControlFilter.FindParent("Channel")

            renamedPath = os.path.join(os.path.dirname(stosNode.FullPath), stosNode.Path)
            XElementWrapper.logger.warning("Renaming stos transform for backwards compatability")
            XElementWrapper.logger.warning(renamedPath + " -> " + stosNode.FullPath)
            shutil.move(renamedPath, stosNode.FullPath)
            stosNode.Path = OutputPath
            stosNode.MappedChannelName = MappedChannelNode.Name
            stosNode.MappedFilterName = MappedFilter.Name
            stosNode.ControlChannelName = ControlChannelNode.Name
            stosNode.ControlFilterName = ControlFilter.Name
            stosNode.ControlImageChecksum = str(ControlFilter.Imageset.Checksum)
            stosNode.MappedImageChecksum = str(MappedFilter.Imageset.Checksum)

    @property
    def SummaryString(self) -> str:
        """
            :return: Name of the group and the downsample level
            :rtype str:
        """
        return "{0:s} {1:3d}".format(self.Name.ljust(20), int(self.Downsample))

    def CleanIfInvalid(self) -> (bool, str):
        cleaned, reason = super(StosGroupNode, self).CleanIfInvalid()

        # TODO: Deleting stale transforms and section mappinds needs to be enabled, but I identified this shortcoming in a remote and
        # want to work on it in my own test environment
        # if not cleaned:
        # for mapping in self.SectionMappings:
        # cleaned or mapping.CleanIfInvalid()

        return cleaned, reason
