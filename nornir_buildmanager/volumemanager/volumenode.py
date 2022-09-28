from __future__ import annotations
from typing import Generator
from nornir_buildmanager.volumemanager import XNamedContainerElementWrapped, BlockNode


class VolumeNode(XNamedContainerElementWrapped):
    """The root of a volume's XML Meta-data"""

    @property
    def Blocks(self) -> Generator[BlockNode]:
        return self.findall('Block')

    def GetBlock(self, name) -> BlockNode:
        return self.GetChildByAttrib('Block', 'Name', name)

    @property
    def NeedsValidation(self) -> bool:
        return True

    @classmethod
    def Create(cls, Name: str, Path: str = None, **extra) -> VolumeNode:
        return super(VolumeNode, cls).Create(tag='Volume', Name=Name, Path=Path, **extra)
