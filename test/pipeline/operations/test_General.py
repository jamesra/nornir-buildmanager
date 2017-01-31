'''
Created on Aug 27, 2013

@author: u0490822
'''
import unittest

from test.pipeline.setup_pipeline import *

import nornir_buildmanager.operations.general as General


class TestGeneralOps(PrepareAndMosaicSetup):

    @property
    def VolumePath(self):
        return "6750"

    @property
    def Platform(self):
        return "PMG"

    def testRename(self):
        self.UntouchedFilterNode = self.VolumeObj.find("Block/Section[@Number='2']/Channel/Filter[@Name='Leveled']")
        self.assertIsNotNone(self.UntouchedFilterNode, "Test is missing filter for test, check setup")

        self.FilterNode = self.VolumeObj.find("Block/Section[@Number='2']/Channel/Filter[@Name='Raw8']")
        self.assertIsNotNone(self.FilterNode, "Test is missing filter for test, check setup")

        OutputFilterNode = General.Rename(OldNode=self.FilterNode, NewName="Renamed")
        self.assertIsNotNone(OutputFilterNode)

        self.OldFilterNode = self.VolumeObj.find("Block/Section[@Number='2']/Channel/Filter[@Name='Raw8']")
        self.assertIsNone(self.OldFilterNode)

        self.NewFilterNode = self.VolumeObj.find("Block/Section[@Number='2']/Channel/Filter[@Name='Renamed']")
        self.assertIsNone(self.OldFilterNode)

        self.UntouchedFilterNode = self.VolumeObj.find("Block/Section[@Number='2']/Channel/Filter[@Name='Leveled']")
        self.assertIsNotNone(self.UntouchedFilterNode)

        # ##Try renaming the path

        originalPath = self.NewFilterNode.FullPath
        self.assertTrue(os.path.exists(originalPath))

        General.MovePath(self.NewFilterNode, NewPath="Renamed", Logger=self.Logger)

        self.assertFalse(os.path.exists(originalPath))
        self.assertTrue(os.path.exists(self.NewFilterNode.FullPath))
