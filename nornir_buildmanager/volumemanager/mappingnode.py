from __future__ import annotations
from nornir_shared import misc as misc

from nornir_buildmanager.volumemanager import XElementWrapper

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
    def Mapped(self) -> frozenset[int]:
        if self._mapped_cache is None:
            mapped_list = misc.ListFromAttribute(self.attrib.get('Mapped', []))
            self._mapped_cache = frozenset(mapped_list)

        return self._mapped_cache

    @Mapped.setter
    def Mapped(self, value: frozenset[int] | int):
        AdjacentSectionString = None
        if isinstance(value, list):
            value.sort()
            AdjacentSectionString = ','.join(str(x) for x in value)
            self._mapped_cache = frozenset(value)
        elif isinstance(value, int):
            AdjacentSectionString = str(value)
            self._mapped_cache = frozenset([value])
        elif value is None:
            if 'Mapped' in self.attrib:
                del self.attrib['Mapped']
                self._mapped_cache = None
            return
        else:
            raise ValueError(f"Unexpected type passed to Mapped {value}")

        self.attrib['Mapped'] = AdjacentSectionString

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
        self.Mapped = updated_map
        # self._AttributeChanged = True #Handled by setattr of Mapped

    def __str__(self):
        self._mapped_cache = None
        return f"{self.Control} <- {', '.join([str(m) for m in self.Mapped])}"

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Mapping'
        
        super(MappingNode, self).__init__(tag=tag, attrib=attrib, **extra)

        self._mapped_cache = None

    @classmethod
    def Create(cls, ControlNumber, MappedNumbers, attrib=None, **extra) -> MappingNode:
        obj = MappingNode(tag='Mapping', attrib=attrib, **extra)

        obj.attrib['Control'] = str(ControlNumber)
        obj._mapped_cache = None

        if MappedNumbers is not None:
            obj.Mapped = MappedNumbers

        return obj
