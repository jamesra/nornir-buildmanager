from __future__ import annotations

import nornir_buildmanager
from nornir_buildmanager.volumemanager import XElementWrapper, TransformNode, ImageNode, BlockNode
from nornir_shared import prettyoutput as prettyoutput


class SectionMappingsNode(XElementWrapper):

    @property
    def SortKey(self):
        """The default key used for sorting elements"""
        return self.tag + ' ' + (nornir_buildmanager.templates.Current.SectionTemplate % self.MappedSectionNumber)

    @property
    def MappedSectionNumber(self) -> int | None:
        if 'MappedSectionNumber' in self.attrib:
            return int(self.attrib['MappedSectionNumber'])

        return None

    @property
    def Transforms(self) -> [TransformNode]:
        return list(self.findall('Transform'))

    @property
    def Images(self) -> [ImageNode]:
        return list(self.findall('Image'))

    def TransformsToSection(self, sectionNumber:int):
        return self.GetChildrenByAttrib('Transform', 'ControlSectionNumber', sectionNumber)

    def FindStosTransform(self, ControlSectionNumber:int, ControlChannelName: str, ControlFilterName: str, MappedSectionNumber: int,
                          MappedChannelName: str, MappedFilterName: str):
        """
        Find the stos transform matching all of the parameters if it exists
        WORKAROUND: The etree implementation has a serious shortcoming in that it cannot handle the 'and' operator in XPath queries.  This function is a workaround for a multiple criteria find query
        :rtype TransformNode:
        """

        # TODO: 3/10/2017 I believe I can stop checking MappedSectionNumber because it is built into the SectionMapping node.  This is a sanity check before I pull the plug
        assert (MappedSectionNumber == self.MappedSectionNumber)

        for t in self.Transforms:
            if t.ControlSectionNumber != ControlSectionNumber:
                continue

            if t.ControlChannelName != ControlChannelName:
                continue

            if t.ControlFilterName != ControlFilterName:
                continue

            if t.MappedSectionNumber != MappedSectionNumber:
                continue

            if t.MappedChannelName != MappedChannelName:
                continue

            if t.MappedFilterName != MappedFilterName:
                continue

            return t

        return None

    def TryRemoveTransformNode(self, transform_node: TransformNode):
        """Remove the transform if it exists
        :rtype bool:
        :return: True if transform removed
        """
        return self.TryRemoveTransform(transform_node.ControlSectionNumber,
                                       transform_node.ControlChannelName,
                                       transform_node.ControlFilterName,
                                       transform_node.MappedChannelName,
                                       transform_node.MappedFilterName)

    def TryRemoveTransform(self,
                           ControlSectionNumber: int,
                           ControlChannelName: str,
                           ControlFilterName: str,
                           MappedChannelName: str,
                           MappedFilterName: str):
        """Remove the transform if it exists
        :rtype bool:
        :return: True if transform removed
        """

        existing_transform = self.FindStosTransform(ControlSectionNumber, ControlChannelName, ControlFilterName,
                                                    self.MappedSectionNumber, MappedChannelName, MappedFilterName)
        if existing_transform is not None:
            existing_transform.Clean()
            return True

        return False

    def AddOrUpdateTransform(self, transform_node: TransformNode):
        """
        Add or update a transform to the section mappings.
        :rtype bool:
        :return: True if the transform node was added.  False if updated.
        """
        existing_transform = self.TryRemoveTransformNode(transform_node)
        self.AddChild(transform_node)
        return not existing_transform

    @classmethod
    def _CheckForFilterExistence(cls,
                                 block: BlockNode,
                                 section_number: int,
                                 channel_name: str,
                                 filter_name: str) -> (bool, str):

        section_node = block.GetSection(section_number)
        if section_node is None:
            return False, "Transform section not found %d.%s.%s" % (section_number, channel_name, filter_name)

        channel_node = section_node.GetChannel(channel_name)
        if channel_node is None:
            return False, "Transform channel not found %d.%s.%s" % (section_number, channel_name, filter_name)

        filter_node = channel_node.GetFilter(filter_name)
        if filter_node is None:
            return False, "Transform filter not found %d.%s.%s" % (section_number, channel_name, filter_name)

        return True, None

    def CleanIfInvalid(self) -> (bool, str):
        cleaned, reason = XElementWrapper.CleanIfInvalid(self)
        if not cleaned:
            return self.CleanTransformsIfInvalid()

        return cleaned, reason

    def CleanTransformsIfInvalid(self) -> (bool, str):
        block = self.FindParent('Block')  # type : BlockNode
        reason = ""
        transform_cleaned = False

        # Check the transforms and make sure the input data still exists
        for t in self.Transforms:
            transformValid = t.IsValid()
            if not transformValid[0]:
                reason += f"Cleaning invalid transform {t.Path}. IsValid returned false\n"
                t.Clean()
                transform_cleaned = True
                continue

            ControlResult = SectionMappingsNode._CheckForFilterExistence(block, t.ControlSectionNumber,
                                                                         t.ControlChannelName, t.ControlFilterName)
            if ControlResult[0] is False:
                reason += f"Cleaning transform {t.Path}.  Control input did not exist: {ControlResult[1]}"
                t.Clean()
                transform_cleaned = True
                continue

            MappedResult = SectionMappingsNode._CheckForFilterExistence(block, t.MappedSectionNumber,
                                                                        t.MappedChannelName, t.MappedFilterName)
            if MappedResult[0] is False:
                reason += f"Cleaning transform {t.Path}.  Mapped input did not exist: {MappedResult[1]}"
                t.Clean()
                transform_cleaned = True
                continue

        if transform_cleaned:
            prettyoutput.Log(reason)

        return transform_cleaned

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'SectionMappings'

        super(SectionMappingsNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, Path=None, MappedSectionNumber=None, attrib=None, **extra):
        obj = SectionMappingsNode(attrib=attrib, **extra)

        if MappedSectionNumber is not None:
            obj.attrib['MappedSectionNumber'] = str(MappedSectionNumber)

        return obj
