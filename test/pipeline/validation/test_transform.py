'''
Created on Feb 11, 2013

@author: u0490822
'''
import unittest

from test.pipeline.setup_pipeline import *

from nornir_buildmanager.volumemanager import *
import nornir_shared.files
import nornir_shared.misc


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

            original_stat = os.stat(tNode.FullPath)
            os.remove(tNode.FullPath)
            self.Logger.info(f"Removing transform to see if it regenerates: {tNode.FullPath}")

            InputTransform = tNode.Parent.GetChildByAttrib('Transform', 'Name', tNode.InputTransform)
            self.assertIsNotNone(InputTransform)

            # Find a transform that depends on the transform we just deleted, if it exists
            OutputTransform = tNode.Parent.GetChildByAttrib('Transform', 'InputTransform', tNode.Name)

            original_output_stat = None
            output_fullpath = None
            if OutputTransform is not None:
                original_output_stat = os.stat(OutputTransform.FullPath)
                output_fullpath = OutputTransform.FullPath

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

            if not os.path.exists(tNode.FullPath):
                raise ValueError(f"Transform {tNode.FullPath} did not regenerate itself")

            if os.stat(tNode.FullPath).st_mtime <= original_stat.st_mtime:
                raise ValueError(f"Transform {tNode.FullPath} did not regenerate itself")

            # Make sure the transforms are still consistent
            self.ValidateAllTransforms(self.ChannelData)

            RefreshedTransform = self.ChannelData.GetChildByAttrib('Transform', 'Name',
                                                                   tNode.Name)  # tNode.Parent.GetChildByAttrib('Transform', 'Name', tNode.Name)
            self.assertIsNotNone(RefreshedTransform)

            # Deleted transform should be regenerated.  The checksum should match what the one we deleted.  Downstream transforms should be left alone
            if not OutputTransform is None:
                new_output_stat = os.stat(output_fullpath)
                output_should_regenerate = prechecksum != RefreshedTransform.Checksum
                if output_should_regenerate:
                    # If the regenerated checksum is equal to the original checksum the downstream transforms should not regenerate

                    # Translated transform involves random numbers, so the odds of a matching checksum are low, which triggers a regeneration of grid transform
                    if new_output_stat.st_mtime <= original_output_stat.st_mtime:
                        raise ValueError(f"Transform {output_fullpath} did not regenerate itself")
                    self.assertEqual(nornir_shared.files.NewestFile(tNode.FullPath, OutputTransform.FullPath),
                                     OutputTransform.FullPath)
                else:
                    if new_output_stat.st_mtime > original_output_stat.st_mtime:
                        raise ValueError(
                            f"Transform {output_fullpath} regenerated itself when the input checksum was unchanged")
                    # The regenerated transform should be the newest, but the downstream transform should not regenerate because checksum is matched
                    self.assertEqual(nornir_shared.files.NewestFile(tNode.FullPath, OutputTransform.FullPath),
                                     tNode.FullPath)

            self.Logger.info("Transform regenerates successfully: " + tNode.FullPath)


if __name__ == "__main__":
    # import syssys.argv = ['', 'Test.testIsValid']
    unittest.main()
