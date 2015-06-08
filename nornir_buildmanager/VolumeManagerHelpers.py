'''
Created on Oct 11, 2012

@author: u0490822
'''

import re

import VolumeManagerETree as VolumeManager
from operator import attrgetter
import nornir_shared.misc
import nornir_buildmanager.validation.transforms
import logging

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
 

class Lockable(object):
    
    @property
    def Locked(self):
        '''
        Return true if the node is locked and should not be deleted
        '''
        return bool(int(self.attrib.get('Locked', False)))
    
    @Locked.setter
    def Locked(self, value):
        
        if value is None:
            if 'Locked' in self.attrib:
                del self.attrib['Locked'] 
            return
        
        assert(isinstance(value, bool))
        self.attrib['Locked'] = "%d" % value
        
class ContrastHandler(object):
    
    logger = logging.getLogger(__name__ + '.' + 'XElementWrapper')
    
    @property
    def MaxIntensityCutoff(self):
        if 'MaxIntensityCutoff' in self.attrib:
            return round(float(self.attrib['MaxIntensityCutoff']),3)

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
            return round(float(self.attrib['MinIntensityCutoff']),3)
        return None
    
    @MinIntensityCutoff.setter
    def MinIntensityCutoff(self, value):
        if value is None:
            if 'MinIntensityCutoff' in self.attrib:
                del self.attrib['MinIntensityCutoff']
        else:
            self.attrib['MinIntensityCutoff'] = "%g" % round(value, 3)    

    @property
    def Gamma(self):
        if 'Gamma' in self.attrib:
            return round(float(self.attrib['Gamma']),3)
        return None
    
    @Gamma.setter
    def Gamma(self, value):
        if value is None:
            if 'Gamma' in self.attrib:
                del self.attrib['Gamma']
        else:
            self.attrib['Gamma'] = "%g" % round(value, 3)
            
    def SetContrastValues(self, MinIntensityCutoff, MaxIntensityCutoff, Gamma):
        self.MinIntensityCutoff = MinIntensityCutoff
        self.MaxIntensityCutoff = MaxIntensityCutoff
        self.Gamma = Gamma
        
    def CopyContrastValues(self, node):
        '''Copy the contrast values from the passed node into ourselves'''
        if node is None:
            self.MinIntensityCutoff = None
            self.MaxIntensityCutoff = None
            self.Gamma = None
        else:
            self.MinIntensityCutoff = node.MinIntensityCutoff
            self.MaxIntensityCutoff = node.MaxIntensityCutoff
            self.Gamma = node.Gamma
        
    
    def _LogContrastMismatch(self, MinIntensityCutoff, MaxIntensityCutoff, Gamma):
        ContrastHandler.logger.warn("\tCurrent values (%g,%g,%g), target (%g,%g,%g)" % (self.MinIntensityCutoff, self.MaxIntensityCutoff, self.Gamma, MinIntensityCutoff, MaxIntensityCutoff, Gamma))
    
    def IsContrastMismatched(self, MinIntensityCutoff, MaxIntensityCutoff, Gamma):
        
        OutputNode = nornir_buildmanager.validation.transforms.IsValueMatched(self, 'MinIntensityCutoff', MinIntensityCutoff, 0)
        if OutputNode is None:
            return True
        
        OutputNode = nornir_buildmanager.validation.transforms.IsValueMatched(self, 'MaxIntensityCutoff', MaxIntensityCutoff, 0)
        if OutputNode is None:
            return True
        
        OutputNode = nornir_buildmanager.validation.transforms.IsValueMatched(self, 'Gamma', Gamma, 3)
        if OutputNode is None:
            return True
        
        return False
        
    def RemoveNodeOnContrastMismatch(self, MinIntensityCutoff, MaxIntensityCutoff, Gamma, NodeToRemove=None):
        '''Remove nodeToRemove if the Contrast values do not match the passed parameters on nodeToTest
        :return: TilePyramid node if the node was preserved.  None if the node was removed'''
        
        if NodeToRemove is None:
            NodeToRemove = self
            
        
        if isinstance(self, Lockable):
            if self.Locked:
                if not nornir_buildmanager.validation.transforms.IsValueMatched(self, 'MinIntensityCutoff', MinIntensityCutoff, 0) or \
                   not nornir_buildmanager.validation.transforms.IsValueMatched(self, 'MaxIntensityCutoff', MaxIntensityCutoff, 0) or \
                   not nornir_buildmanager.validation.transforms.IsValueMatched(self, 'Gamma', Gamma, 3):
                    ContrastHandler.logger.warn("Contrast mismatch ignored due to lock on %s" % self.FullPath)
                    self._LogContrastMismatch(MinIntensityCutoff, MaxIntensityCutoff, Gamma)
                return False
        
        if nornir_buildmanager.validation.transforms.RemoveOnMismatch(self, 'MinIntensityCutoff', MinIntensityCutoff, Precision=0, NodeToRemove=NodeToRemove) is None:
            return True
         
        if nornir_buildmanager.validation.transforms.RemoveOnMismatch(self, 'MaxIntensityCutoff', MaxIntensityCutoff, Precision=0, NodeToRemove=NodeToRemove) is None:
            return True
         
        if nornir_buildmanager.validation.transforms.RemoveOnMismatch(self, 'Gamma', Gamma, Precision=3, NodeToRemove=NodeToRemove) is None:
            return True
        
        return False
        

