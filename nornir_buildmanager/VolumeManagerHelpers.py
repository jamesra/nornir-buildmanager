'''
Created on Oct 11, 2012

@author: u0490822
'''

import re
import math

import VolumeManagerETree as VolumeManager
from operator import attrgetter
import nornir_shared.misc

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

    def SetTransform(self, transform_node):
        assert(not transform_node is None)
        self.InputTransformChecksum = transform_node.Checksum
        self.InputTransformType = transform_node.Type
        self.InputTransform = transform_node.Name
        
        if not transform_node.CropBox is None:
            self.InputTransformCropbox = transform_node.CropBox            
        
    
    def IsInputTransformMatched(self, transform_node):
        '''Return true if the transform node matches our input transform'''
        return self.InputTransform == transform_node.Name and \
                self.InputTransformType == transform_node.Type and \
                self.InputTransformChecksum == transform_node.Checksum and \
                self.InputTransformCropBox == transform_node.CropBox
      
    
    def RemoveIfTransformMismatched(self, transform_node):
        '''Remove this element from its parent if the transform node does not match our input transform attributes
        :return: True if element removed from parent, otherwise false
        :rtype: bool
        '''
        if not self.IsInputTransformMatched(transform_node):
            self.Clean() 
            return True
        
        return False
    
    @property
    def InputTransform(self):
        if 'InputTransform' in self.attrib:
            return self.attrib['InputTransform']
        
        return None 
    
    @InputTransform.setter
    def InputTransform(self, value):
        assert(isinstance(value,str))
        if value is None and 'InputTransform' in self.attrib:
            del self.attrib['InputTransform']
        else:
            self.attrib['InputTransform'] = value


    @property
    def InputTransformChecksum(self):
        if 'InputTransformChecksum' in self.attrib:
            return self.attrib['InputTransformChecksum']
        
        return None

    @InputTransformChecksum.setter
    def InputTransformChecksum(self, value):
        if value is None and 'InputTransformChecksum' in self.attrib:
            del self.attrib['InputTransformChecksum']
        else:
            self.attrib['InputTransformChecksum'] = value
             

    @property
    def InputTransformType(self):
        if 'InputTransformType' in self.attrib:
            return self.attrib['InputTransformType']
        
        return None 

    @InputTransformType.setter
    def InputTransformType(self, value):
        assert(isinstance(value,str))
        if value is None and 'InputTransformType' in self.attrib:
            del self.attrib['InputTransformType']
        else:
            self.attrib['InputTransformType'] = value
            
    
    @property
    def InputTransformCropBox(self):
        '''Returns boundaries of transform output if available, otherwise none
           :rtype tuple:
           :return (Xo, Yo, Width, Height):
        '''

        if 'InputTransformCropBox' in self.attrib:
            return nornir_shared.misc.ListFromAttribute(self.attrib['InputTransformCropBox'])
        else:
            return None
        
    @InputTransformCropBox.setter
    def InputTransformCropBox(self, bounds):
        '''Sets boundaries in fixed space for output from the transform.
        :param bounds tuple:  (Xo, Yo, Width, Height) or (Width, Height)
        '''
        if len(bounds) == 4:
            self.attrib['InputTransformCropBox'] = "%g,%g,%g,%g" % bounds
        elif len(bounds) == 2:
            self.attrib['InputTransformCropBox'] = "0,0,%g,%g" % bounds
        elif bounds is None:
            if 'InputTransformCropBox' in self.attrib:
                del self.attrib['InputTransformCropBox']
        else:
            raise Exception("Invalid argument passed to InputTransformCropBox %s.  Expected 2 or 4 element tuple." % str(bounds))

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