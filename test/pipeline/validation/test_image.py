import logging
import os
import shutil
import tempfile
import unittest
import glob

from test.pipeline.setup_pipeline import *

import nornir_imageregistration.core as core
from nornir_buildmanager.VolumeManagerETree import *
import nornir_buildmanager.build as build
import nornir_buildmanager.validation.image as image
import nornir_shared.files
import nornir_shared.misc


class ImageValidationTest(CopySetupTestBase):

    @property
    def Platform(self):
        return "PNG"

    @property
    def VolumePath(self):
        return "6872_small"

    @property
    def PNGList(self):
        return glob.glob(os.path.join(self.TestOutputPath, '*.png'))

    def GetArea(self, path):
        '''Returns (Width, Height) of image'''

        ActualArea = core.GetImageSize(path)

        if not path is None:
            self.assertIsNotNone(ActualArea, "Actual area should be not be none if image path is valid")
        else:
            # GetImageSize returns Height,Width, so we need to flip
            self.assertIsNone(ActualArea, "Actual area should be none if image path is none")


        return ActualArea

    def RunRemoveOnDimensionTest(self, path):

        ActualArea = self.GetArea(path)

        Removed = image.RemoveOnDimensionMismatch(path, ActualArea)
        self.assertFalse(Removed, "Image incorrectly removed when dimensions matched")

        # Swap the dimensions and make sure the image is deleted
        IncorrectArea = (ActualArea[1], ActualArea[0])
        Removed = image.RemoveOnDimensionMismatch(path, IncorrectArea)

        self.assertTrue(Removed, "Image incorrectly retained when dimensions swapped")

        return

    def testRemoveOnDimensionTestImageNone(self):

        ActualArea = (1, 1)
        Removed = image.RemoveOnDimensionMismatch(None, ActualArea)
        self.assertTrue(Removed, "None input should indicate image was removed")


    def testRemoveOnDimensionMismatch(self):

        pngs = self.PNGList
        self.assertTrue(len(pngs) > 0, "No input files found")

        for png in pngs:
            self.RunRemoveOnDimensionTest(png)

