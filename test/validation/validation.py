'''
Created on May 19, 2015

@author: u0490822
'''
import tempfile
import unittest

import nornir_buildmanager


class TestValidation(unittest.TestCase):


    def setUp(self):
        pass


    def tearDown(self):
        pass


    def testValidation(self):
        
        hint_node = nornir_buildmanager.VolumeManager.AutoLevelHintNode()
        hint_node.UserRequestedGamma = 1.001
        hint_node.UserRequestedMaxIntensityCutoff = 255.5
        hint_node.UserRequestedMinIntensityCutoff = 32.2
        
        # Make sure our IsValueMatched function worked and handles precision correctly
        self.assertTrue(nornir_buildmanager.validation.transforms.IsValueMatched(OutputNode=hint_node, OutputAttribute="UserRequestedGamma", TargetValue=1, Precision=0))
        self.assertTrue(nornir_buildmanager.validation.transforms.IsValueMatched(OutputNode=hint_node, OutputAttribute="UserRequestedGamma", TargetValue=1, Precision=1))
        self.assertTrue(nornir_buildmanager.validation.transforms.IsValueMatched(OutputNode=hint_node, OutputAttribute="UserRequestedGamma", TargetValue=1, Precision=2))
        self.assertFalse(nornir_buildmanager.validation.transforms.IsValueMatched(OutputNode=hint_node, OutputAttribute="UserRequestedGamma", TargetValue=1, Precision=3))
       
    
        self.assertTrue(nornir_buildmanager.validation.transforms.IsValueMatched(OutputNode=hint_node, OutputAttribute="UserRequestedMaxIntensityCutoff", TargetValue=256, Precision=0))
        self.assertTrue(nornir_buildmanager.validation.transforms.IsValueMatched(OutputNode=hint_node, OutputAttribute="UserRequestedMaxIntensityCutoff", TargetValue=255.5, Precision=0))
        self.assertTrue(nornir_buildmanager.validation.transforms.IsValueMatched(OutputNode=hint_node, OutputAttribute="UserRequestedMaxIntensityCutoff", TargetValue=255.6, Precision=0))
        
        self.assertFalse(nornir_buildmanager.validation.transforms.IsValueMatched(OutputNode=hint_node, OutputAttribute="UserRequestedMaxIntensityCutoff", TargetValue=255, Precision=0))
        self.assertFalse(nornir_buildmanager.validation.transforms.IsValueMatched(OutputNode=hint_node, OutputAttribute="UserRequestedMaxIntensityCutoff", TargetValue=257, Precision=0))
        
        self.assertTrue(nornir_buildmanager.validation.transforms.IsValueMatched(OutputNode=hint_node, OutputAttribute="UserRequestedMaxIntensityCutoff", TargetValue=255.5, Precision=1))
        self.assertFalse(nornir_buildmanager.validation.transforms.IsValueMatched(OutputNode=hint_node, OutputAttribute="UserRequestedMaxIntensityCutoff", TargetValue=255.56, Precision=1))
        self.assertTrue(nornir_buildmanager.validation.transforms.IsValueMatched(OutputNode=hint_node, OutputAttribute="UserRequestedMaxIntensityCutoff", TargetValue=255.54, Precision=1))
        
    def testInputChecksumValidation(self):
        
        valid_checksum = "0123456789"
        invalid_checksum = "012345678"
          
        input_transform = nornir_buildmanager.VolumeManager.TransformNode(Name="Input", Type="Test_Input")
        output_transform = nornir_buildmanager.VolumeManager.TransformNode(Name="Output", Type="Test_Output")
        
        wrong_checksum_input = nornir_buildmanager.VolumeManager.TransformNode(Name="Input", Type="Test_Input")
        wrong_name_input = nornir_buildmanager.VolumeManager.TransformNode(Name="Wrong Name Input", Type="Test_Input")
        wrong_type_input = nornir_buildmanager.VolumeManager.TransformNode(Name="Wrong Name Input", Type="Wrong Type Input")
        wrong_cropbox_input = nornir_buildmanager.VolumeManager.TransformNode(Name="Wrong Name Input", Type="Wrong cropbox Input")  
        
        input_transform.attrib['Checksum'] = valid_checksum
        wrong_name_input.attrib['Checksum'] = valid_checksum
        wrong_type_input.attrib['Checksum'] = valid_checksum
        wrong_cropbox_input.attrib['Checksum'] = valid_checksum
        
        wrong_checksum_input.attrib['Checksum'] = invalid_checksum
        wrong_cropbox_input.CropBox = (0, 0, 64, 64)
        
        output_transform.SetTransform(input_transform)
        
        self.assertTrue(output_transform.IsInputTransformMatched(input_transform), "Valid input transform should pass")
        self.assertFalse(output_transform.IsInputTransformMatched(wrong_checksum_input), "Incorrect input checksum should fail")
        self.assertFalse(output_transform.IsInputTransformMatched(wrong_name_input), "Incorrect input name should fail")
        self.assertFalse(output_transform.IsInputTransformMatched(wrong_type_input), "Incorrect input type should fail")
        self.assertFalse(output_transform.IsInputTransformMatched(wrong_cropbox_input), "Incorrect cropbox should fail")
        
if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
