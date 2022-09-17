from __future__ import annotations

import os

import nornir_buildmanager
from nornir_buildmanager.volumemanager import XContainerElementWrapper, PyramidLevelHandler
from nornir_shared import prettyoutput as prettyoutput


class TilePyramidNode(XContainerElementWrapper, PyramidLevelHandler):
    """A collection of images, all downsampled for each levels"""

    DefaultName = 'TilePyramid'
    DefaultPath = 'TilePyramid'

    @property
    def LevelFormat(self) -> str:
        return self.attrib.get('LevelFormat', None)

    @LevelFormat.setter
    def LevelFormat(self, val):
        assert (isinstance(val, str))
        self.attrib['LevelFormat'] = val

    @property
    def NumberOfTiles(self) -> int:
        return int(self.attrib.get('NumberOfTiles', 0))

    @NumberOfTiles.setter
    def NumberOfTiles(self, val):
        self.attrib['NumberOfTiles'] = '%d' % val

    @property
    def ImageFormatExt(self) -> str:
        return self.attrib.get('ImageFormatExt', None)

    @ImageFormatExt.setter
    def ImageFormatExt(self, val):
        assert (isinstance(val, str))
        self.attrib['ImageFormatExt'] = val

    @property
    def Type(self) -> str:
        """The default mask to use for this filter"""
        m = self.attrib.get("Type", None)
        if m is not None:
            if len(m) == 0:
                m = None

        return m

    @Type.setter
    def Type(self, val):
        if val is None:
            if 'Type' in self.attrib:
                del self.attrib['Type']
        else:
            self.attrib['Type'] = val

    def ImagesInLevel(self, level_node):
        """
        :return: A list of all images contained in the level directory
        :rtype: list
        """

        level_full_path = level_node.FullPath
        expectedExtension = self.ImageFormatExt

        try:
            images = []
            with os.scandir(level_full_path) as pathscan:
                for item in pathscan:
                    if item.is_file() is False:
                        continue

                    if item.name[0] == '.':  # Avoid the .desktop_ini files of the world
                        continue

                    (root, ext) = os.path.splitext(item.name)
                    if ext != expectedExtension:
                        continue

                    images.add(item.path)

            return True, images

        except FileNotFoundError:
            return []

    @property
    def NeedsValidation(self) -> bool:
        return True

    def IsValid(self) -> (bool, str):
        """Remove level directories without files, or with more files than they should have"""

        (valid, reason) = super(TilePyramidNode, self).IsValid()
        if not valid:
            return valid, reason

        return valid, reason

        # Starting with the highest resolution level, we need to check that all
        # of the levels are valid

    def CheckIfLevelTilesExistViaMetaData(self, level_node):
        """
        Using the meta-data, returns whether there is a reasonable belief that
        the passed level has all of the tiles and that they are valid
        :return: True if the level should have its contents validated
        """

        level_full_path = level_node.FullPath

        if self.Parent is None:  # Don't check for validity if our node has not been added to the tree yet
            if not os.path.isdir(level_full_path):
                return False, '{0} directory does not exist'.format(level_full_path)
            else:
                return True, 'Element has not been added to the tree'

        level_has_changes = level_node.ChangesSinceLastValidation

        if level_has_changes is None:
            return [False, '{0} directory does not exist'.format(level_full_path)]

        if level_has_changes:
            prettyoutput.Log('Validating tiles in {0}, directory was modified since last check'.format(level_full_path))
            nornir_buildmanager.operations.tile.VerifyTiles(level_node)

        # The "No modifications since last validation case"
        if self.NumberOfTiles == level_node.TilesValidated:
            return (
                True, "Tiles validated previously, directory has not been modified, and # validated == # in Pyramid")
        elif self.NumberOfTiles < level_node.TilesValidated:
            return True, "More tiles validated than expected in level"
        else:
            return False, "Fewer tiles validated than expected in level"

    def TryToMakeLevelValid(self, level_node):
        """
        :param str level_full_path: The path to the directories containing the image files
        :return: (Bool, String) containing whether all tiles exist and a reason string
        """

        (ProbablyGood, Reason) = self.CheckIfLevelTilesExistViaMetaData(level_node)

        if ProbablyGood:
            return True, Reason

        # Attempt to regenerate the level, then we'll check again for validity
        self.GenerateLevels(level_node.Downsample)
        # if output is not None:
        #    output.Save()

        (ProbablyGood, Reason) = self.CheckIfLevelTilesExistViaMetaData(level_node)

        if ProbablyGood:
            return True, Reason

        return ProbablyGood, Reason

    #
    #         globfullpath = os.path.join(level_full_path, '*' + self.ImageFormatExt)
    #
    #         files = glob.glob(globfullpath)
    #
    #         if(len(files) == 0):
    #             return [False, "No files in level"]
    #
    #         FileNumberMatch = len(files) <= self.NumberOfTiles
    #
    #         if not FileNumberMatch:
    #             return [False, "File count mismatch for level"]
    #
    #         return [True, None]

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'TilePyramid'

        super(TilePyramidNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, NumberOfTiles=0, LevelFormat=None, ImageFormatExt=None, attrib=None, **extra):
        if LevelFormat is None:
            LevelFormat = nornir_buildmanager.templates.Current.LevelFormat

        if ImageFormatExt is None:
            ImageFormatExt = '.png'

        obj = cls(tag='TilePyramid',
                  Path=TilePyramidNode.DefaultPath,
                  attrib=attrib,
                  NumberOfTiles=str(NumberOfTiles),
                  ImageFormatExt=ImageFormatExt,
                  LevelFormat=LevelFormat,
                  **extra)

        return obj

    def GenerateLevels(self, Levels):
        node = nornir_buildmanager.operations.tile.BuildTilePyramids(self, Levels)
        if node is not None:
            node.Save()
