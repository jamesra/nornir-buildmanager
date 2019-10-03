'''
Created on May 17, 2018

@author: u0490822
'''


from nornir_buildmanager.operations.block import *
from nornir_imageregistration.transforms import registrationtree
from nornir_buildmanager import VolumeManagerETree, VolumeManagerHelpers

from test.pipeline.setup_pipeline import VerifyVolume, VolumeEntry, \
    CopySetupTestBase, EmptyVolumeTestBase


import test.pipeline.test_sectionimage as test_sectionimage

def _RTNodesToNumberList(Nodes):
    nums = []
    for n in Nodes:
        nums.append(n.SectionNumber)

    return nums


def ValidateStosMap(test, StosMapNode, expectedRT, expectedCenter):

    rt = RegistrationTreeFromStosMapNode(StosMapNode)

    test.assertEqual(len(rt.Nodes), len(expectedRT.Nodes))

    test.assertEqual(StosMapNode.CenterSection, expectedCenter)

    for expectedNode in list(expectedRT.Nodes.values()):
        actualNode = rt.Nodes[expectedNode.SectionNumber]
        actualNodeNumbers = _RTNodesToNumberList(actualNode.Children)
        expectedNodeNumbers = _RTNodesToNumberList(expectedNode.Children)
        test.assertEqual(actualNodeNumbers, expectedNodeNumbers)


