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
    def Scale(self) -> Scale | None:
        if hasattr(self, '_scale') is False:
            scaleNode = self.find('Scale')
            self._scale = Scale.Create(scaleNode) if scaleNode is not None else None

        return self._scale

    def GetScale(self) -> Scale | None:
        return self.Scale

    def _try_remove_scale_node(self):
        existing_scale_node = self.find('Scale')
        if existing_scale_node is not None:
            self.remove(existing_scale_node)

    def SetScale(self, scale_value_in_nm: float | int | ScaleAxis | Scale) -> tuple[bool, ScaleNode]:
        """Create a scale node for the channel
        If a float or integer is passed, the value should be in nanometer units.
        :return: ScaleNode object that was created"""
        # TODO: Scale should be its own object and a property

        self._try_remove_scale_node()

        if isinstance(scale_value_in_nm, Scale):
            (added, scaleNode) = self.UpdateOrAddChild(ScaleNode.CreateFromScale(scale_value_in_nm),
                                                       f"ScaleNode[X='{scale_value_in_nm.X.UnitsPerPixel}'][Y='{scale_value_in_nm.Y.UnitsPerPixel}'][Z='{scale_value_in_nm.Z.UnitsPerPixel}']")
        else:
            [added, scaleNode] = self.UpdateOrAddChild(ScaleNode.Create())

            if isinstance(scale_value_in_nm, float):
                scaleNode.UpdateOrAddChild(XElementWrapper('X', {'UnitsOfMeasure': 'nm',
                                                                 'UnitsPerPixel': str(scale_value_in_nm)}))
                scaleNode.UpdateOrAddChild(XElementWrapper('Y', {'UnitsOfMeasure': 'nm',
                                                                 'UnitsPerPixel': str(scale_value_in_nm)}))
            elif isinstance(scale_value_in_nm, int):
                scaleNode.UpdateOrAddChild(XElementWrapper('X', {'UnitsOfMeasure': 'nm',
                                                                 'UnitsPerPixel': str(scale_value_in_nm)}))
                scaleNode.UpdateOrAddChild(XElementWrapper('Y', {'UnitsOfMeasure': 'nm',
                                                                 'UnitsPerPixel': str(scale_value_in_nm)}))
            elif isinstance(scale_value_in_nm, ScaleAxis):
                scaleNode.UpdateOrAddChild(
                    XElementWrapper('X', {'UnitsOfMeasure': str(scale_value_in_nm.UnitsOfMeasure),
                                          'UnitsPerPixel': str(scale_value_in_nm.UnitsPerPixel)}))
                scaleNode.UpdateOrAddChild(
                    XElementWrapper('Y', {'UnitsOfMeasure': str(scale_value_in_nm.UnitsOfMeasure),
                                          'UnitsPerPixel': str(scale_value_in_nm.UnitsPerPixel)}))
            else:
                raise NotImplementedError("Unknown type %s" % scale_value_in_nm)

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
