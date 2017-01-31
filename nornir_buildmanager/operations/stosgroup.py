'''
Created on Dec 15, 2016

@author: James Anderson

Operations to manipulate or report on the volume.

'''
import os
import shutil

from nornir_buildmanager import VolumeManagerETree
import nornir_imageregistration
from nornir_imageregistration.files import stosfile


def CreateStosGroup(GroupName, BlockNode, Downsample, **kwargs):
    (created, stos_group) = BlockNode.GetOrCreateStosGroup(GroupName, Downsample)
    if created:
        print("Created stos group {0} with transforms downsampled by {1}".format(GroupName, str(Downsample)))
        return BlockNode;
    else:
        print("Stos group {0} already exists".format(GroupName));
        return None;

def RemoveStosGroup(GroupName, BlockNode, Downsample, **kwargs):
    removed = BlockNode.RemoveStosGroup(GroupName, Downsample)
    if removed:
        print("Removed Stos group {0} with transforms downsampled by {1}".format(GroupName, str(Downsample)))
        return BlockNode;
    else:
        print("Stos group {0} did not exist".format(GroupName));
        return None;
    
def ListStosGroups(BlockNode, **kwargs):
    
    print("Slice-to-Slice Transform Groups");
    print("");
    sortedGroups = sorted(BlockNode.StosGroups, key=lambda sg: sg.Name)
    
    for group in sortedGroups:
        print(group.SummaryString);
    print("");
    
def ListGroupSectionMappings(BlockNode, GroupName, Downsample, **kwargs):
    
    print("Slice-to-Slice Transform Groups");
    print("");
    StosGroup = BlockNode.GetStosGroup(GroupName, Downsample)
    if StosGroup is None:
        print("No stos group found with name {0}".format(GroupName))
        return
    
    print("{0:s}{1:s}{2:s}".format('Mapped'.ljust(30), 'Control'.ljust(30), 'Type'.ljust(10)))
    print("{0:s}{1:s}{2:s}{3:s}{4:s}{5:s}".format('Section'.ljust(10), 'Channel'.ljust(10), 'Filter'.ljust(10), 'Section'.ljust(10), 'Channel'.ljust(10), 'Filter'.ljust(10)))
    
    for transforms in map(lambda sm: sm.Transforms, sorted(StosGroup.SectionMappings, key=lambda sm: sm.MappedSectionNumber)):
        for t in transforms:
            print("{0:s}{1:s}{2:s}{3:s}{4:s}{5:s}{6:s}".format(repr(t.MappedSectionNumber).ljust(10), t.MappedChannelName.ljust(10), t.MappedFilterName.ljust(10),
                                                          repr(t.ControlSectionNumber).ljust(10), t.ControlChannelName.ljust(10), t.ControlFilterName.ljust(10),
                                                          t.Type.ljust(10)))
    
    print("")
    
def CopyStosGroup(BlockNode, SourceGroupName, TargetGroupName, Downsample, **kwargs):
    '''
    @param Downsample int: The downsample level to copy
    
    Copy the transforms from one stos group to another.  If the target group does not exist it is created.
    '''
    # Get or create the TargetGroup
    (created_source, SourceGroup) = BlockNode.GetOrCreateStosGroup(SourceGroupName, Downsample)
    (created_target, TargetGroup) = BlockNode.GetOrCreateStosGroup(TargetGroupName, Downsample)
    
    for sourceSectionMapping in SourceGroup.SectionMappings:
        (created_mapping, targetSectionMapping) = TargetGroup.GetOrCreateSectionMapping(sourceSectionMapping.MappedSectionNumber)
        CopySectionMappingTransforms(sourceSectionMapping, targetSectionMapping)

    return TargetGroup
        
        
