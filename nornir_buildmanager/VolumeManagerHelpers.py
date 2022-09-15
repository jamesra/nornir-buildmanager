"""
Created on Oct 11, 2012

@author: u0490822
"""

import logging
from operator import attrgetter
import re
 
import nornir_buildmanager.VolumeManagerETree
import nornir_shared.misc

import nornir_shared.prettyoutput as prettyoutput

  
def IsMatch(in_string, RegExStr, CaseSensitive=False) -> bool:
    """
    :returns: true if the in_string matches the regular expression string
    """
    if RegExStr == '*':
        return True

    flags = 0
    if not CaseSensitive:
        flags = re.IGNORECASE

    match = re.match(RegExStr, in_string, flags)
    if match is not None:
        return True

    return False


def SearchCollection(Objects, AttribName: str, RegExStr: str, CaseSensitive=False) -> [nornir_buildmanager.VolumeManagerETree.XElementWrapper]:
    """Search a list of object's attributes using a regular express.
       Returns list of objects with matching attributes.
       Returns all entries if RegExStr is None"""

    if RegExStr is None:
        return Objects

    Matches = []

    flags = 0
    if not CaseSensitive:
        flags = re.IGNORECASE

    for MatchObj in Objects:
        if not hasattr(MatchObj, AttribName):
            continue

        Attrib = MatchObj.attrib.get(AttribName, None)
        if Attrib is None:
            continue

        if RegExStr == '*':
            Matches.append(MatchObj)
            continue

        match = re.match(RegExStr, Attrib, flags)
        if match is not None:
            (wrapped, MatchObj) = nornir_buildmanager.VolumeManagerETree.VolumeManager.WrapElement(MatchObj)
            assert (wrapped is False)
            Matches.append(MatchObj)

    return Matches


class Lockable(object):

    @property
    def Locked(self) -> bool:
        """
        Return true if the node is locked and should not be deleted
        """
        return bool(int(self.attrib.get('Locked', False)))

    @Locked.setter
    def Locked(self, value: bool | None):

        if value is None:
            if 'Locked' in self.attrib:
                del self.attrib['Locked']
            return

        assert (isinstance(value, bool))
        self.attrib['Locked'] = "%d" % value


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

        if isinstance(self, Lockable):
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


