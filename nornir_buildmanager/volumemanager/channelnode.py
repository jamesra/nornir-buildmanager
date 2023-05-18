from __future__ import annotations

import nornir_buildmanager
from nornir_buildmanager.volumemanager import FilterNode, Scale, ScaleAxis, ScaleNode, TransformNode, XElementWrapper, \
    XNamedContainerElementWrapped


class ChannelNode(XNamedContainerElementWrapped):

    def __init__(self, **kwargs):
        super(ChannelNode, self).__init__(**kwargs)
        self._scale = None

    @property
    def Filters(self) -> [FilterNode]:
        return self.findall('Filter')

    def GetFilter(self, Filter: str) -> FilterNode | None:
        return self.GetChildByAttrib('Filter', 'Name', Filter)

    def HasFilter(self, FilterName: str) -> bool:
        return not self.GetFilter(FilterName) is None

    def GetOrCreateFilter(self, Name: str) -> (bool, FilterNode):
        (added, filterNode) = self.UpdateOrAddChildByAttrib(FilterNode.Create(Name), 'Name')
        return added, filterNode

    def MatchFilterPattern(self, filterPattern: str) -> [FilterNode]:
        return nornir_buildmanager.volumemanager.SearchCollection(self.Filters,
                                                                  'Name',
                                                                  filterPattern)

    def GetTransform(self, transform_name) -> TransformNode | None:
        return self.GetChildByAttrib('Transform', 'Name', transform_name)

    def RemoveFilterOnContrastMismatch(self, FilterName, MinIntensityCutoff, MaxIntensityCutoff, Gamma) -> bool:
        """
        :return: true if filter found and removed
        """

        filter_node = self.GetFilter(Filter=FilterName)
        if filter_node is None:
            return False

        if filter_node.Locked:
            if filter_node.IsContrastMismatched(MinIntensityCutoff, MaxIntensityCutoff, Gamma):
                self.logger.warning("Locked filter cannot be removed for contrast mismatch. %s " % filter_node.FullPath)
                return False

        if filter_node.RemoveChildrenOnContrastMismatch(MinIntensityCutoff, MaxIntensityCutoff, Gamma):
            filter_node.Clean("Contrast mismatch")
            return True

        return False

    def RemoveFilterOnBppMismatch(self, FilterName, expected_bpp) -> bool:
        """
        "return: true if filter found and removed
        """

        filter_node = self.GetFilter(Filter=FilterName)
        if filter_node is None:
            return False

        if filter_node.BitsPerPixel != expected_bpp:
            if filter_node.Locked:
                self.logger.warning(
                    "Locked filter cannot be removed for bits-per-pixel mismatch. %s " % filter_node.FullPath)
                return False
            else:
                filter_node.Clean(
                    "Filter's {0} bpp did not match expected {1} bits-per-pixel".format(filter_node.BitsPerPixel,
                                                                                        expected_bpp))
                return True

        return False

    @property
    def Scale(self) -> Scale:
        if hasattr(self, '_scale') is False:
            scaleNode = self.find('Scale')
            self._scale = Scale.Create(scaleNode) if scaleNode is not None else None

        return self._scale

    def GetScale(self) -> Scale:
        return self.Scale

    def SetScale(self, scaleValueInNm: float):
        """Create a scale node for the channel
        :return: ScaleNode object that was created"""
        # TODO: Scale should be its own object and a property

        [added, scaleNode] = self.UpdateOrAddChild(ScaleNode.Create())

        if isinstance(scaleValueInNm, float):
            scaleNode.UpdateOrAddChild(XElementWrapper('X', {'UnitsOfMeasure': 'nm',
                                                             'UnitsPerPixel': str(scaleValueInNm)}))
            scaleNode.UpdateOrAddChild(XElementWrapper('Y', {'UnitsOfMeasure': 'nm',
                                                             'UnitsPerPixel': str(scaleValueInNm)}))
        elif isinstance(scaleValueInNm, int):
            scaleNode.UpdateOrAddChild(XElementWrapper('X', {'UnitsOfMeasure': 'nm',
                                                             'UnitsPerPixel': str(scaleValueInNm)}))
            scaleNode.UpdateOrAddChild(XElementWrapper('Y', {'UnitsOfMeasure': 'nm',
                                                             'UnitsPerPixel': str(scaleValueInNm)}))
        elif isinstance(scaleValueInNm, ScaleAxis):
            scaleNode.UpdateOrAddChild(XElementWrapper('X', {'UnitsOfMeasure': str(scaleValueInNm.UnitsOfMeasure),
                                                             'UnitsPerPixel': str(scaleValueInNm.UnitsPerPixel)}))
            scaleNode.UpdateOrAddChild(XElementWrapper('Y', {'UnitsOfMeasure': str(scaleValueInNm.UnitsOfMeasure),
                                                             'UnitsPerPixel': str(scaleValueInNm.UnitsPerPixel)}))
        elif isinstance(scaleValueInNm, Scale):
            (added, scaleNode) = self.UpdateOrAddChild(ScaleNode.CreateFromScale(scaleValueInNm))
        else:
            raise NotImplementedError("Unknown type %s" % scaleValueInNm)

        self._scale = Scale.Create(scaleNode)

        return added, scaleNode

    @property
    def NeedsValidation(self):
        return True

    def __str__(self):
        return "Channel: %s Section: %d" % (self.Name, self.Parent.Number)

    @classmethod
    def Create(cls, Name, Path=None, **extra) -> ChannelNode:
        return super(ChannelNode, cls).Create(tag='Channel', Name=Name, Path=Path, **extra)
