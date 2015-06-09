'''
Created on Feb 18, 2013

@author: u0490822
'''
import glob
import logging
import os
import shutil
import unittest

from nornir_buildmanager.VolumeManagerETree import *
import nornir_buildmanager.importers.pmg as pmg
import nornir_shared.files
import nornir_shared.misc
import test.testbase

import nornir_buildmanager.build

class VolumeManagerTestBase(test.testbase.TestBase):

    def setUp(self):
        super(VolumeManagerTestBase, self).setUp()

        self.VolumeFullPath = self.TestOutputPath

        if os.path.exists(self.VolumeFullPath):
            shutil.rmtree(self.VolumeFullPath)

        self.VolumeObj = VolumeManager.Load(self.VolumeFullPath, Create=True)
        
    def tearDown(self):
        if os.path.exists(self.VolumeFullPath):
            shutil.rmtree(self.VolumeFullPath)
            
class VolumeManagerBlockTest(VolumeManagerTestBase):

    def testBlock(self):
        
        block = BlockNode("TEM")
        [added_block, block] = self.VolumeObj.UpdateOrAddChild(block)
        self.assertTrue(added_block, "New block should return true")
        
        test_data = [1,2,3,4,5]
        even_data = [2,4]
        odd_data = [1,3,5]
        self.assertListEqual(block.NonStosSectionNumbers, [], "Non stos section numbers should initialize to empty list")
        block.MarkSectionsAsDamaged(test_data)
        self.assertListEqual(block.NonStosSectionNumbers, test_data, "Damaged sections should be reflected in the block node")
        
        block.MarkSectionsAsUndamaged(even_data)
        self.assertListEqual(block.NonStosSectionNumbers, odd_data, "Odd sections should remain after removing even numbers")
        
        block.Parent.Save()
        
        self.VolumeObj = VolumeManager.Load(self.VolumeFullPath, Create=True)
        (added_block, loaded_block) = self.VolumeObj.UpdateOrAddChild(block)
        self.assertFalse(added_block, "Block should exist after save+load")
        
        self.assertListEqual(loaded_block.NonStosSectionNumbers, odd_data, "Odd sections should remain after removing even numbers and save+load")
            
        nornir_buildmanager.build.Execute(buildArgs=[self.TestOutputPath, 'ListDamagedSections'])
        
        nornir_buildmanager.build.Execute(buildArgs=[self.TestOutputPath, 'MarkSectionsDamaged', '-Sections', ','.join(list(map(str,even_data)))])
        self.VolumeObj = VolumeManager.Load(self.VolumeFullPath, Create=True)
        (added_block, loaded_block) = self.VolumeObj.UpdateOrAddChild(block)
        self.assertFalse(added_block, "Block should exist after running pipeline")
        self.assertListEqual(loaded_block.NonStosSectionNumbers, test_data, "All sections should be marked after adding even numbers")
        
        nornir_buildmanager.build.Execute(buildArgs=[self.TestOutputPath, 'MarkSectionsUndamaged', '-Sections',','.join(list(map(str,odd_data)))])
        self.VolumeObj = VolumeManager.Load(self.VolumeFullPath, Create=True)
        (added_block, loaded_block) = self.VolumeObj.UpdateOrAddChild(block)
        self.assertFalse(added_block, "Block should exist after running pipeline")
        self.assertListEqual(loaded_block.NonStosSectionNumbers, even_data, "Even sections should be marked after removing odd numbers")
        
class VolumeManagerFilterTest(VolumeManagerTestBase):

    def testBlock(self):
        
        block = BlockNode("TEM")
        [added_block, block] = self.VolumeObj.UpdateOrAddChild(block)
        self.assertTrue(added_block, "New block should return true")
        
        section = SectionNode(543)
        [added_section, section] = block.UpdateOrAddChild(section)
        self.assertTrue(added_section, "New section should return true")
        
        channel = ChannelNode("YY")
        [added_channel, channel] = section.UpdateOrAddChild(channel)
        self.assertTrue(added_channel, "New channel should return true")
        
        filter = FilterNode("Leveled")
        [added_filter, filter] = channel.UpdateOrAddChild(filter)
        self.assertTrue(added_filter, "New channel should return true")
        
        filter.MinIntensityCutoff = 4
        filter.MaxIntensityCutoff = 250
        filter.Gamma = 1.4
        
        self.VolumeObj.Save()
        
        #See if we can print the filter contrast settings
        nornir_buildmanager.build.Execute(buildArgs=[self.TestOutputPath, 'ListFilterContrast'])
        

class VolumeManagerAppendTest(VolumeManagerTestBase):

    def runTest(self):

        logger = logging.getLogger(__name__ + "VolumeManagerAppendTest")
        self.assertEqual(self.VolumeObj.tag, "Volume")

        # Try adding a block, first as a
        BlockObj = BlockNode(Name='Block', Path='Block')
        [added, BlockObj] = self.VolumeObj.UpdateOrAddChild(BlockObj)

        self.assertTrue(added)
        self.assertTrue(BlockObj in self.VolumeObj)

        imageObj = ImageNode("Some.png")
        [added, imageObj] = BlockObj.UpdateOrAddChild(imageObj)
        self.assertTrue(imageObj in BlockObj)
    #
        dataObj = DataNode("Some.xml")
        [added, dataObj] = BlockObj.UpdateOrAddChild(dataObj)
        self.assertTrue(dataObj in BlockObj)

        tnode = TransformNode(Name="Stage", Path="SomePath.mosaic", Checksum="ABCDE", Type='Test')
        BlockObj.UpdateOrAddChild(tnode)
        self.assertTrue(tnode in BlockObj)

        HistogramElement = HistogramNode(tnode, Type='Test', attrib=None)
        [HistogramElementCreated, HistogramElement] = BlockObj.UpdateOrAddChildByAttrib(HistogramElement, "Type")
        self.assertTrue(HistogramElementCreated)
        
        [HistogramElementCreatedAgain, HistogramElement] = BlockObj.UpdateOrAddChildByAttrib(HistogramElement, "Type")
        self.assertFalse(HistogramElementCreatedAgain)

        c = HistogramElement.__class__
        self.assertTrue(HistogramElement in BlockObj)

    #    ImageNode = VolumeManagerETree.XElementWrapper('Image', {'path' : OutputHistogramPngFilename})
    #    [added, ImageNode] = HistogramElement.UpdateOrAddChild(ImageNode)
    #
    #    DataNode = VolumeManagerETree.XElementWrapper('Data', {'Path' : OutputHistogramXmlFilename})
    #    [added, DataNode] = HistogramElement.UpdateOrAddChild(DataNode)
        imageObj = ImageNode("Histogram.png")
        [added, imageObj] = HistogramElement.UpdateOrAddChild(imageObj)
        self.assertTrue(imageObj in HistogramElement)
    #
        dataObj = DataNode("Histogram.xml")
        [added, dataObj] = HistogramElement.UpdateOrAddChild(dataObj)
        self.assertTrue(dataObj in HistogramElement)

        p = dataObj.FullPath
        s = str(dataObj)
        t = str(HistogramElement)

        for k in HistogramElement.attrib.keys():
            v = HistogramElement.attrib[k]
            print str(v)

        d = HistogramElement.DataNode

        self.assertTrue(dataObj in HistogramElement)

        HistogramElement.Clean()

        self.VolumeObj.Save()


if __name__ == "__main__":
    # import syssys.argv = ['', 'Test.testName']
    unittest.main()