class InputTransformHandler(object):
    """This can be added as a base class to another element.  It
       adds InputTransformChecksum and various option helper attributes.
    """

    def __init__(self, *args, **kwargs):
        super(InputTransformHandler, self).__init__(*args, **kwargs)
        self.InputTransformCropbox = None

    @property
    def HasInputTransform(self) -> bool:
        return self.InputTransformChecksum is not None

    def SetTransform(self, transform_node: nornir_buildmanager.VolumeManagerETree.TransformNode):
        if transform_node is None:
            self.InputTransformChecksum = None
            self.InputTransformType = None
            self.InputTransform = None
            self.InputTransformCropbox = None
        else:
            self.InputTransformChecksum = transform_node.Checksum
            self.InputTransformType = transform_node.Type
            self.InputTransform = transform_node.Name
            self.InputTransformCropbox = transform_node.CropBox

    def IsInputTransformMatched(self, transform_node: nornir_buildmanager.VolumeManagerETree.TransformNode) -> bool:
        """Return true if the transform node matches our input transform"""
        if not self.HasInputTransform:
            return transform_node is None

        return self.InputTransform == transform_node.Name and \
               self.InputTransformType == transform_node.Type and \
               self.InputTransformChecksum == transform_node.Checksum and \
               self.InputTransformCropBox == transform_node.CropBox

    def CleanIfInputTransformMismatched(self, transform_node: nornir_buildmanager.VolumeManagerETree.TransformNode) -> bool:
        """Remove this element from its parent if the transform node does not match our input transform attributes
        :return: True if element removed from parent, otherwise false
        :rtype: bool
        """
        if not self.IsInputTransformMatched(transform_node):
            self.Clean("Input transform %s did not match" % transform_node.FullPath)
            return True

        return False

    @property
    def InputTransform(self) -> str | None:
        return self.attrib.get('InputTransform', None)

    @InputTransform.setter
    def InputTransform(self, value):
        if value is None:
            if 'InputTransform' in self.attrib:
                del self.attrib['InputTransform']
        else:
            assert (isinstance(value, str))
            self.attrib['InputTransform'] = value

    @property
    def InputTransformChecksum(self) -> str | None:
        return self.attrib.get('InputTransformChecksum', None)

    @InputTransformChecksum.setter
    def InputTransformChecksum(self, value):
        if value is None:
            if 'InputTransformChecksum' in self.attrib:
                del self.attrib['InputTransformChecksum']
        else:
            self.attrib['InputTransformChecksum'] = value

    @property
    def InputTransformType(self) -> str | None:
        return self.attrib.get('InputTransformType', None)

    @InputTransformType.setter
    def InputTransformType(self, value):
        if value is None:
            if 'InputTransformType' in self.attrib:
                del self.attrib['InputTransformType']
        else:
            assert (isinstance(value, str))
            self.attrib['InputTransformType'] = value

    @property
    def InputTransformCropBox(self):
        """Returns boundaries of transform output if available, otherwise none
           :rtype tuple:
           :return (Xo, Yo, Width, Height):
        """

        if 'InputTransformCropBox' in self.attrib:
            return nornir_shared.misc.ListFromAttribute(self.attrib['InputTransformCropBox'])
        else:
            return None

    @InputTransformCropBox.setter
    def InputTransformCropBox(self, bounds):
        """Sets boundaries in fixed space for output from the transform.
        :param bounds tuple:  (Xo, Yo, Width, Height) or (Width, Height)
        """
        if bounds is None:
            if 'InputTransformCropBox' in self.attrib:
                del self.attrib['InputTransformCropBox']
        elif len(bounds) == 4:
            self.attrib['InputTransformCropBox'] = "%g,%g,%g,%g" % bounds
        elif len(bounds) == 2:
            self.attrib['InputTransformCropBox'] = "0,0,%g,%g" % bounds
        else:
            raise Exception(
                "Invalid argument passed to InputTransformCropBox %s.  Expected 2 or 4 element tuple." % str(bounds))

    def InputTransformNeedsValidation(self) -> (bool, str):
        """
        :return: True if the MetaData indicates the input transform has changed
        and InputTransformIsValid should be called.
        """
        InputTransformType = self.attrib.get('InputTransformType', None)
        if InputTransformType is None:
            return False, "No input transform to validate"

        if self.InputTransformType is None:
            return False, "No input transform to validate"

        if len(self.InputTransformType) == 0:
            return False, "No input transform to validate"

        InputTransformNodes = self.FindAllFromParent("Transform[@Type='" + self.InputTransformType + "']")

        for it in InputTransformNodes:
            if it.NeedsValidation:
                return True, f"Potential Input Transform needs validation: {it.FullPath}"

        return False, "No Input Transforms found requiring validation"

    def InputTransformIsValid(self) -> (bool, str):
        """ Verify that the input transform matches the checksum recorded for the input. """

        InputTransformType = self.attrib.get('InputTransformType', None)
        if InputTransformType is None:
            return True, ""

        if len(self.InputTransformType) > 0:

            # Check all of our transforms with a matching type until we find a match that is valid
            nMatches = 0
            InputTransformNode = self.FindFromParent("Transform[@Type='" + self.InputTransformType + "']")

            # Due to an obnoxious mistake the StosGroupTransforms can have the same input type as the .mosaic files.
            # To work around this we check that the InputTransform is not ourselves
            if InputTransformNode == self:
                return True, "Could not validate input transform that was equal to self"

            # If we find a transform of the same type and no matching checksum the input transform is invalid
            # If there is no input transform we do not call it invalid since the input may exist in a different channel
            # If we find a transform of the same type and matching checksum the input transform is valid

            while InputTransformNode is not None:

                if InputTransformNode is None:
                    self.logger.warning(
                        'Expected input transform not found.  This can occur when the transform lives in a different channel.  Leaving node alone: ' + self.ToElementString())
                    return True, ""
                else:
                    nMatches = nMatches + 1

                if InputTransformNode.CleanIfInvalid():
                    prettyoutput.Log('Check input transform of type: {0}'.format(self.InputTransformType))
                    InputTransformNode = self.FindFromParent("Transform[@Type='" + self.InputTransformType + "']")
                    continue

                if self.IsInputTransformMatched(InputTransformNode):
                    return True, ""
                else:
                    return False, 'Input Transform mismatch'

            if nMatches > 0:  # If we had at least one hit then delete ourselves
                return False, 'Input Transform mismatch'
            else:
                return True, ""

        return True, ""

    @classmethod
    def EnumerateTransformDependents(cls, parent_node, checksum, type_name, recursive):
        """Return a list of all sibling transforms (Same parent element) which have our checksum and type as an input transform checksum and type"""

        # WORKAROUND: The etree implementation has a serious shortcoming in that it cannot handle the 'and' operator in XPath queries.  This function is a workaround for a multiple criteria find query
        if parent_node is None:
            return

        for t in parent_node.findall('*'):
            if recursive:
                for c in cls.EnumerateTransformDependents(t, checksum, type_name, recursive):
                    yield c

            if 'InputTransformChecksum' not in t.attrib:
                continue

            if not t.InputTransformChecksum == checksum:
                continue

            if 'InputTransformType' in t.attrib:
                if not t.InputTransformType == type_name:
                    continue

            yield t

        return


