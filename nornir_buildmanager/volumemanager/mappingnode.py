from __future__ import annotations

from nornir_buildmanager.volumemanager import XElementWrapper
from nornir_shared import misc as misc


class MappingNode(XElementWrapper):


    @property
    def SortKey(self):
        """The default key used for sorting elements"""
        return self.Control  # self.tag + ' ' + (nornir_buildmanager.templates.Current.SectionTemplate % self.Control)

    @property
    def Control(self) -> int | None:
        if 'Control' in self.attrib:
            return int(self.attrib['Control'])

        return None

    @property
    def Mapped(self) -> int:
        if self._mapped_cache is None:
            mappedList = misc.ListFromAttribute(self.attrib.get('Mapped', []))
            mappedList.sort()
            self._mapped_cache = mappedList

        return self._mapped_cache

    @Mapped.setter
    def Mapped(self, value):
        AdjacentSectionString = ''
        if isinstance(value, list):
            value.sort()
            AdjacentSectionString = ','.join(str(x) for x in value)
        else:
            assert (isinstance(value, int))
            AdjacentSectionString = str(value)

        self.attrib['Mapped'] = AdjacentSectionString
        self._mapped_cache = None

    def AddMapping(self, value: int):
        intval = int(value)
        updated_map = list(self.Mapped)
        if intval in updated_map:
            return
        else:
            updated_map.append(value)
            self.Mapped = updated_map
            # self._AttributeChanged = True #Handled by setattr of Mapped

    def RemoveMapping(self, value: int):
        intval = int(value)
        updated_map = list(self.Mapped)
        if intval not in updated_map:
            return

        updated_map.remove(intval)
        self.Mappings = updated_map
        # self._AttributeChanged = True #Handled by setattr of Mapped

    def __str__(self):
        self._mapped_cache = None
        return "%d <- %s" % (self.Control, str(self.Mapped))

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Mapping'
        
        super(MappingNode, self).__init__(tag=tag, attrib=attrib, **extra)

        self.Mappings = None
        self._mapped_cache = None

    @classmethod
    def Create(cls, ControlNumber, MappedNumbers, attrib=None, **extra):
        obj = MappingNode(tag='Mapping', attrib=attrib, **extra)

        obj.attrib['Control'] = str(ControlNumber)
        obj._mapped_cache = None

        if MappedNumbers is not None:
            obj.Mapped = MappedNumbers

        return obj
