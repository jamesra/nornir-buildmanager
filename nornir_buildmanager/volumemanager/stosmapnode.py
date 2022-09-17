from __future__ import annotations

import nornir_imageregistration.transforms
from nornir_buildmanager.volumemanager import XElementWrapper
from nornir_buildmanager.volumemanager.mappingnode import MappingNode
from nornir_shared import misc as misc


class StosMapNode(XElementWrapper):

    @property
    def Name(self) -> str:
        return self.get('Name', '')

    @Name.setter
    def Name(self, Value):
        self.attrib['Name'] = Value

    @property
    def Type(self) -> str:
        """Type of Stos Map"""
        return self.attrib.get("Type", None)

    @Type.setter
    def Type(self, val):
        if val is None:
            if 'Type' in self.attrib:
                del self.attrib['Type']
        else:
            self.attrib['Type'] = val

    @property
    def CenterSection(self) -> int | None:
        if 'CenterSection' in self.attrib:
            val = self.attrib.get('CenterSection', None)
            if len(val) == 0:
                return None
            else:
                return int(val)

        return None

    @CenterSection.setter
    def CenterSection(self, val):
        if val is None:
            self.attrib['CenterSection'] = ""
        else:
            assert (isinstance(val, int))
            self.attrib['CenterSection'] = str(val)

    @property
    def Mappings(self) -> [MappingNode]:
        return list(self.findall('Mapping'))

    def MappedToControls(self) -> {int: [int]}:
        """Return dictionary of possible control sections for a given mapped section number"""
        MappedToControlCandidateList = {}
        for mappingNode in self.Mappings:
            for mappedSection in mappingNode.Mapped:
                if mappedSection in MappedToControlCandidateList:
                    MappedToControlCandidateList[mappedSection].append(mappingNode.Control)
                else:
                    MappedToControlCandidateList[mappedSection] = [mappingNode.Control]

        return MappedToControlCandidateList

    def GetMappingsForControl(self, Control) -> [MappingNode]:
        mappings = self.findall("Mapping[@Control='" + str(Control) + "']")
        if mappings is None:
            return []

        return list(mappings)

    def ClearBannedControlMappings(self, NonStosSectionNumbers):
        """Remove any control sections from a mapping which cannot be a control"""

        removed = False
        for InvalidControlSection in NonStosSectionNumbers:
            mapNodes = self.GetMappingsForControl(InvalidControlSection)
            for mapNode in mapNodes:
                removed = True
                self.remove(mapNode)

        return removed

    @property
    def AllowDuplicates(self) -> bool:
        return bool(self.attrib.get('AllowDuplicates', True))

    @classmethod
    def _SectionNumberFromParameter(cls, input_value):
        val = None
        if isinstance(input_value, nornir_imageregistration.transforms.registrationtree.RegistrationTreeNode):
            val = input_value.SectionNumber
        elif isinstance(input_value, int):
            val = input_value
        else:
            raise TypeError("Section Number parameter should be an integer or RegistrationTreeNode")

        return val

    def AddMapping(self, control: int, mapped: int):
        """
        Creates a mapping to a control section by Add/Update a <Mapping> element
        :param int control: Control section number
        :param int mapped: Mapped section number
        """

        val = StosMapNode._SectionNumberFromParameter(mapped)
        control = StosMapNode._SectionNumberFromParameter(control)

        child_mapping = self.GetChildByAttrib('Mapping', 'Control', control)
        if child_mapping is None:
            child_mapping = MappingNode.Create(control, val)
            self.append(child_mapping)
        else:
            if val not in child_mapping.Mapped:
                child_mapping.AddMapping(val)
        return

    def RemoveMapping(self, Control, Mapped):
        """Remove a mapping
        :param int Control: Control section number
        :param int Mapped: Mapped section number

        :return: True if mapped section is found and removed
        """

        Mapped = StosMapNode._SectionNumberFromParameter(Mapped)
        Control = StosMapNode._SectionNumberFromParameter(Control)

        childMapping = self.GetChildByAttrib('Mapping', 'Control', Control)
        if childMapping is not None:
            if Mapped in childMapping.Mapped:
                childMapping.RemoveMapping(Mapped)

                if len(childMapping.Mapped) == 0:
                    self.remove(childMapping)

                return True

        return False

    def FindAllControlsForMapped(self, MappedSection):
        """Given a section to be mapped, return the first control section found"""
        for m in self.findall('Mapping'):

            if MappedSection in m.Mapped:
                yield m.Control

        return

    def RemoveDuplicateControlEntries(self, Control):
        """If there are two entries with the same control number we merge the mapping list and delete the duplicate"""

        mappings = list(self.GetMappingsForControl(Control))
        if len(mappings) < 2:
            return False

        mergeMapping = mappings[0]
        for i in range(1, len(mappings)):
            mappingNode = mappings[i]
            for mappedSection in mappingNode.Mapped:
                mergeMapping.AddMapping(mappedSection)
                XElementWrapper.logger.warning('Moving duplicate mapping ' + str(Control) + ' <- ' + str(mappedSection))

            self.remove(mappingNode)

        return True

    @property
    def NeedsValidation(self):
        return True  # Checking the mapping is easier than checking if volumedata.xml has changed

    def IsValid(self) -> (bool, str):
        """Check for mappings whose control section is in the non-stos section numbers list"""

        if not hasattr(self, 'Parent'):
            return super(StosMapNode, self).IsValid()

        NonStosSectionsNode = self.Parent.find('NonStosSectionNumbers')

        AlreadyMappedSections = []

        if NonStosSectionsNode is None:
            return super(StosMapNode, self).IsValid()

        NonStosSections = misc.ListFromAttribute(NonStosSectionsNode.text)

        MappingNodes = list(self.findall('Mapping'))

        for i in range(len(MappingNodes) - 1, -1, -1):
            mapping_node = MappingNodes[i]
            self.RemoveDuplicateControlEntries(mapping_node.Control)

        MappingNodes = list(self.findall('Mapping'))

        for i in range(len(MappingNodes) - 1, -1, -1):
            mapping_node = MappingNodes[i]

            if mapping_node.Control in NonStosSections:
                mapping_node.Clean()
                XElementWrapper.logger.warning('Mappings for control section ' + str(
                    mapping_node.Control) + ' removed due to existence in NonStosSectionNumbers element')
            else:
                mapped_sections = mapping_node.Mapped
                for isection in range(len(mapped_sections) - 1, -1, -1):
                    mapped_section = mapped_sections[isection]
                    if mapped_section in AlreadyMappedSections and not self.AllowDuplicates:
                        del mapped_sections[isection]
                        XElementWrapper.logger.warning(
                            f'Removing duplicate mapping {mapped_section} -> {mapping_node.Control}')
                    else:
                        AlreadyMappedSections.append(mapped_section)

                if len(mapped_sections) == 0:
                    mapping_node.Clean()
                    XElementWrapper.logger.warning(
                        'No mappings remain for control section ' + str(mapping_node.Control))
                elif len(mapped_sections) != mapping_node.Mapped:
                    mapping_node.Mapped = mapped_sections

        return super(StosMapNode, self).IsValid()

    @classmethod
    def Create(cls, Name, attrib=None, **extra):
        obj = StosMapNode(tag='StosMap', Name=Name, attrib=attrib, **extra)
        return obj
