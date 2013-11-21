'''
Created on Feb 14, 2013

@author: u0490822
'''
import logging
import os
import shutil
import sys
import tempfile
import unittest

import test.testbase

from nornir_buildmanager.VolumeManagerETree import *
import nornir_buildmanager.build as build
from nornir_buildmanager.validation import transforms
from nornir_imageregistration.files.mosaicfile import *
import nornir_shared.misc


def VerifyVolume(test, VolumeObj, listVolumeEntries):
    '''Walk a list of volume entries and ensure each exists'''

    SearchEntry = VolumeObj
    if isinstance(VolumeObj, str):
        SearchEntry = VolumeManager.Load(VolumeObj)

    if not isinstance(listVolumeEntries, list):
        assert(isinstance(listVolumeEntries, VolumeEntry))
        listVolumeEntries = [listVolumeEntries]

    for entry in listVolumeEntries:
        NextSearchEntry = SearchEntry.find(entry.xpath)
        test.assertIsNotNone(NextSearchEntry, "Missing output in volume " + entry.xpath)
        SearchEntry = NextSearchEntry

    return SearchEntry


class VolumeEntry(object):

    xpathtemplate = "%(xpath)s[@%(AttribName)s='%(AttribValue)s']"

    def __init__(self, elementName, attributeName=None, attributeValue=None):
        self.XPath = elementName
        self.AttributeName = attributeName
        self.AttributeValue = attributeValue

    @property
    def xpath(self):
        x = self.XPath
        if not self.AttributeName is None:
            x = VolumeEntry.xpathtemplate % {'xpath' : self.XPath, 'AttribName' : self.AttributeName, 'AttribValue' : self.AttributeValue}

        return x

class PlatformTest(test.testbase.TestBase):

    @property
    def Platform(self):
        raise Exception("Platform property not implemented")

    def RunBuild(self, buildArgs):
        # Run a build, ensure the output directory exists, and return the volume obj
        build.Execute(buildArgs)
        self.assertTrue(os.path.exists(self.TestOutputPath), "Test input was not copied")

        VolumeObj = VolumeManager.Load(self.TestOutputPath)
        self.assertIsNotNone(VolumeObj)
        self.assertTrue(os.path.exists(VolumeObj.FullPath))

        return VolumeObj

    def setUp(self):
        '''Imports a volume and stops, tests call pipeline functions'''
        super(PlatformTest, self).setUp()

        self.PlatformFullPath = os.path.join(self.TestDataPath, "PlatformRaw", self.Platform)
        self.assertTrue(os.path.exists(self.PlatformFullPath), "Test data for platform does not exist:" + self.PlatformFullPath)


class CopySetupTestBase(PlatformTest):
    '''Copies data from Platform data directory to test output directory at setup'''

    def setUp(self):
        super(CopySetupTestBase, self).setUp()

        self.assertTrue(os.path.exists(self.PlatformFullPath))

        if os.path.exists(self.TestOutputPath):
            shutil.rmtree(self.TestOutputPath)

        shutil.copytree(self.PlatformFullPath, self.TestOutputPath)


class PipelineTest(PlatformTest):

    @property
    def VolumeDir(self):
        return self.TestOutputPath

    @property
    def VolumePath(self):
        return "6750"

    @property
    def Platform(self):
        return "PMG"



    def setUp(self):
        '''Imports a volume and stops, tests call pipeline functions'''
        super(PipelineTest, self).setUp()

        self.TestDataSource = os.path.join(self.PlatformFullPath, self.VolumePath)
        self.assertTrue(os.path.exists(self.TestDataSource), "Test input does not exist:" + self.TestDataSource);

    def tearDown(self):
        # if os.path.exists(self.VolumeDir):
        #    shutil.rmtree(self.VolumeDir);
        pass


    def ValidateTransformChecksum(self, Node):
        '''Ensure that the reported checksum and actual file checksum match'''
        self.assertTrue(hasattr(Node, 'Checksum'));
        self.assertTrue(os.path.exists(Node.FullPath));
        FileChecksum = MosaicFile.LoadChecksum(Node.FullPath);
        self.assertEqual(Node.Checksum, FileChecksum);

    def CheckTransformInputs(self, transformNode):
        '''Walk every transform node, verify that if the inputtransform data matches the recorded data'''

        # If the object does not claim to have an input checksum then the test is not valid
        if not 'InputTransformChecksum' in transformNode.attrib:
            return;

        # The transform should report the name of the input transform if it has a checksum
        self.assertTrue('InputTransform' in transformNode.attrib);

        self.ValidateTransformChecksum(transformNode);
        InputTransform = transformNode.Parent.GetChildByAttrib('Transform', 'Name', transformNode.InputTransform);
        self.assertIsNotNone(InputTransform);

        self.assertFalse(transforms.IsOutdated(transformNode, InputTransform))

        # Check that our reported checksum and actual file checksums match
        self.ValidateTransformChecksum(InputTransform);

        # Check that
        self.assertFalse(transforms.IsOutdated(self.PruneTransform, self.StageTransform));


    def ValidateAllTransforms(self, ParentNode):
        '''Check every transform in the parent node to ensure that if it refers to an input transform the values match'''

        TransformNodes = list(ParentNode.findall('Transform'));
        for tNode in TransformNodes:
            self.CheckTransformInputs(tNode);





class ImportOnlySetup(PipelineTest):
    '''Calls prepare on a PMG volume.  Used as a base class for more complex tests'''
    def setUp(self):
        super(ImportOnlySetup, self).setUp();

        # Import the files
        buildArgs = ['Build.py', '-input', self.TestDataSource, '-volume', self.VolumeDir, '-debug'];
        build.Execute(buildArgs);

        self.assertTrue(os.path.exists(self.VolumeDir), "Test input was not copied");

        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.VolumeDir)
        self.assertIsNotNone(self.VolumeObj);

class PrepareSetup(PipelineTest):
    '''Calls prepare on a PMG volume.  Used as a base class for more complex tests'''
    def setUp(self):
        super(PrepareSetup, self).setUp();

        # Import the files
        buildArgs = ['Build.py', '-input', self.TestDataSource, '-volume', self.VolumeDir, '-pipeline', 'TEMPrepare', '-debug'];
        build.Execute(buildArgs);

        self.assertTrue(os.path.exists(self.VolumeDir), "Test input was not copied");

        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.VolumeDir)
        self.assertIsNotNone(self.VolumeObj);

class PrepareAndMosaicSetup(PipelineTest):
    '''Calls prepare and mosaic pipelines on a PMG volume.  Used as a base class for more complex tests'''

    def setUp(self):

        super(PrepareAndMosaicSetup, self).setUp();
        # Import the files
        buildArgs = ['Build.py', '-input', self.TestDataSource, '-volume', self.VolumeDir, '-pipeline', 'TEMPrepare', 'TEMMosaic', '-debug'];
        build.Execute(buildArgs);

        self.assertTrue(os.path.exists(self.VolumeDir), "Test input was not copied");

        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.VolumeDir)
        self.assertIsNotNone(self.VolumeObj);

if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()