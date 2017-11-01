'''
Created on Apr 15, 2013

@author: u0490822
'''

'''Image files are expected to have this naming convention:
    Section#_Channel_Comments'''

import glob
import logging
import os
import shutil
import sys

from nornir_buildmanager import metadatautils
from nornir_buildmanager.VolumeManagerETree import *
from nornir_buildmanager.importers import filenameparser
import nornir_shared.files

from filenameparser import ParseFilename, mapping
import nornir_shared.prettyoutput as prettyoutput

imageNameMappings = [mapping('Section', typefunc=int),
                     mapping('Channel', typefunc=str),
                     mapping('Filter', typefunc=str, default=None), 
                     mapping('Downsample', typefunc=int, default=1)]

def FillInMissingImageNameData(imageData):
    '''Not all image names will have the essential information on where the image belongs.
       The image name mapping system is not terribly sophisticated.  This
       function uses what is known to fill in the blanks'''

    if imageData.Filter is None:
        imageData.Filter = 'Imported'

    return imageData


def Import(VolumeElement, ImportPath, scaleValueInNm, extension=None, *args, **kwargs):
    '''Import the specified directory into the volume'''
    if extension is None:
        extension = 'png'

    DirList = nornir_shared.files.RecurseSubdirectoriesGenerator(ImportPath, RequiredFiles="*.%s" % extension)
    for path in DirList:
        for idocFullPath in glob.glob(os.path.join(path, '*.' + extension)):
            result = SectionImage.ToMosaic(VolumeElement, idocFullPath, scaleValueInNm, VolumeElement.FullPath, *args, **kwargs)
            if result:
                yield result


class SectionImage(object):
    '''
    Import sections represented by a single image
    '''
    
    def __init__(self):
        '''
        Constructor
        '''

    @classmethod
    def ToMosaic(cls, VolumeObj, InputPath, scaleValueInNm, OutputPath=None, OutputImageExt=None, TargetBpp=None, debug=None):

        '''#Converts a directory of images to sections, each represented by a single image.
           Each image should have the format Section#_ChannelText'''


        # Default to the directory above ours if an output path is not specified
        if OutputPath is None:
            OutputPath = os.path.join(InputPath, "..")

        # If the user did not supply a value, use a default
        if (TargetBpp is None):
            TargetBpp = 8

        # Report the current stage to the user
        # prettyoutput.CurseString('Stage', "PMGToMosaic " + InputPath);

        # Find the files with a .pmg extension
        filename = InputPath
 
        if 'histogram' in filename.lower():
            prettyoutput.Log("PNG importer ignoring probable histogram file: " + filename)
            return None

        # TODO wrap in try except and print nice error on badly named files?
        fileData = filenameparser.ParseFilename(filename, imageNameMappings)
        fileData = FillInMissingImageNameData(fileData)

        imageDir = os.path.dirname(filename);
        imagebasename = os.path.basename(filename)

        BlockName = os.path.basename(imageDir)
        if BlockName is None or len(BlockName) == 0:
            BlockName = 'LM'

        BlockObj = BlockNode(BlockName);
        [addedBlock, BlockObj] = VolumeObj.UpdateOrAddChild(BlockObj);

        if(fileData is None):
            raise Exception("Could not parse section from PMG filename: " + filename)
 
        SectionNumber = fileData.Section
        sectionObj = SectionNode(SectionNumber)

        [addedSection, sectionObj] = BlockObj.UpdateOrAddChildByAttrib(sectionObj, 'Number')

        ChannelName = fileData.Channel
        ChannelName = ChannelName.replace(' ', '_')
        channelObj = ChannelNode(ChannelName)
        channelObj.SetScale(scaleValueInNm)
        [channelAdded, channelObj] = sectionObj.UpdateOrAddChildByAttrib(channelObj, 'Name')

        # Create a filter for the images
        # Create a filter and mosaic
        FilterName = fileData.Filter
        if FilterName is None:
            if(TargetBpp is None):
                FilterName = 'Import'
            else:
                FilterName = 'Import' + str(TargetBpp)

        (added_filter, filterObj) = channelObj.GetOrCreateFilter(FilterName)
        filterObj.BitsPerPixel = TargetBpp

        # Create an image for the filter
        ImageSetNode = metadatautils.CreateImageSetForImage(filterObj, filename, Downsample=fileData.Downsample)

        # Find the image node
        levelNode = ImageSetNode.GetChildByAttrib('Level', 'Downsample', fileData.Downsample)
        imageNode = levelNode.GetChildByAttrib('Image', 'Path', imagebasename)

        if not os.path.exists(os.path.dirname(imageNode.FullPath)):
            os.makedirs(os.path.dirname(imageNode.FullPath))

        if not os.path.exists(os.path.dirname(imageNode.FullPath)):
            os.makedirs(os.path.dirname(imageNode.FullPath))

        prettyoutput.Log("Copying file: " + imageNode.FullPath)
        shutil.copy(filename, imageNode.FullPath)

        if addedBlock:
            return VolumeObj
        elif addedSection:
            return BlockObj
        elif channelAdded:
            return sectionObj
        else:
            return channelObj