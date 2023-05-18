from __future__ import annotations

import os

from nornir_buildmanager.volumemanager import InputTransformHandler, XFileElementWrapper


class TransformDataNode(InputTransformHandler, XFileElementWrapper):
    """
    Represents visualization data associated with a specific transform
    """

    @classmethod
    def Create(cls, Path: str, attrib: dict = None, **extra) -> TransformDataNode:
        return cls(tag='TransformData', Path=Path, attrib=attrib, **extra)

    def IsValid(self) -> (bool, str):
        if not os.path.exists(self.FullPath):
            return [False, 'File does not exist']

        (valid, reason) = self.InputTransformIsValid()
        if not valid:
            return valid, reason

        return super(TransformDataNode, self).IsValid()
