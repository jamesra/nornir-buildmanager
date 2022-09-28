from __future__ import annotations

from nornir_buildmanager.volumemanager import HistogramBase, AutoLevelHintNode


class HistogramNode(HistogramBase):

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Histogram'

        super(HistogramNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, InputTransformNode, Type, attrib=None, **extra) -> HistogramNode:
        obj = HistogramNode(attrib=attrib, **extra)
        obj.SetTransform(InputTransformNode)
        obj.attrib['Type'] = Type
        return obj

    def GetAutoLevelHint(self):
        return self.find('AutoLevelHint')

    def GetOrCreateAutoLevelHint(self):
        existing_hint = self.GetAutoLevelHint()
        if existing_hint is not None:
            return existing_hint
        else:
            # Create a new AutoLevelData node using the calculated values as overrides so users can find and edit it later
            self.UpdateOrAddChild(AutoLevelHintNode.Create())
            return self.GetAutoLevelHint()