def CopySectionMappingTransforms(SourceMapping, TargetMapping):
    '''
    Copy the transforms from the source mapping to the target mapping
    ''' 
    SourceGroup = SourceMapping.Parent
    TargetGroup = TargetMapping.Parent
    BlockNode = SourceGroup.FindParent('Block')
    
    for source_transform in SourceMapping.Transforms:
        # Copy the file that 
        ControlFilter = BlockNode.GetSection(source_transform.ControlSectionNumber).GetChannel(source_transform.ControlChannelName).GetFilter(source_transform.ControlFilterName)
        MappedFilter = BlockNode.GetSection(source_transform.MappedSectionNumber).GetChannel(source_transform.MappedChannelName).GetFilter(source_transform.MappedFilterName)
        
        # Remove the existing transform and replace it
        TargetTransform = TargetGroup.GetStosTransformNode(ControlFilter, MappedFilter)
        if not TargetTransform is None:
            TargetTransform.Clean()
            TargetTransform = None
        
        # Copy the transform file itself
        SourceStosFullpath = source_transform.FullPath
        TargetStosFullpath = os.path.join(TargetMapping.Parent.FullPath, os.path.basename(SourceStosFullpath))    
        shutil.copy(SourceStosFullpath, TargetStosFullpath)
        
        TargetGroup.GetOrCreateStosTransformNode(ControlFilter, MappedFilter, source_transform.Type, TargetStosFullpath)
        

def ImportStos(InputStosFullpath, BlockNode, GroupName,
                ControlSectionNumber, ControlChannelName, ControlFilterName, ControlDownsample,
                MappedSectionNumber, MappedChannelName, MappedFilterName, MappedDownsample, Type, **kwargs):
    '''
    Import a .stos file into the stos group.
    
    Corrects any difference between downsampling of control and mapped images in the transform.  The control downsample level 
    is maintained, and the mapped points are transformed to match the control space.
    '''
    
    StosGroup = BlockNode.GetStosGroup(GroupName, ControlDownsample)
    if StosGroup is None:
        print("No stos group found with name {0}".format(GroupName))
        return
    
    # Copy the original stos file to a subdirectory under the StosGroup
    OriginalsFullPath = os.path.join(StosGroup.FullPath, 'Originals') 
    if not os.path.exists(OriginalsFullPath):
        os.makedirs(OriginalsFullPath)
        
    OriginalCopyFullPath = os.path.join(OriginalsFullPath, os.path.basename(InputStosFullpath))
    shutil.copy(InputStosFullpath, OriginalCopyFullPath)
    
    # Create the Transform meta-data and copy the file into the StosGroup
    ControlFilter = BlockNode.GetSection(ControlSectionNumber).GetChannel(ControlChannelName).GetFilter(ControlFilterName)
    MappedFilter = BlockNode.GetSection(MappedSectionNumber).GetChannel(MappedChannelName).GetFilter(MappedFilterName)
    
    OutputFilename = VolumeManagerETree.StosGroupNode.GenerateStosFilename(ControlFilter, MappedFilter)
    OutputFileFullPath = os.path.join(StosGroup.FullPath, OutputFilename)
    
    # Copy the STOS file into the StosGroup directory
    if(ControlDownsample != MappedDownsample):
        # Adjust the mappedDownsample to match the control downsample
        stos = stosfile.StosFile.Load(InputStosFullpath)
        adjustedStos = stos.EqualizeStosGridPixelSpacing(ControlDownsample, MappedDownsample,
                                          MappedFilter.GetOrCreateImage(ControlDownsample).FullPath,
                                          MappedFilter.GetOrCreateMaskImage(ControlDownsample).FullPath,
                                          False)
        adjustedStos.Save(OutputFileFullPath, True)
    else:
        shutil.copy(InputStosFullpath, OutputFileFullPath)
     
    StosGroup.GetOrCreateSectionMapping(MappedSectionNumber)
    stosTransformNode = StosGroup.GetOrCreateStosTransformNode(ControlFilter, MappedFilter, Type, OutputFilename)
    
    return StosGroup
    

if __name__ == '__main__':
    pass
