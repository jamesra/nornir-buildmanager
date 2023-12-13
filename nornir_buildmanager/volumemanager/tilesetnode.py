from __future__ import annotations

import os

import nornir_imageregistration
import nornir_buildmanager 
from nornir_buildmanager.volumemanager import XContainerElementWrapper, PyramidLevelHandler, InputTransformHandler
from nornir_shared import prettyoutput as prettyoutput
import nornir_imageregistration


class TilesetNode(XContainerElementWrapper, PyramidLevelHandler, InputTransformHandler):
    DefaultPath = 'Tileset'

    @property
    def CoordFormat(self) -> str:
        return self.attrib.get('CoordFormat', None)

    @CoordFormat.setter
    def CoordFormat(self, val):
        self.attrib['CoordFormat'] = val

    @property
    def FilePrefix(self) -> str:
        return self.attrib.get('FilePrefix', None)

    @FilePrefix.setter
    def FilePrefix(self, val):
        self.attrib['FilePrefix'] = val

    @property
    def FilePostfix(self) -> str:
        return self.attrib.get('FilePostfix', None)

    @FilePostfix.setter
    def FilePostfix(self, val):
        self.attrib['FilePostfix'] = val

    @property
    def TileXDim(self) -> int:
        val = self.attrib.get('TileXDim', None)
        if val is not None:
            val = int(val)

        return val

    @TileXDim.setter
    def TileXDim(self, val):
        self.attrib['TileXDim'] = '%d' % int(val)

    @property
    def TileYDim(self) -> int:
        val = self.attrib.get('TileYDim', None)
        if val is not None:
            val = int(val)

        return val

    @TileYDim.setter
    def TileYDim(self, val):
        self.attrib['TileYDim'] = '%d' % int(val)

    @classmethod
    def Create(cls, attrib: dict | None = None, **extra) -> TilesetNode:
        return cls(tag='Tileset',
                   attrib=attrib,
                   **extra)

    def __init__(self, tag=None, attrib=None, Path=None, **extra):
        if tag is None:
            tag = 'Tileset'

        if Path is None:
            Path = TilesetNode.DefaultPath

        super(TilesetNode, self).__init__(tag=tag, Path=Path, attrib=attrib, **extra)

        if 'Path' not in self.attrib:
            self.attrib['Path'] = TilesetNode.DefaultPath

    def GenerateLevels(self, Levels):
        node = nornir_buildmanager.operations.tile.BuildTilesetPyramid(self)
        if node is not None:
            node.Save()

    @property
    def NeedsValidation(self) -> bool:
        # We don't check with the base class' last directory modification because
        # we cannot save the metadata without changing the timestamp, so we
        # only look at the input transform (which will not exist for volumes built
        # before June 8th 2020.)  If there is no input transform then no validation
        # is done and tilesets must be deleted manually to refresh them.
        # if super(TilesetNode, self).NeedsValidation:
        #    return True

        input_needs_validation = InputTransformHandler.InputTransformNeedsValidation(self)
        return input_needs_validation[0]

    def IsValid(self) -> (bool, str):
        """Check if the TileSet is valid.  Be careful using this, because it only checks the existing meta-data.
           If you are comparing to a new input transform you should use VMH.IsInputTransformMatched"""

        [valid, reason] = super(TilesetNode, self).IsValid()
        prettyoutput.Log('Validate: {0}'.format(self.FullPath))
        if valid:
            (valid, reason) = InputTransformHandler.InputTransformIsValid(self)
            # if valid:
            # [valid, reason] = super(TransformNode, self).IsValid()

        # We can delete a locked transform if it does not exist on disk
        if not valid and not os.path.exists(self.FullPath):
            self.Locked = False

        return valid, reason

    def IsLevelValid(self, level_node, GridDimX: int, GridDimY: int):
        """
        :param level_node:
        :param GridDimX:
        :param GridDimY:
    """

        if GridDimX is None or GridDimY is None:
            return False, "No grid dimensions found in tileset"

        level_full_path = level_node.FullPath

        GridXDim = GridDimX - 1  # int(GridDimX) - 1
        GridYDim = GridDimY - 1  # int(GridDimY) - 1

        FilePrefix = self.FilePrefix
        FilePostfix = self.FilePostfix

        GridXString = nornir_buildmanager.templates.Current.GridTileCoordTemplate % GridXDim
        # MatchString = os.path.join(OutputDir, FilePrefix + 'X%' + nornir_buildmanager.templates.GridTileCoordFormat % GridXDim + '_Y*' + FilePostfix)
        MatchString = os.path.join(level_full_path,
                                   nornir_buildmanager.templates.Current.GridTileMatchStringTemplate % {
                                       'prefix': FilePrefix,
                                       'X': GridXString,
                                       'Y': '*',
                                       'postfix': FilePostfix})

        # Start with the middle because it is more likely to have a match earlier
        TestIndicies = list(range(GridYDim // 2, GridYDim))
        TestIndicies.extend(list(range((GridYDim // 2) - 1, -1, -1)))
        for iY in TestIndicies:
            # MatchString = os.path.join(OutputDir, FilePrefix +
            #                           'X' + nornir_buildmanager.templates.GridTileCoordFormat % GridXDim +
            #                           '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % iY +
            #                           FilePostfix)
            MatchString = os.path.join(level_full_path,
                                       nornir_buildmanager.templates.Current.GridTileMatchStringTemplate % {
                                           'prefix': FilePrefix,
                                           'X': GridXString,
                                           'Y': nornir_buildmanager.templates.Current.GridTileCoordTemplate % iY,
                                           'postfix': FilePostfix})
            if os.path.exists(MatchString):
                [YSize, XSize] = nornir_imageregistration.GetImageSize(MatchString)
                if YSize != self.TileYDim or XSize != self.TileXDim:
                    return [False, "Image size does not match meta-data"]

                level_node.UpdateValidationTime()
                return [True, "Last column of tileset found"]

            MatchString = os.path.join(level_full_path, nornir_buildmanager.templates.Current.GridTileNameTemplate % {
                'prefix': FilePrefix,
                'X': GridXDim,
                'Y': iY,
                'postfix': FilePostfix})
            if os.path.exists(MatchString):
                [YSize, XSize] = nornir_imageregistration.GetImageSize(MatchString)
                if YSize != self.TileYDim or XSize != self.TileXDim:
                    return [False, "Image size does not match meta-data"]

                level_node.UpdateValidationTime()
                return [True, "Last column of tileset found"]

        return [False, "Last column of tileset not found"]