class PyramidLevelHandler(object):

    @property
    def Levels(self) -> [nornir_buildmanager.VolumeManagerETree.LevelNode]:
        return sorted(list(self.findall('Level')), key=attrgetter('Downsample'))

    @property
    def MaxResLevel(self) -> nornir_buildmanager.VolumeManagerETree.LevelNode:
        """Return the level with the highest resolution"""
        return self.Levels[0]

    @property
    def MinResLevel(self) -> nornir_buildmanager.VolumeManagerETree.LevelNode:
        """Return the level with the highest resolution"""
        return self.Levels[-1]

    def GetLevel(self, Downsample: float) -> nornir_buildmanager.VolumeManagerETree.LevelNode:
        """
        :param float Downsample: Level downsample factor from full-res data
        :return: Level node for downsample node
        :rtype LevelNode:
        """
        return self.GetChildByAttrib("Level", "Downsample", "%g" % Downsample)

    def GetOrCreateLevel(self, Downsample, GenerateData: bool = True) -> (bool, nornir_buildmanager.VolumeManagerETree.LevelNode | None):
        """
            :param float Downsample: Level downsample factor from full-res data
            :param bool GenerateData: True if missing data should be generated.  Defaults to true.
            :return: Level node for downsample node
            :rtype LevelNode:
        """

        if GenerateData:
            existingLevel = self.GetLevel(Downsample)
            if existingLevel is None:
                self.GenerateLevels(Downsample)
            elif isinstance(self,
                            nornir_buildmanager.VolumeManagerETree.TilePyramidNode):  # hasattr(self, 'NumberOfTiles'): #If this is a tile pyramid with many images regenerate if the verified tile count != NumberOfTiles.  If there is a mismatch regenerate
                self.TryToMakeLevelValid(existingLevel)

        (added, lnode) = self.UpdateOrAddChildByAttrib(nornir_buildmanager.VolumeManagerETree.LevelNode.Create(Downsample), "Downsample")
        return added, lnode

    def GenerateLevels(self, Levels):
        """Creates data to populate a level of a pyramid.  Derived class should override"""
        raise NotImplementedError('PyramidLevelHandler.GenerateMissingLevel')

    @property
    def HasLevels(self) -> bool:
        """
        :return: true if the pyramid has any levels
        """
        return len(self.Levels) > 0

    def GetScale(self) -> float | None:
        """Return the size of a pixel at full resolution"""

        Parent = self.Parent
        while Parent is not None:
            if Parent.hasattr('Scale'):
                return Parent.Scale

            Parent = self.Parent

        return None

    def HasLevel(self, Downsample) -> bool:
        return not self.GetLevel(Downsample) is None

    def CanGenerate(self, Downsample) -> bool:
        """
        :return: True if this level could be generated from higher resolution levels
        """
        return not self.MoreDetailedLevel(Downsample) is None

    def CreateLevels(self, levels: [float]):
        assert (isinstance(levels, list))
        for level in levels:
            self.GetOrCreateLevel(level)

    def LevelIndex(self, downsample: float) -> int:
        """Returns the index number into the level nodes.  Levels with a lower index are more detailed, i.e. less downsampling.  Higher indicies are less detailed."""
        for i, obj in enumerate(self.Levels):
            if float(downsample) == float(obj.Downsample):
                return i

        raise Exception("Downsample level does not exist in levels")

    def MoreDetailedLevel(self, downsample: float) -> nornir_buildmanager.VolumeManagerETree.LevelNode | None:
        """Return an existing level with more detail than the provided downsample level"""
        BestChoice = None

        LevelList = self.Levels
        LevelList.reverse()

        for Level in LevelList:
            if Level.Downsample < downsample:
                return Level
            else:
                continue

        return BestChoice

    def LessDetailedLevel(self, downsample: float) -> nornir_buildmanager.VolumeManagerETree.LevelNode | None:
        """Return an existing level with less detail than the provided downsample level"""

        for Level in self.Levels:
            if Level.Downsample > downsample:
                return Level