class SectionToSectionMappingTest(test_sectionimage.ImportLMImages):

    @property
    def VolumePath(self):
        return "SectionToSectionMappingTest"

    def _GetResetBlockNode(self):
        VolumeObj = self.LoadOrCreateVolume()
        BlockNode = VolumeObj.find("Block")
        self.assertIsNotNone(BlockNode)

        return BlockNode

    def SetNonStosSectionList(self, BlockNode, NonStosNumberList, **kwargs):

        StosExemptNode = VolumeManagerETree.XElementWrapper(tag='NonStosSectionNumbers')
        (added, StosExemptNode) = BlockNode.UpdateOrAddChild(StosExemptNode)

        # Fetch the list of the exempt nodes from the element text
        if len(NonStosNumberList) > 0:
            StosExemptNode.text = ','.join(str(x) for x in NonStosNumberList)
        else:
            StosExemptNode.text = ""

    def _GenerateExpectedRT(self, GoodSections, BadSections, center, adjacentThreshold):
        # Verify that the sections are mapped correctly
        expectedRT = registrationtree.RegistrationTree.CreateRegistrationTree(GoodSections, adjacentThreshold=adjacentThreshold, center=center)
        expectedRT.AddNonControlSections(BadSections)
        return expectedRT


    def testCreateSectionToSectionMapping(self):

        center = 5
        self.GenerateStosMap(center, adjacentThreshold=1, Logger=self.Logger)
        self.GenerateStosMap(center, adjacentThreshold=2, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=2, Logger=self.Logger)

        self.CheckRemoveDuplicateMappings(center, adjacentThreshold=2, Logger=self.Logger)

        self.StosMapGeneratorAddSectionLaterCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.StosMapGeneratorAddSectionLaterCheck(center, adjacentThreshold=2, Logger=self.Logger)

        center = 1
        self.GenerateStosMap(center, adjacentThreshold=1, Logger=self.Logger)
        self.GenerateStosMap(center, adjacentThreshold=2, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=2, Logger=self.Logger)

        center = 12
        self.GenerateStosMap(center, adjacentThreshold=1, Logger=self.Logger)
        self.GenerateStosMap(center, adjacentThreshold=2, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=2, Logger=self.Logger)

        center = 0
        self.GenerateStosMap(center, adjacentThreshold=1, Logger=self.Logger)
        self.GenerateStosMap(center, adjacentThreshold=2, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=2, Logger=self.Logger)

        center = 13
        self.GenerateStosMap(center, adjacentThreshold=1, Logger=self.Logger)
        self.GenerateStosMap(center, adjacentThreshold=2, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=2, Logger=self.Logger)

    def testChangeCenterForStosMap(self):
        '''Creates a stosmap, then updates the center and ensures the new stosmap reflects the change'''
        #Create a stos-map, then 
        FirstCenter = 4
        BlockNode = self.GenerateStosMap(FirstCenter, 1, self.Logger, None)

        SecondCenter = 6
        BlockNode = self.GenerateStosMap(SecondCenter, 1, self.Logger, BlockNode)

        volumechecklist = [VolumeEntry("StosMap", "Name", "PotentialRegistrationChain")]
        StosMapNode = VerifyVolume(self, BlockNode, volumechecklist)

        self.CheckMappings(StosMapNode, 6, [5,7])
        self.CheckMappings(StosMapNode, 5, [4])
        self.CheckMappings(StosMapNode, 4, [3])


    
    def CheckMappings(self, StosMapNode, controlNumber, expectedMappings):
        controlMappings = StosMapNode.GetMappingsForControl(controlNumber)
        self.assertTrue(len(controlMappings) == 1, "Unexpected number of mappings for control section %d" % controlNumber)
        mapped = frozenset(controlMappings[0].Mapped)
        self.assertTrue(len(mapped) == len(expectedMappings))

        for expected in expectedMappings:
            self.assertTrue(expected in mapped, "Expected section %d to me mapped to %d" % (expected, controlNumber))

        unexpected = mapped.difference(expectedMappings)
        self.assertTrue(len(unexpected) == 0, "Unexpected mappings found to %d" % (controlNumber))
        

    def GenerateStosMap(self, center, adjacentThreshold, Logger, BlockNode=None):

        BlockShouldBeCreated = BlockNode is None

        if BlockNode is None:
            BlockNode = self._GetResetBlockNode()

        GoodSections = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        OutputBlockNode = CreateOrUpdateSectionToSectionMapping(Parameters={'NumAdjacentSections' : adjacentThreshold, 'CenterSection' : center}, BlockNode=BlockNode, ChannelsRegEx="*", FiltersRegEx="*", Logger=self.Logger)

        if BlockShouldBeCreated:
            self.assertIsNotNone(OutputBlockNode)

        # VolumeManagerETree.VolumeManager.Save(self.TestOutputPath, VolumeObj)

        volumechecklist = [VolumeEntry("StosMap", "Name", "PotentialRegistrationChain")]

        StosMapNode = VerifyVolume(self, BlockNode, volumechecklist)

        # Verify that the sections are mapped correctly
        if center not in GoodSections:
            center = registrationtree.NearestSection(GoodSections, center)

        expectedRT = self._GenerateExpectedRT(GoodSections, [], center, adjacentThreshold)
        ValidateStosMap(self, StosMapNode, expectedRT, center)

        return BlockNode


    def StosMapGeneratorWithInvalidCheck(self, center, adjacentThreshold, Logger):
        '''Generate a stos map that contains some bad sections'''

        GoodSections = [2, 3, 4, 6, 7, 8, 9]
        BadSections = [1, 5, 10]
        BlockNode = self._GetResetBlockNode()
        self._StosMapGeneratorWithInvalidCheckWithBlock(BlockNode, GoodSections, BadSections, center, adjacentThreshold, Logger)

    def _StosMapGeneratorWithInvalidCheckWithBlock(self, BlockNode, GoodSections, BadSections, center, adjacentThreshold, Logger, expectedRT=None):

        self.SetNonStosSectionList(BlockNode, BadSections)

        OutputBlockNode = CreateOrUpdateSectionToSectionMapping(Parameters={'NumAdjacentSections' : adjacentThreshold, 'CenterSection' : center}, BlockNode=BlockNode, ChannelsRegEx='*', FiltersRegEx='*', Logger=self.Logger)
        self.assertIsNotNone(OutputBlockNode)

        volumechecklist = [VolumeEntry("StosMap", "Name", "PotentialRegistrationChain")]
        StosMapNode = VerifyVolume(self, BlockNode, volumechecklist)

        if expectedRT is None:
            expectedRT = self._GenerateExpectedRT(GoodSections, BadSections, center, adjacentThreshold)

        if center not in GoodSections:
            center = registrationtree.NearestSection(GoodSections, center)

        ValidateStosMap(self, StosMapNode, expectedRT, center)

        return OutputBlockNode


    def StosMapGeneratorAddSectionLaterCheck(self, center, adjacentThreshold, Logger):

        BlockNode = self._GetResetBlockNode()

        print("Remove section 7")
        omitSectionNode = BlockNode.GetSection(7)
        self.assertIsNotNone(omitSectionNode)
        BlockNode.remove(omitSectionNode)

        GoodSections = [2, 3, 4, 6, 8, 9]
        BadSections = [1, 5, 10]

        self.SetNonStosSectionList(BlockNode, [1, 5, 10])

        OutputBlockNode = self._StosMapGeneratorWithInvalidCheckWithBlock(BlockNode, GoodSections, BadSections, center, adjacentThreshold, Logger)
        self.assertIsNotNone(OutputBlockNode)

        if center not in GoodSections:
            center = registrationtree.NearestSection(GoodSections, center)

        # OK, add the section back and make sure it is included in the updated stos map

        print("Add section 7")
        OutputBlockNode.append(omitSectionNode)
        GoodSections = [2, 3, 4, 6, 7, 8, 9]
        BadSections = [1, 5, 10]

        expectedRT = self._GenerateExpectedRT(GoodSections, BadSections, center, adjacentThreshold)

        # We expect extra mappings for section 8 since section 7 did not exxist in the original
        if(adjacentThreshold == 1):
            expectedRT.AddPair(6, 8)
        elif(adjacentThreshold == 2):
            expectedRT.AddPair(4, 8)
            expectedRT.AddPair(6, 9)
        else:
            self.fail("Test not tweaked for adjacentThreshold > 2")

        OutputBlockNode = self._StosMapGeneratorWithInvalidCheckWithBlock(OutputBlockNode, GoodSections, BadSections, center, adjacentThreshold, Logger, expectedRT)
        self.assertIsNotNone(OutputBlockNode)

        print("Done!")

    def CheckRemoveDuplicateMappings(self, center, adjacentThreshold, Logger):

        GoodSections = [2, 3, 4, 6, 7, 8, 9]
        BadSections = [1, 5, 10]
        BlockNode = self._GetResetBlockNode()
        self._StosMapGeneratorWithInvalidCheckWithBlock(BlockNode, GoodSections, BadSections, center, adjacentThreshold, Logger)

        volumechecklist = [VolumeEntry("StosMap", "Name", "PotentialRegistrationChain")]
        StosMapNode = VerifyVolume(self, BlockNode, volumechecklist)

        # Add some extra stosmap nodes and make sure they get cleaned up

        ExtraMapNode = VolumeManagerETree.MappingNode.Create(4, 10)
        StosMapNode.append(ExtraMapNode)

        removed = StosMapNode.RemoveDuplicateControlEntries(3)
        self.assertFalse(removed, "No duplicate should return false")

        removed = StosMapNode.RemoveDuplicateControlEntries(4)
        self.assertTrue(removed, "Duplicate should be removed and return true")

        listMapFour = StosMapNode.GetMappingsForControl(4)
        self.assertEqual(len(listMapFour), 1, "Duplicate StosMap was not removed")

        expectedRT = self._GenerateExpectedRT(GoodSections, BadSections, center, adjacentThreshold)
        expectedRT.AddPair(4, 10)

        if center not in GoodSections:
            center = registrationtree.NearestSection(GoodSections, center)

        ValidateStosMap(self, StosMapNode, expectedRT, center)

        BannedSections = [4]
        StosMapNode.ClearBannedControlMappings(BannedSections)
        listMapFour = StosMapNode.GetMappingsForControl(4)
        self.assertEqual(len(listMapFour), 0, "Banned section should be removed")
