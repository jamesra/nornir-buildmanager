from __future__ import annotations

import nornir_buildmanager.volumemanager
from nornir_buildmanager.volumemanager import HistogramBase

class PruneNode(HistogramBase):

    @property
    def Overlap(self) -> float | None:
        if 'Overlap' in self.attrib:
            return float(self.attrib['Overlap'])

        return None

    @property
    def NumImages(self) -> int:
        if 'NumImages' in self.attrib:
            return int(self.attrib['NumImages'])

        return 0

    @NumImages.setter
    def NumImages(self, value: int | None):

        if value is None:
            if 'NumImages' in self.attrib:
                del self.attrib['NumImages']
                return

        self.attrib['NumImages'] = str(value)
        return

    @property
    def UserRequestedCutoff(self) -> float | None:
        val = self.attrib.get('UserRequestedCutoff', None)
        if isinstance(val, str):
            if len(val) == 0:
                return None

        if val is not None:
            val = float(val)

        return val

    @UserRequestedCutoff.setter
    def UserRequestedCutoff(self, val: float | None):
        if val is None:
            val = ""

        self.attrib['UserRequestedCutoff'] = str(val)

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Prune'

        super(PruneNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, Type: str, Overlap: float, attrib=None, **extra) -> PruneNode:

        obj = cls(attrib=attrib, **extra)
        obj.attrib['Type'] = Type
        obj.attrib['Overlap'] = str(Overlap)

        if 'UserRequestedCutoff' not in obj.attrib:
            obj.attrib['UserRequestedCutoff'] = ""

        return obj
