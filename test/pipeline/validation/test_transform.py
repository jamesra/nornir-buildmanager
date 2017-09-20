'''
Created on Feb 11, 2013

@author: u0490822
'''
import logging
import os
import shutil
import tempfile
import unittest

from nornir_buildmanager.VolumeManagerETree import *
from nornir_buildmanager.validation import transforms
import nornir_shared.files
import nornir_shared.misc
from test.pipeline.setup_pipeline import *

import nornir_buildmanager.build as build


class TransformIsValidTest(PrepareAndMosaicSetup):

    @property
    def VolumePath(self):
        return "6750"

    @property
    def Platform(self):
        return "PMG"

    def LoadMetaData(self):
        '''Updates the object's meta-data variables from disk'''

        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.TestOutputPath)

        self.ChannelData = self.VolumeObj.find('Block/Section/Channel')
        self.assertIsNotNone(self.ChannelData, "Could not locate channel meta-data")

        # OK, by default the transforms should be correct
        self.StageTransform = self.ChannelData.GetChildByAttrib('Transform', 'Name', 'Stage')
        self.PruneTransform = self.ChannelData.GetChildByAttrib('Transform', 'Name', 'Prune')
        self.TranslateTransform = self.ChannelData.GetChildByAttrib('Transform', 'Name', 'Translated_Prune')
        # self.GridTransform = self.ChannelData.GetChildByAttrib('Transform', 'Name', 'Refined_Prune')
        self.GridTransform = self.ChannelData.GetChildByAttrib('Transform', 'Name', 'Grid')

        self.assertIsNotNone(self.StageTransform)
        self.assertIsNotNone(self.PruneTransform)
        self.assertIsNotNone(self.TranslateTransform)
        self.assertIsNotNone(self.GridTransform)
        # self.assertIsNotNone(self.ZeroGridTransform)

    def setUp(self):

        super(TransformIsValidTest, self).setUp()

        self.LoadMetaData()

    def runTest(self):

        self.ValidateAllTransforms(self.ChannelData)

        # self.assertFalse(self.ZeroGridTransform.Checksum == self.GridTransform.Checksum)

        # Make sure the checksum test is able to fail
        self.assertFalse(self.PruneTransform.IsInputTransformMatched(self.TranslateTransform))
        self.assertFalse(self.PruneTransform.IsInputTransformMatched(self.GridTransform))

        # Take turns removing transform files and ensuring they are regenerated in isolation
        TransformNodes = list(self.ChannelData.findall('Transform'))

        for tNode in TransformNodes:
            if not 'InputTransform' in tNode.attrib:
                continue
            
            prechecksum = tNode.Checksum

            os.remove(tNode.FullPath)
            self.Logger.info("Removing transform to see if it regenerates: " + tNode.FullPath)

            InputTransform = tNode.Parent.GetChildByAttrib('Transform', 'Name', tNode.InputTransform)
            self.assertIsNotNone(InputTransform)

            # Find a transform that depends on the transform we just deleted, if it exists
            OutputTransform = tNode.Parent.GetChildByAttrib('Transform', 'InputTransform', tNode.Name)

            # Regenerate the missing transform, but ensure the later transform is untouched.
            # Import the files
            
            # buildArgs = [self.TestOutputPath, '-debug', 'Prune', '-Threshold', '1.0']
            # build.Execute(buildArgs)
            self.RunPrune()

            # buildArgs = [ self.TestOutputPath, '-debug', 'Mosaic', '-InputFilter', 'Leveled']
            # build.Execute(buildArgs)
            self.RunMosaic(Filter="Leveled")

            # Load the meta-data from the volumedata.xml file again
            self.LoadMetaData()

            # Make sure the transforms are still consistent
            self.ValidateAllTransforms(self.ChannelData)
            
            RefreshedTransform = tNode.Parent.GetChildByAttrib('Transform', 'Name', tNode.Name)
            self.assertIsNotNone(RefreshedTransform)

            # Deleted transform should be regenerated.  The checksum should match what the one we deleted.  Downstream transforms should be left alone
            if not OutputTransform is None:
                if prechecksum == RefreshedTransform.Checksum:
                    #Translated transform involves random numbers, so the odds of a matching checksum are low, which triggers a regeneration of grid transform
                    self.assertEqual(nornir_shared.files.NewestFile(tNode.FullPath, OutputTransform.FullPath), tNode.FullPath)
                else:
                    # This is for translate results, so we'll special case this
                    self.assertTrue('translate' in RefreshedTransform.Name, "Translate should be the only transform with a different checksum after regeneration")

            self.Logger.info("Transform regenerates successfully: " + tNode.FullPath)


if __name__ == "__main__":
    # import syssys.argv = ['', 'Test.testIsValid']
    unittest.main()
