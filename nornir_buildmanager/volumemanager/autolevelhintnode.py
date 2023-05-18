from __future__ import annotations

import math

from nornir_buildmanager.volumemanager import XElementWrapper


class AutoLevelHintNode(XElementWrapper):

    @property
    def UserRequestedMinIntensityCutoff(self) -> float | None:
        """Returns None or a float"""
        val = self.attrib.get('UserRequestedMinIntensityCutoff', None)
        if val is None or len(val) == 0:
            return None
        return float(val)

    @UserRequestedMinIntensityCutoff.setter
    def UserRequestedMinIntensityCutoff(self, val: float):
        if val is None:
            self.attrib['UserRequestedMinIntensityCutoff'] = ""
        else:
            if math.isnan(val):
                self.attrib['UserRequestedMinIntensityCutoff'] = ""
            else:
                self.attrib['UserRequestedMinIntensityCutoff'] = "%g" % val

    @property
    def UserRequestedMaxIntensityCutoff(self) -> float | None:
        """Returns None or a float"""
        val = self.attrib.get('UserRequestedMaxIntensityCutoff', None)
        if val is None or len(val) == 0:
            return None
        return float(val)

    @UserRequestedMaxIntensityCutoff.setter
    def UserRequestedMaxIntensityCutoff(self, val: float):
        if val is None:
            self.attrib['UserRequestedMaxIntensityCutoff'] = ""
        else:
            if math.isnan(val):
                self.attrib['UserRequestedMaxIntensityCutoff'] = ""
            else:
                self.attrib['UserRequestedMaxIntensityCutoff'] = "%g" % val

    @property
    def UserRequestedGamma(self) -> float | None:
        """Returns None or a float"""
        val = self.attrib.get('UserRequestedGamma', None)
        if val is None or len(val) == 0:
            return None
        return float(val)

    @UserRequestedGamma.setter
    def UserRequestedGamma(self, val: float):
        if val is None:
            self.attrib['UserRequestedGamma'] = ""
        else:
            if math.isnan(val):
                self.attrib['UserRequestedGamma'] = ""
            else:
                self.attrib['UserRequestedGamma'] = "%g" % val

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'AutoLevelHint'

        super(AutoLevelHintNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, MinIntensityCutoff=None, MaxIntensityCutoff=None, Gamma=None):
        attrib = {'UserRequestedMinIntensityCutoff': "",
                  'UserRequestedMaxIntensityCutoff': "",
                  'UserRequestedGamma': ""}

        obj = AutoLevelHintNode(attrib=attrib)

        obj.UserRequestedMinIntensityCutoff = MinIntensityCutoff
        obj.UserRequestedMaxIntensityCutoff = MaxIntensityCutoff
        obj.UserRequestedGamma = Gamma

        return obj
