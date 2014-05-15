'''
Created on Oct 11, 2012

@author: u0490822
'''

import re

import VolumeManagerETree as VolumeManager
from operator import attrgetter


def IsMatch(in_string, RegExStr, CaseSensitive=False):
    if RegExStr == '*':
        return True

    flags = 0
    if not CaseSensitive:
        flags = re.IGNORECASE

    match = re.match(RegExStr, in_string, flags)
    if not match is None:
        return True

    return False


def SearchCollection(Objects, AttribName, RegExStr, CaseSensitive=False):
    '''Search a list of object's attributes using a regular express.
       Returns list of objects with matching attributes.
       Returns all entries if RegExStr is None'''

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

    @property
    def Levels(self):
        return sorted(list(self.findall('Level')), key=attrgetter('Downsample'))

    @property
    def MaxResLevel(self):
        '''Return the level with the highest resolution'''
        return self.Levels[0]

    @property
    def MinResLevel(self):
        '''Return the level with the highest resolution'''
        return self.Levels[-1]

    def GetLevel(self, Downsample):
        '''
            :param float Downsample: Level downsample factor from full-res data
            :return: Level node for downsample node 
            :rtype LevelNode:
        '''
        return self.GetChildByAttrib("Level", "Downsample", "%g" % Downsample)

    def GetOrCreateLevel(self, Downsample, GenerateData=True):
        '''
            :param float Downsample: Level downsample factor from full-res data
            :param bool Generate: True if missing data should be generated.  Defaults to true.
            :return: Level node for downsample node 
            :rtype LevelNode:
        '''
        if not self.HasLevel(Downsample) and GenerateData:
            self.GenerateLevels(Downsample)

        [added, lnode] = self.UpdateOrAddChildByAttrib(VolumeManager.LevelNode(Downsample), "Downsample")
        return lnode

    def GenerateLevels(self, Levels):
        '''Creates data to populate a level of a pyramid.  Derived class should override'''
        raise NotImplementedError('PyramidLevelHandler.GenerateMissingLevel')

    def HasLevel(self, Downsample):
        return not self.GetLevel(Downsample) is None

    def CreateLevels(self, Levels):
        assert(isinstance(Levels, list))
        for l in Levels:
            self.GetOrCreateLevel(l)

    def LevelIndex(self, Downsample):
        '''Returns the index number into the level nodes.  Levels with a lower index are more detailed, i.e. less downsampling.  Higher indicies are less detailed.'''
        for i, obj in enumerate(self.Levels):
            if float(Downsample) == float(obj.Downsample):
                return i

        raise Exception("Downsample level does not exist in levels")

    def MoreDetailedLevel(self, Downsample):
        '''Return an existing level with more detail than the provided downsample level'''
        BestChoice = None

        LevelList = self.Levels
        LevelList.reverse()

        for Level in LevelList:
            if Level.Downsample < Downsample:
                return Level
            else:
                continue

        return BestChoice

    def LessDetailedLevel(self, Downsample):
        '''Return an existing level with less detail than the provided downsample level'''

        for Level in self.Levels:
            if Level.Downsample > Downsample:
                return Level