class InputTransformHandler(object):
    
    @property
    def HasInputTransform(self):
        return not self.InputTransformChecksum is None

    def SetTransform(self, transform_node):
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
        
    
    def IsInputTransformMatched(self, transform_node):
        '''Return true if the transform node matches our input transform'''
        return self.InputTransform == transform_node.Name and \
                self.InputTransformType == transform_node.Type and \
                self.InputTransformChecksum == transform_node.Checksum and \
                self.InputTransformCropBox == transform_node.CropBox
      
    
    def CleanIfInputTransformMismatched(self, transform_node):
        '''Remove this element from its parent if the transform node does not match our input transform attributes
        :return: True if element removed from parent, otherwise false
        :rtype: bool
        '''
        if not self.IsInputTransformMatched(transform_node):
            self.Clean("Input transform %s did not match" % transform_node.FullPath) 
            return True
        
        return False
    
    @property
    def InputTransform(self):
        if 'InputTransform' in self.attrib:
            return self.attrib['InputTransform']
        
        return None 
    
    @InputTransform.setter
    def InputTransform(self, value):
        if value is None:
            if 'InputTransform' in self.attrib:
                del self.attrib['InputTransform']
        else:
            assert(isinstance(value,str))
            self.attrib['InputTransform'] = value


    @property
    def InputTransformChecksum(self):
        if 'InputTransformChecksum' in self.attrib:
            return self.attrib['InputTransformChecksum']
        
        return None

    @InputTransformChecksum.setter
    def InputTransformChecksum(self, value):
        if value is None:
            if 'InputTransformChecksum' in self.attrib:
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
        if value is None:
            if 'InputTransformType' in self.attrib:
                del self.attrib['InputTransformType']
        else:
            assert(isinstance(value,str))
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
                self.logger.warning('Expected input transform not found.  This can occur when the transform lives in a different channel.  Leaving node alone: ' + self.ToElementString())
                return True

            if not self.IsInputTransformMatched(InputTransformNode):
                return [False, 'Input Transform mismatch']

        return True
    
    @classmethod
    def EnumerateTransformDependents(cls, parent_node, checksum, type, recursive):
        '''Return a list of all sibling transforms (Same parent element) which have our checksum and type as an input transform checksum and type'''
        
        #WORKAROUND: The etree implementation has a serious shortcoming in that it cannot handle the 'and' operator in XPath queries.  This function is a workaround for a multiple criteria find query
        if parent_node is None:
            return 
        
        for t in parent_node.findall('*'):
            if recursive:
                for c in cls.EnumerateTransformDependents(t, checksum, type, recursive):
                    yield c
            
            if not 'InputTransformChecksum' in t.attrib:
                continue 
            
            if not t.InputTransformChecksum == checksum:
                continue 
            
            if 'InputTransformType' in t.attrib:
                if not t.InputTransformType == type:
                    continue
                
            yield t
            
        return

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

    @property
    def HasLevels(self):
        '''
        :return: true if the pyramid has any levels
        '''
        return len(self.Levels) > 0
    
    def HasLevel(self, Downsample):
        return not self.GetLevel(Downsample) is None
    
    def CanGenerate(self, Downsample):
        '''
        :return: True if this level could be generated from higher resolution levels
        '''
        return not self.MoreDetailedLevel(Downsample) is None

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