from __future__ import annotations

from nornir_buildmanager.volumemanager import HistogramBase, AutoLevelHintNode, ImageNode, DataNode


class HistogramNode(HistogramBase):

    # @property
    # def Name(self) -> str:
    #     return self.get('Name', '')
    #
    # @Name.setter
    # def Name(self, value: str | None):
    #
    #     if value is None:
    #         if 'Name' in self.attrib:
    #             del self.attrib['Name']
    #             return
    #     else:
    #         self.attrib['Name'] = value

    def __init__(self, name=None, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Histogram'

        super(HistogramNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, InputTransformNode=None, Type=None, attrib=None, **extra) -> HistogramNode:
        obj = HistogramNode(attrib=attrib, **extra)
        if InputTransformNode is not None:
            obj.SetTransform(InputTransformNode)
        if Type is not None:
            obj.attrib['Type'] = Type
        return obj

    @property
    def Image(self) -> ImageNode | None:
        return self.find('Image')

    @property
    def Data(self) -> DataNode | None:
        return self.find('Data')

    def GetAutoLevelHint(self) -> AutoLevelHintNode | None:
        return self.find('AutoLevelHint')

    def GetOrCreateAutoLevelHint(self) -> AutoLevelHintNode:
        existing_hint = self.GetAutoLevelHint()
        if existing_hint is not None:
            return existing_hint
        else:
            # Create a new AutoLevelData node using the calculated values as overrides so users can find and edit it later
            self.UpdateOrAddChild(AutoLevelHintNode.Create())
            return self.GetAutoLevelHint()
