'''
Created on Apr 15, 2013

@author: u0490822
'''
import glob
import os
import unittest

import nornir_buildmanager.VolumeManagerETree

import nornir_buildmanager.importers.sectionimage as sectionimage
from setup_pipeline import VolumeEntry
import setup_pipeline


class ImportLMImages(setup_pipeline.PlatformTest):

    @property
    def Platform(self):
        return "PNG"

    @property
    def VolumePath(self):
        return "6872"
 
    def setUp(self):
        super(ImportLMImages, self).setUp()

        ImportDir = os.path.join(self.PlatformFullPath, self.VolumePath)
        VolumeObj = self.LoadOrCreateVolume()
        sectionimage.SectionImage.ToMosaic(VolumeObj, InputPath=ImportDir, OutputPath=self.TestOutputPath, debug=True)
        VolumeObj.Save()
        del VolumeObj


class testImportPNG(ImportLMImages):

    def test(self):

        listExpectedEntries = [VolumeEntry("Block", "Name", 6872),
                               VolumeEntry("Section", "Number", 1),
                               VolumeEntry("Channel", "Name", "gfp"),
                               VolumeEntry("Filter", "Name", "mosaic")]


        setup_pipeline.VerifyVolume(self, self.TestOutputPath, listExpectedEntries)

#
# class testManipulateImageVolume(setup_pipeline.PipelineTest):
#     '''Imports a set of images and then tests stos operations'''
#
#     @property
#     def VolumePath(self):
#         return "6872"
#
#     @property
#     def Platform(self):
#         return "PMG"
#
# #    def test(self):
# #        '''OK, run the TEMStos stage on the imported images'''
# #
#
#
#



if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
