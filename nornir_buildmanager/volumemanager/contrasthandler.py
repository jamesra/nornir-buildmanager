import logging

import nornir_buildmanager
from . import lockable


class ContrastHandler(object):
    logger = logging.getLogger(__name__ + '.' + 'XElementWrapper')

    @property
    def MaxIntensityCutoff(self):
        if 'MaxIntensityCutoff' in self.attrib:
            return round(float(self.attrib['MaxIntensityCutoff']), 3)

        return None

    @MaxIntensityCutoff.setter
    def MaxIntensityCutoff(self, value):
        if value is None:
            if 'MaxIntensityCutoff' in self.attrib:
                del self.attrib['MaxIntensityCutoff']
        else:
            self.attrib['MaxIntensityCutoff'] = "%g" % round(value, 3)

    @property
    def MinIntensityCutoff(self):
        if 'MinIntensityCutoff' in self.attrib:
            return round(float(self.attrib['MinIntensityCutoff']), 3)
        return None

    @MinIntensityCutoff.setter
    def MinIntensityCutoff(self, value):
        if value is None:
            if 'MinIntensityCutoff' in self.attrib:
                del self.attrib['MinIntensityCutoff']
        else:
            self.attrib['MinIntensityCutoff'] = "%g" % round(value, 3)

    @property
    def Gamma(self) -> float | None:
        if 'Gamma' in self.attrib:
            return round(float(self.attrib['Gamma']), 3)
        return None

    @Gamma.setter
    def Gamma(self, value: float):
        if value is None:
            if 'Gamma' in self.attrib:
                del self.attrib['Gamma']
        else:
            self.attrib['Gamma'] = "%g" % round(value, 3)

    def SetContrastValues(self, MinIntensityCutoff, MaxIntensityCutoff, Gamma: float):
        self.MinIntensityCutoff = MinIntensityCutoff
        self.MaxIntensityCutoff = MaxIntensityCutoff
        self.Gamma = Gamma

    def CopyContrastValues(self, node):
        """Copy the contrast values from the passed node into ourselves"""
        if node is None:
            self.MinIntensityCutoff = None
            self.MaxIntensityCutoff = None
            self.Gamma = None
        else:
            self.MinIntensityCutoff = node.MinIntensityCutoff
            self.MaxIntensityCutoff = node.MaxIntensityCutoff
            self.Gamma = node.Gamma

    def _LogContrastMismatch(self, MinIntensityCutoff, MaxIntensityCutoff, Gamma: float):
        ContrastHandler.logger.warning("\tCurrent values (%g,%g,%g), target (%g,%g,%g)" %
                                       (self.MinIntensityCutoff, self.MaxIntensityCutoff, self.Gamma,
                                        MinIntensityCutoff, MaxIntensityCutoff, Gamma))

    def IsContrastMismatched(self, MinIntensityCutoff, MaxIntensityCutoff, Gamma: float):

        OutputNode = nornir_buildmanager.validation.transforms.IsValueMatched(self, 'MinIntensityCutoff',
                                                                              MinIntensityCutoff, 0)
        if OutputNode is None:
            return True

        OutputNode = nornir_buildmanager.validation.transforms.IsValueMatched(self, 'MaxIntensityCutoff',
                                                                              MaxIntensityCutoff, 0)
        if OutputNode is None:
            return True

        OutputNode = nornir_buildmanager.validation.transforms.IsValueMatched(self, 'Gamma', Gamma, 3)
        if OutputNode is None:
            return True

        return False

    def RemoveChildrenOnContrastMismatch(self, MinIntensityCutoff, MaxIntensityCutoff, Gamma: float, NodeToRemove=None):
        """Remove nodeToRemove if the Contrast values do not match the passed parameters on nodeToTest
        :return: TilePyramid node if the node was preserved.  None if the node was removed"""

        if NodeToRemove is None:
            NodeToRemove = self

        if isinstance(self, lockable.Lockable):
            if self.Locked:
                if not nornir_buildmanager.validation.transforms.IsValueMatched(self, 'MinIntensityCutoff',
                                                                                MinIntensityCutoff, 0) or \
                        not nornir_buildmanager.validation.transforms.IsValueMatched(self, 'MaxIntensityCutoff',
                                                                                     MaxIntensityCutoff, 0) or \
                        not nornir_buildmanager.validation.transforms.IsValueMatched(self, 'Gamma', Gamma, 3):
                    ContrastHandler.logger.warning("Contrast mismatch ignored due to lock on %s" % self.FullPath)
                    self._LogContrastMismatch(MinIntensityCutoff, MaxIntensityCutoff, Gamma)
                return False

        if nornir_buildmanager.validation.transforms.RemoveOnMismatch(self, 'MinIntensityCutoff', MinIntensityCutoff,
                                                                      Precision=0, NodeToRemove=NodeToRemove) is None:
            return True

        if nornir_buildmanager.validation.transforms.RemoveOnMismatch(self, 'MaxIntensityCutoff', MaxIntensityCutoff,
                                                                      Precision=0, NodeToRemove=NodeToRemove) is None:
            return True

        if nornir_buildmanager.validation.transforms.RemoveOnMismatch(self, 'Gamma', Gamma, Precision=3,
                                                                      NodeToRemove=NodeToRemove) is None:
            return True

        return False
