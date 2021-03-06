'''
Created on Apr 15, 2013

@author: u0490822
'''

import os

import nornir_buildmanager.VolumeManagerETree as VolumeManagerETree
import nornir_shared.misc as misc


def GetOrCreateImageNodeHelper(ParentNode, OutputImageName,  InputTransformNode=None):
    # Create a node in the XML records
    Created = False
    OverlayImageNode = ParentNode.GetChildByAttrib('Image', 'Path', os.path.basename(OutputImageName))
    if not OverlayImageNode is None:
        cleaned = OverlayImageNode.CleanIfInvalid()
        if cleaned:
            OverlayImageNode = None
            
        if not InputTransformNode is None:
            if InputTransformNode.CleanIfInputTransformMismatched(InputTransformNode):
                OverlayImageNode = None

    if OverlayImageNode is None:
        OverlayImageNode = VolumeManagerETree.ImageNode.Create(os.path.basename(OutputImageName))
        OverlayImageNode.SetTransform(InputTransformNode)
        ParentNode.append(OverlayImageNode)
        Created = True

    return (Created, OverlayImageNode)


def GetOrCreateHistogramNodeHelper(ParentNode, DataPath, ImagePath, InputTransformNode=None):
    # Create a node in the XML records
    Created = False
    histogramNode = ParentNode.find('Histogram')
    if not histogramNode is None:
        cleaned = histogramNode.CleanIfInvalid()
        if cleaned:
            histogramNode = None
            
        if not InputTransformNode is None:
            if InputTransformNode.CleanIfInputTransformMismatched(InputTransformNode):
                histogramNode = None

    if histogramNode is None:
        histogramNode = VolumeManagerETree.HistogramNode.Create(InputTransformNode, None)
        DataNode = VolumeManagerETree.DataNode.Create(os.path.basename(DataPath))
        histogramNode.append(DataNode)
        
        ImageNode = VolumeManagerETree.ImageNode.Create(os.path.basename(ImagePath))
        histogramNode.append(ImageNode)
        
        histogramNode.SetTransform(InputTransformNode)
        ParentNode.append(histogramNode)
        Created = True

    return (Created, histogramNode)

def GetOrCreateNodeHelper(ParentNode, tag, Path, InputTransformNode=None, CreateFunc=None, ReplaceFunc=None):
    '''
    This function gets or creates a node.  If the node exists and does not pass the criteria function it will be removed.
    :param Element ParentNode: Element we are creating the node under
    :param str tag: Element tag name
    :param str Path: Path to the file resource
    :param TransformNode InputTransformNode: The transform node the node we are creating is expected to refer to
    :param function CreateFunc: Function to call if a new node is created and needs to be initialized
    :param function ReplaceFunc: Function to call if an existing node is found that does not pass the test
    '''
    raise NotImplemented()
    

def CreateLevelNodes(ParentNode, DownsampleLevels):
    DownsampleLevels = misc.SortedListFromDelimited(DownsampleLevels)

    LevelsCreated = []
    for level in DownsampleLevels:
        LevelNode = ParentNode.GetChildByAttrib('Level', 'Downsample', level)
        if LevelNode is None:
            LevelNode = VolumeManagerETree.LevelNode.Create(level)
            [added, LevelNode] = ParentNode.UpdateOrAddChildByAttrib(LevelNode, 'Downsample')
            LevelsCreated.append(LevelNode)

        os.makedirs(LevelNode.FullPath, exist_ok=True)

    return LevelsCreated


def CreateImageSetForImage(ParentNode, ImageFullPath, Downsample=1, **attribs):
    if attribs is None:
        attribs = {}

    Type = ""
    if 'Type' in attribs:
        Type = attribs['Type']
        del attribs['Type']

    ImageSetNode = VolumeManagerETree.ImageSetNode.Create(Type, attrib=attribs);
    [NewImageSetNode, ImageSetNode] = ParentNode.UpdateOrAddChildByAttrib(ImageSetNode, 'Path');

    os.makedirs(ImageSetNode.FullPath, exist_ok=True)

    CreateLevelNodes(ImageSetNode, Downsample)

    LevelNode = ImageSetNode.GetChildByAttrib('Level', 'Downsample', Downsample)

    imagePath = os.path.basename(ImageFullPath)

    imagenode = VolumeManagerETree.ImageNode.Create(imagePath)
    [nodecreated, imagenode] = LevelNode.UpdateOrAddChildByAttrib(imagenode, 'Path')

    return ImageSetNode


def FindSectionImageSet(BlockNode, SectionNumber, ImageSetName, Downsample):
    '''Find the first image matching the criteria'''
    InputImageSetXPathTemplate = "Section[@Number='%(SectionNumber)s']/Channel/Filter/ImageSet[@Name='%(ImageSetName)s']"
    InputImageXPathTemplate = "Level[@Downsample='%(Downsample)d']/Image"

    ImageSets = list(BlockNode.findall(InputImageSetXPathTemplate % {'SectionNumber' : SectionNumber,
                                                                       'ImageSetName' : ImageSetName}))
    for ImageSet in ImageSets:
        ImageXPath = InputImageXPathTemplate % {'Downsample' : Downsample}
        ImageNode = ImageSet.find(ImageXPath)
        return ImageSet

    return None


def MaskImageNodeForImageSet(ImageSetNode, Downsample):
    '''Return a mask image for an image set/downsample combo'''

    if hasattr(ImageSetNode, 'MaskName'):
        MappedMaskSetNode = ImageSetNode.Parent.GetChildByAttrib('ImageSet', 'Name', ImageSetNode.MaskName)

        if not MappedMaskSetNode is None:
            MaskImageXPathTemplate = "Level[@Downsample='%(Downsample)d']/Image"
            MaskImageXPath = MaskImageXPathTemplate % {'Downsample' : Downsample}
            return MappedMaskSetNode.find(MaskImageXPath)

    return None


if __name__ == '__main__':
    pass
