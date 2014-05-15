'''
Created on Mar 15, 2013

@author: u0490822
'''
import unittest

from nornir_buildmanager.VolumeManagerETree import *
import nornir_buildmanager.build as build
import setup_pipeline


class LMBuildTest(setup_pipeline.PlatformTest):

    @property
    def VolumePath(self):
        return "6259_small"

    @property
    def Platform(self):
        return "PMG"

class ShadingCorrectionTest(LMBuildTest):

    def testLMBuild(self):

        # Import the files
        buildArgs = ['-debug', '-verbose', 'import', self.ImportedDataPath, '-volume', self.TestOutputPath]
        build.Execute(buildArgs)

        buildArgs = ['-debug', '-verbose', 'ShadeCorrect', '-volume', self.TestOutputPath, '-Correction', 'brightfield']
        build.Execute(buildArgs)

        self.assertTrue(os.path.exists(self.TestOutputPath), "Test input was not copied")

        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.TestOutputPath)
        self.assertIsNotNone(self.VolumeObj)

        # Make sure a shading corrected filter exists
        ShadingCorrectedFilters = list(self.VolumeObj.findall("Block/Section/Channel/Filter[@Name='ShadingCorrected']"))
        Channels = list(self.VolumeObj.findall("Block/Section/Channel"))

        # We expect each folder to be a seperate channel, so make sure each channel has a ShadingCorrectedFilter
        self.assertEqual(len(ShadingCorrectedFilters), len(os.listdir(self.ImportedDataPath)))
        self.assertEqual(len(ShadingCorrectedFilters), len(Channels))


        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.TestOutputPath)

        # TODO: Much more to do here, but out of time for today...
        pass


if __name__ == "__main__":
    # import syssys.argv = ['', 'Test.testName']
    unittest.main()
