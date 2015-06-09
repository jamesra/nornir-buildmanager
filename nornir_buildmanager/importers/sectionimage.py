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

from filenameparser import ParseFilename, mapping
from nornir_buildmanager import metadatautils
from nornir_buildmanager.VolumeManagerETree import *
from nornir_buildmanager.importers import filenameparser
import nornir_shared.prettyoutput as prettyoutput
import nornir_shared.files


imageNameMappings = [mapping('Section', typefunc=int),
                     mapping('Channel', typefunc=str),
                     mapping('Filter', typefunc=str, default=None),
                     mapping('ImageSetName', typefunc=str, default=None),
                     mapping('Downsample', typefunc=int, default=1)]

def FillInMissingImageNameData(imageData):
    '''Not all image names will have the essential information on where the image belongs.
       The image name mapping system is not terribly sophisticated.  This
       function uses what is known to fill in the blanks'''

    try:
        imagesetisdownsample = int(imageData.ImageSetName)
        if imageData.Downsample == 1:
            Downsample = imagesetisdownsample
            imageData.ImageSetName = None
    except:
        pass

    if imageData.ImageSetName is None:
        imageData.ImageSetName = 'image'

    if imageData.Filter is None:
        imageData.Filter = imageData.Channel
        imageData.Channel = 'Image'

    return imageData



def Import(VolumeElement, ImportPath, extension, *args, **kwargs):
    '''Import the specified directory into the volume'''
        
    DirList = nornir_shared.files.RecurseSubdirectoriesGenerator(ImportPath, RequiredFiles="*." + extension)
    for path in DirList:
        for idocFullPath in glob.glob(os.path.join(path, '*.' + extension)):
            yield SectionImage.ToMosaic(VolumeElement, idocFullPath, VolumeElement.FullPath)
    


class SectionImage(object):
    '''
    Import sections represented by a single image
    '''


    def __init__(self):
        '''
        Constructor
        '''

    @classmethod
    def ToMosaic(cls, VolumeObj, InputPath, OutputPath=None, Extension=None, OutputImageExt=None, TileOverlap=None, TargetBpp=None, debug=None):

        '''#Converts a directory of images to sections, each represented by a single image.
           Each image should have the format Section#_ChannelText'''

        if Extension is None:
            Extension = 'png'

        # Default to the directory above ours if an output path is not specified
        if OutputPath is None:
            OutputPath = os.path.join(InputPath, "..");

        # If the user did not supply a value, use a default
        if(TileOverlap is None):
            TileOverlap = 0.10;

        if (TargetBpp is None):
            TargetBpp = 8;

        # Report the current stage to the user
        # prettyoutput.CurseString('Stage', "PMGToMosaic " + InputPath);

        # Find the files with a .pmg extension
        imageFiles = glob.glob(os.path.join(InputPath, '*.' + Extension));
        if(len(imageFiles) == 0):
            # This shouldn't happen, but just in case
            assert len(imageFiles) > 0, "ToMosaic called without proper target file present in the path: " + str(InputPath);
            return;

        ParentDir = os.path.dirname(InputPath);
        sectionDir = os.path.basename(InputPath);

        SectionNumber = None;
        ChannelName = None;

        for filename in imageFiles:

            if 'histogram' in filename.lower():
                prettyoutput.Log("PNG importer ignoring probable histogram file: " + filename)
                continue

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
                raise Exception("Could not parse section from PMG filename: " + filename);

            if fileData.ImageSetName is None:
                fileData.ImageSetName = "image"

            SectionNumber = fileData.Section;
            sectionObj = SectionNode(SectionNumber);

            [addedSection, sectionObj] = BlockObj.UpdateOrAddChildByAttrib(sectionObj, 'Number')

            # Calculate our output directory.  The scripts expect directories to have section numbers, so use that.
            SectionPath = os.path.join(OutputPath, '{:04d}'.format(SectionNumber));

            ChannelName = fileData.Channel
            ChannelName = ChannelName.replace(' ', '_')
            channelObj = ChannelNode(ChannelName)
            [channelAdded, channelObj] = sectionObj.UpdateOrAddChildByAttrib(channelObj, 'Name')

            # Create a filter for the images
            # Create a filter and mosaic
            FilterName = fileData.Filter
            if(TargetBpp is None):
                FilterName = 'Raw';

            (added_filter, filterObj) = channelObj.GetOrCreateFilter(FilterName);
            filterObj.BitsPerPixel = TargetBpp

            # Create an image for the filter
            ImageSetNode = metadatautils.CreateImageSetForImage(filterObj, fileData.ImageSetName, filename, Downsample=fileData.Downsample)

            # Find the image node
            levelNode = ImageSetNode.GetChildByAttrib('Level', 'Downsample', fileData.Downsample)
            imageNode = levelNode.GetChildByAttrib('Image', 'Path', imagebasename)

            if not os.path.exists(os.path.dirname(imageNode.FullPath)):
                os.makedirs(os.path.dirname(imageNode.FullPath))

            if not os.path.exists(os.path.dirname(imageNode.FullPath)):
                os.makedirs(os.path.dirname(imageNode.FullPath))

            prettyoutput.Log("Copying file: " + imageNode.FullPath)
            shutil.copy(filename, imageNode.FullPath)

        return [SectionNumber, ChannelName];
