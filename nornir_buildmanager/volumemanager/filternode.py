from __future__ import annotations

import nornir_buildmanager
import nornir_buildmanager.volumemanager
from nornir_buildmanager.volumemanager import ContrastHandler, HistogramNode, ImageNode, ImageSetNode, Scale, \
    TilePyramidNode, TilesetNode, XNamedContainerElementWrapped


class FilterNode(XNamedContainerElementWrapped, ContrastHandler):
    DefaultMaskName = "Mask"

    def DefaultImageName(self, extension: str) -> str:
        """Default name for an image in this filters imageset"""
        InputChannelNode = self.FindParent('Channel')
        section_node = InputChannelNode.FindParent('Section')
        return BuildFilterImageName(section_node.Number, InputChannelNode.Name, self.Name, extension)

    @property
    def Scale(self) -> Scale | None:
        """Returns the scale if it is specified in a parent Channel Node"""
        channelNode = self.FindParent('Channel')
        if channelNode is None:
            return None

        return channelNode.Scale

    @property
    def Histogram(self):
        """Get the image set for the filter, create if missing"""

        # imageset = self.GetChildByAttrib('ImageSet', 'Name', ImageSetNode.Name)
        # There should be only one Imageset, so use find
        histogram = self.find('Histogram')
        if histogram is None:
            raise NotImplementedError("Creation of missing histogram node is deprecated")
            # histogram = HistogramNode.Create(InputTransformNode=None)
            # self.append(histogram)

        return histogram

    @property
    def BitsPerPixel(self) -> int | None:
        val = self.attrib.get('BitsPerPixel', None)
        if val is not None:
            val = int(val)

        return val

    @BitsPerPixel.setter
    def BitsPerPixel(self, val):
        if val is None:
            if 'BitsPerPixel' in self.attrib:
                del self.attrib['BitsPerPixel']
        else:
            self.attrib['BitsPerPixel'] = '%d' % val

    def GetOrCreateTilePyramid(self) -> (bool, TilePyramidNode):
        # pyramid = self.GetChildByAttrib('TilePyramid', "Name", TilePyramidNode.Name)
        # There should be only one Imageset, so use find
        pyramid = self.find('TilePyramid')
        if pyramid is None:
            pyramid = TilePyramidNode.Create(NumberOfTiles=0)
            self.append(pyramid)
            return True, pyramid
        else:
            return False, pyramid

    @property
    def TilePyramid(self) -> TilePyramidNode:
        # pyramid = self.GetChildByAttrib('TilePyramid', "Name", TilePyramidNode.Name)
        # There should be only one Imageset, so use find
        pyramid = self.find('TilePyramid')
        if pyramid is None:
            pyramid = TilePyramidNode.Create(NumberOfTiles=0)
            self.append(pyramid)

        return pyramid

    @property
    def HasTilePyramid(self) -> bool:
        return not self.find('TilePyramid') is None

    @property
    def HasImageset(self) -> bool:
        return not self.find('ImageSet') is None

    @property
    def HasTileset(self) -> bool:
        return not self.find('Tileset') is None

    @property
    def Tileset(self) -> TilesetNode | None:
        """Get the tileset for the filter, create if missing"""
        # imageset = self.GetChildByAttrib('ImageSet', 'Name', ImageSetNode.Name)
        # There should be only one Imageset, so use find
        tileset = self.find('Tileset')
        return tileset

    @property
    def Imageset(self) -> ImageSetNode:
        """Get the imageset for the filter, create if missing"""
        # imageset = self.GetChildByAttrib('ImageSet', 'Name', ImageSetNode.Name)
        # There should be only one Imageset, so use find
        imageset = self.find('ImageSet')
        if imageset is None:
            imageset = ImageSetNode.Create()
            self.append(imageset)

        return imageset

    @property
    def MaskImageset(self) -> ImageSetNode | None:
        """Get the imageset for the default mask"""

        maskFilter = self.GetMaskFilter()
        if maskFilter is None:
            return None

        return maskFilter.Imageset

    @property
    def MaskName(self) -> str | None:
        """The default mask to use for this filter"""
        m = self.attrib.get("MaskName", None)
        if m is not None:
            if len(m) == 0:
                m = None

        return m

    @MaskName.setter
    def MaskName(self, val):
        if val is None:
            if 'MaskName' in self.attrib:
                del self.attrib['MaskName']
        else:
            self.attrib['MaskName'] = val

    def GetOrCreateMaskName(self) -> str:
        """Returns the maskname for the filter, if it does not exist use the default mask name"""
        if self.MaskName is None:
            self.MaskName = FilterNode.DefaultMaskName

        return self.MaskName

    @property
    def HasMask(self) -> bool:
        """
        :return: True if the mask filter exists
        """
        return not self.GetMaskFilter() is None

    def GetMaskFilter(self, MaskName: str = None) -> FilterNode | None:
        if MaskName is None:
            MaskName = self.MaskName

        if MaskName is None:
            return None

        assert (isinstance(MaskName, str))

        return self.Parent.GetFilter(MaskName)

    def GetOrCreateMaskFilter(self, MaskName: str = None) -> FilterNode:
        if MaskName is None:
            MaskName = self.GetOrCreateMaskName()

        assert (isinstance(MaskName, str))

        return self.Parent.GetOrCreateFilter(MaskName)

    def GetImage(self, Downsample) -> ImageNode | None:
        if not self.HasImageset:
            return None

        return self.Imageset.GetImage(Downsample)

    def GetOrCreateImage(self, Downsample) -> ImageNode:
        """As described, raises a nornir_buildmanager.NornirUserException if the image cannot be generated"""
        imageset = self.Imageset
        return imageset.GetOrCreateImage(Downsample)

    def GetMaskImage(self, Downsample) -> ImageNode | None:
        maskFilter = self.GetMaskFilter()
        if maskFilter is None:
            return None

        return maskFilter.GetImage(Downsample)

    def GetOrCreateMaskImage(self, Downsample) -> ImageNode:
        """As described, raises a nornir_buildmanager.NornirUserException if the image cannot be generated"""
        (added_mask_filter, maskFilter) = self.GetOrCreateMaskFilter()
        return maskFilter.GetOrCreateImage(Downsample)

    def GetHistogram(self) -> HistogramNode:
        return self.find('Histogram')

    @property
    def NeedsValidation(self) -> bool:
        return True

    @classmethod
    def Create(cls, Name: str, Path: str = None, **extra) -> FilterNode:
        return super(FilterNode, cls).Create(tag='Filter', Name=Name, Path=Path, **extra)

    def _LogContrastMismatch(self, MinIntensityCutoff, MaxIntensityCutoff, Gamma):
        print("\tCurrent values (%g,%g,%g), target (%g,%g,%g)" % (
            self.MinIntensityCutoff, self.MaxIntensityCutoff, self.Gamma, MinIntensityCutoff, MaxIntensityCutoff,
            Gamma))


#       XElementWrapper.logger.warning("\tCurrent values (%g,%g,%g), target (%g,%g,%g)" % (
#            self.MinIntensityCutoff, self.MaxIntensityCutoff, self.Gamma, MinIntensityCutoff, MaxIntensityCutoff,
#            Gamma))


def BuildFilterImageName(SectionNumber: int, ChannelName: str, FilterName: str, Extension=None) -> str:
    return nornir_buildmanager.templates.Current.SectionTemplate % SectionNumber + \
        f"_{ChannelName}_{FilterName}{Extension}"
