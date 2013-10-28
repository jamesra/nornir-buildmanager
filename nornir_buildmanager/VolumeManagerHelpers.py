'''
Created on Oct 11, 2012

@author: u0490822
'''

import VolumeManagerETree as VM
import re


def SearchCollection(Objects, AttribName, RegExStr, CaseSensitive=False):
    '''Search a list of object's attributes using a regular express.
       Returns list of objects with matching attributes.
       Returns all entries if RegExStr is None'''

    if RegExStr is None:
        return Objects

    Matches = []
    
    flags = None
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
        if not match is None:
            Matches.append(MatchObj)

    return Matches


class InputTransformHandler(object):

    def SetTransform(self, TransformNode):
        assert(not TransformNode is None)
        self.InputTransformChecksum = TransformNode.Checksum
        self.InputTransformType = TransformNode.Type
        self.InputTransform = TransformNode.Name

    @property
    def InputTransformChecksum(self):
        return self.attrib['InputTransformChecksum']

    @InputTransformChecksum.setter
    def InputTransformChecksum(self, value):
        self.attrib['InputTransformChecksum'] = value

    @property
    def InputTransformType(self):
        return self.attrib['InputTransformType']

    @InputTransformType.setter
    def InputTransformType(self, value):
        self.attrib['InputTransformType'] = value

    def InputTransformIsValid(self):
        # Verify that the input transform matches the checksum we recorded for the input
        
        InputTransformType = self.attrib.get('InputTransformType', None)
        if InputTransformType is None:
            return True

        if len(self.InputTransformType) > 0:
            InputTransformNode = self.FindFromParent("Transform[@Type='" + self.InputTransformType + "']")
            if InputTransformNode is None:
                self.logger.warning('Expected input transform not found.  Leaving node alone: ' + self.ToElementString())
                return True

            if not (InputTransformNode.Checksum == self.InputTransformChecksum):
                return [False, 'Input Transform checksum mismatch']

        return True


class PyramidLevelHandler(object):

    def GetLevel(self, Downsample):
        return self.GetChildByAttrib("Level", "Downsample", "%g" % Downsample)

    def GetOrCreateLevel(self, Downsample):
        [added, lnode] = self.UpdateOrAddChildByAttrib(VM.LevelNode(Downsample), "Downsample")
        return lnode

    def CreateLevels(self, Levels):
        assert(isinstance(Levels, list))
        for l in Levels:
            self.GetOrCreateLevel(l)

    @property
    def Levels(self):
        return list(self.findall('Level'))

    @property
    def MaxResLevel(self):
        '''Return the level with the highest resolution'''
        OutputLevel = None
        for level in self.findall('Level'):
            if OutputLevel is None:
                OutputLevel = level

            if OutputLevel.Downsample > level.Downsample:
                OutputLevel = level

        return OutputLevel

