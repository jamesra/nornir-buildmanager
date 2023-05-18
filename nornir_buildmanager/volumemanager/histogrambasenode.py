from __future__ import annotations

import os

from nornir_buildmanager.volumemanager import DataNode, ImageNode, InputTransformHandler, XElementWrapper


class HistogramBase(InputTransformHandler, XElementWrapper):

    @property
    def DataNode(self) -> DataNode:
        return self.find('Data')  # data: DataNode

    @property
    def ImageNode(self) -> ImageNode:
        return self.find('Image')  # data: ImageNode

    @property
    def DataFullPath(self) -> str:
        if self.DataNode is None:
            return ""

        return self.DataNode.FullPath

    @property
    def ImageFullPath(self) -> str:
        if self.ImageNode is None:
            return ""

        return self.ImageNode.FullPath

    @property
    def Checksum(self) -> str:
        if self.DataNode is None:
            return ""
        else:
            return self.DataNode.Checksum

    @property
    def NeedsValidation(self) -> bool:

        if self.InputTransformNeedsValidation():
            return True

        if self.DataNode is None:
            return True

        return self.DataNode.NeedsValidation

    def IsValid(self) -> (bool, str):
        """Remove this node if our output does not exist"""
        if self.DataNode is None:
            return False, "No data node found"
        else:
            if not os.path.exists(self.DataNode.FullPath):
                return False, "No file to match data node"

        '''Check for the transform node and ensure the checksums match'''
        # TransformNode = self.Parent.find('Transform')

        return super(HistogramBase, self).IsValid()
