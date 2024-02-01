from __future__ import annotations

from collections.abc import Generator, Iterable
from typing import Sequence

import nornir_buildmanager
import nornir_buildmanager.volumemanager
from nornir_buildmanager.volumemanager import SectionNode, StosGroupNode, StosMapNode, XElementWrapper, \
    XNamedContainerElementWrapped


class BlockNode(XNamedContainerElementWrapped):

    @property
    def Sections(self) -> Generator[SectionNode]:
        return self.findall('Section')

    @property
    def StosGroups(self) -> Generator[StosGroupNode]:
        return self.findall('StosGroup')

    @property
    def StosMaps(self) -> Generator[StosMapNode]:
        return self.findall('StosMap')

    def GetSection(self, Number: int) -> SectionNode:
        return self.GetChildByAttrib('Section', 'Number', Number)  # type: SectionNode

    def GetOrCreateSection(self, Number: int) -> (bool, SectionNode):
        """
        :param Number: Section Number
        :return: (bool, SectionNode) True if a node was created.  The section node element.
        """
        section_obj = self.GetSection(Number)

        if section_obj is None:
            SectionName = ('%' + nornir_buildmanager.templates.Current.SectionFormat) % Number
            SectionPath = ('%' + nornir_buildmanager.templates.Current.SectionFormat) % Number

            section_obj = SectionNode.Create(Number,
                                             SectionName,
                                             SectionPath)
            return self.UpdateOrAddChildByAttrib(section_obj, 'Number')
        else:
            return False, section_obj

    def GetStosGroup(self, group_name: str, downsample: float) -> StosGroupNode | None:
        stos_group: StosGroupNode
        for stos_group in self.findall("StosGroup[@Name='%s']" % group_name):
            if stos_group.Downsample == downsample:
                return stos_group

        return None

    def GetOrCreateStosGroup(self, group_name: str, downsample: float) -> (bool, StosGroupNode):
        """:Return: Tuple of (created, stos_group)"""

        existing_stos_group = self.GetStosGroup(group_name, downsample)
        if existing_stos_group is not None:
            return False, existing_stos_group

        OutputStosGroupNode = StosGroupNode.Create(group_name, Downsample=downsample)
        self.append(OutputStosGroupNode)

        return True, OutputStosGroupNode

    def GetStosMap(self, map_name: str) -> StosMapNode:
        return self.GetChildByAttrib('StosMap', 'Name', map_name)  # type: StosMapNode

    def GetOrCreateStosMap(self, map_name) -> StosMapNode:
        stos_map_node = self.GetStosMap(map_name)
        if stos_map_node is None:
            stos_map_node = StosMapNode.Create(map_name)
            self.append(stos_map_node)
            return stos_map_node
        else:
            return stos_map_node

    def RemoveStosMap(self, map_name: str) -> bool:
        """:return: True if a map was found and removed"""
        stos_map_node = self.GetStosMap(map_name)
        if stos_map_node is not None:
            self.remove(stos_map_node)
            return True

        return False

    def RemoveStosGroup(self, group_name: str, downsample: float) -> bool:
        """:return: True if a StosGroup was found and removed"""
        existing_stos_group = self.GetStosGroup(group_name, downsample)
        if existing_stos_group is not None:
            self.remove(existing_stos_group)
            return True

        return False

    def MarkSectionsAsDamaged(self, section_number_list: Sequence[int]):
        """Add the sections in the list to the NonStosSectionNumbers"""
        if not isinstance(section_number_list, set) or isinstance(section_number_list, frozenset):
            section_number_list = frozenset(section_number_list)

        self.NonStosSectionNumbers = frozenset(section_number_list.union(self.NonStosSectionNumbers))

    def MarkSectionsAsUndamaged(self, section_number_list: Sequence[int]):
        if not isinstance(section_number_list, set) or isinstance(section_number_list, frozenset):
            section_number_list = frozenset(section_number_list)

        existing_set = self.NonStosSectionNumbers
        self.NonStosSectionNumbers = existing_set.difference(section_number_list)

    @property
    def NonStosSectionNumbers(self) -> frozenset[int]:
        """A list of integers indicating which section numbers should not be control sections for slice to slice registration"""
        StosExemptNode = XElementWrapper(tag='NonStosSectionNumbers')
        (added, StosExemptNode) = self.UpdateOrAddChild(StosExemptNode)

        # Fetch the list of the exempt nodes from the element text
        ExemptString = StosExemptNode.text

        if ExemptString is None or len(ExemptString) == 0:
            return frozenset([])

        # OK, parse the exempt string to a different list
        NonStosSectionNumbers = frozenset(sorted([int(x) for x in ExemptString.split(',')]))

        ##################
        # Temporary fix for old meta-data that was not sorted.  It can be
        # deleted after running an align on each legacy volume
        ExpectedText = BlockNode.NonStosNumbersToString(NonStosSectionNumbers)
        if ExpectedText != StosExemptNode.text:
            self.NonStosSectionNumbers = NonStosSectionNumbers
        ################

        return NonStosSectionNumbers

    @NonStosSectionNumbers.setter
    def NonStosSectionNumbers(self, value: Iterable[int]):
        """A list of integers indicating which section numbers should not be control sections for slice to slice registration"""
        StosExemptNode = XElementWrapper(tag='NonStosSectionNumbers')
        (added, StosExemptNode) = self.UpdateOrAddChild(StosExemptNode)

        ExpectedText = BlockNode.NonStosNumbersToString(value)
        if StosExemptNode.text != ExpectedText:
            StosExemptNode.text = ExpectedText
            StosExemptNode._AttributesChanged = True

    @staticmethod
    def NonStosNumbersToString(value) -> str:
        """Converts a string, integer, list, set, or frozen set to a comma
        delimited string"""
        if isinstance(value, str):
            return value
        elif isinstance(value, int):
            return str(value)
        elif isinstance(value, list):
            value.sort()
            return ','.join(list(map(str, value)))
        elif isinstance(value, set) or isinstance(value, frozenset):
            listValue = list(value)
            listValue.sort()
            return ','.join(list(map(str, listValue)))

        raise NotImplementedError()

    @property
    def NeedsValidation(self) -> bool:
        return True

    @classmethod
    def Create(cls, Name, Path=None, **extra) -> BlockNode:
        return super(BlockNode, cls).Create(tag='Block', Name=Name, Path=Path, **extra)
