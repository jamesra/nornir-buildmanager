'''
Created on Apr 15, 2013

@author: u0490822
'''
import unittest
import setup_pipeline
import glob
import os
import nornir_buildmanager.VolumeManagerETree
import nornir_buildmanager.importers.sectionimage as sectionimage
from setup_pipeline import VolumeEntry


class ImportLMImages(setup_pipeline.PipelineTest):

    @property
    def Platform(self):
        return "PNG"

    @property
    def VolumePath(self):
        return "6872"

    def LoadVolumeObj(self):
        return nornir_buildmanager.VolumeManagerETree.VolumeManager.Load(self.VolumeDir, Create=True)

    def setUp(self):
        super(ImportLMImages, self).setUp()

        ImportDir = os.path.join(self.PlatformFullPath, self.VolumePath)
        VolumeObj = self.LoadVolumeObj()
        sectionimage.SectionImage.ToMosaic(VolumeObj, InputPath=ImportDir, OutputPath=self.VolumeDir, debug=True)
        VolumeObj.Save()
        del VolumeObj


class testImportPNG(ImportLMImages):

    def test(self):

        listExpectedEntries = [VolumeEntry("Block", "Name", 6872),
                               VolumeEntry("Section", "Number", 1),
                               VolumeEntry("Channel", "Name", "gfp"),
                               VolumeEntry("Filter", "Name", "mosaic")]


        setup_pipeline.VerifyVolume(self, self.VolumeDir, listExpectedEntries)


class testManipulateImageVolume(setup_pipeline.PipelineTest):
    '''Imports a set of images and then tests stos operations'''

#    def test(self):
#        '''OK, run the TEMStos stage on the imported images'''
#






if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()