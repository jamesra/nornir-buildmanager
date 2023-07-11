from __future__ import annotations

import datetime
import os

import nornir_buildmanager
import nornir_buildmanager.volumemanager as volumemanager


class LevelNode(volumemanager.XContainerElementWrapper):

    @property
    def SaveAsLinkedElement(self) -> bool:
        """
        See base class for full description.  This is set to false to
        prevent saving the LevelNode's VolumeData.xml from changing the
        directories last modified time.  This allows us to know if a
        directory has changed and we need to re-verify any images in the
        level.
        """
        return False

    @classmethod
    def PredictPath(cls, level) -> str:
        return nornir_buildmanager.templates.Current.LevelFormat % int(level)

    @classmethod
    def ClassSortKey(cls, self) -> str:
        """Required for objects derived from XContainerElementWrapper"""
        return "Level" + ' ' + nornir_buildmanager.templates.Current.DownsampleFormat % float(self.Downsample)

    @property
    def SortKey(self) -> str:
        """The default key used for sorting elements"""
        return LevelNode.ClassSortKey(self)

    @property
    def Name(self) -> str:
        return '%g' % self.Downsample

    @Name.setter
    def Name(self, Value: str):
        assert False, "Attempting to set name on LevelNode"

    @property
    def Downsample(self) -> float:
        assert ('Downsample' in self.attrib)
        return float(self.attrib.get('Downsample', ''))

    @Downsample.setter
    def Downsample(self, Value: float | int):
        self.attrib['Downsample'] = '%g' % Value

    @property
    def GridDimX(self) -> int:
        val = self.attrib.get('GridDimX', None)
        if val is not None:
            val = int(val)

        return val

    @GridDimX.setter
    def GridDimX(self, val: int):
        if val is None:
            if 'GridDimX' in self.attrib:
                del self.attrib['GridDimX']
        else:
            self.attrib['GridDimX'] = '%d' % int(val)

    @property
    def GridDimY(self) -> int:
        val = self.attrib.get('GridDimY', None)
        if val is not None:
            val = int(val)

        return val

    @GridDimY.setter
    def GridDimY(self, val: int):
        if val is None:
            if 'GridDimY' in self.attrib:
                del self.attrib['GridDimY']
        else:
            self.attrib['GridDimY'] = '%d' % int(val)

    @property
    def TilesValidated(self) -> bool | None:
        """
        :return: Returns None if the attribute has not been set, otherwise an integer
        """
        val = self.attrib.get('TilesValidated', None)
        if val is not None:
            val = int(val)

        return val

    @TilesValidated.setter
    def TilesValidated(self, val: bool | None):
        if val is None:
            if 'TilesValidated' in self.attrib:
                del self.attrib['TilesValidated']
        else:
            self.attrib['TilesValidated'] = '%d' % int(val)

    def IsValid(self) -> tuple[bool, str]:
        """Remove level directories without files, or with more files than they should have"""

        if not os.path.isdir(self.FullPath):
            return False, 'Directory does not exist'

        # We need to be certain to avoid the pathscan that occurs in our parent class,
        # So we check that our directory exists and call it good

        PyramidNode = self.Parent
        if isinstance(PyramidNode, nornir_buildmanager.volumemanager.TilePyramidNode):
            return PyramidNode.TryToMakeLevelValid(self)
        elif isinstance(PyramidNode, nornir_buildmanager.volumemanager.TilesetNode):
            return PyramidNode.IsLevelValid(self, self.GridDimX, self.GridDimY)
        elif isinstance(PyramidNode, nornir_buildmanager.volumemanager.ImageSetNode):
            if not PyramidNode.HasImage(self.Downsample):
                return False, "No image node found"
            # Make sure each level has at least one tile from the last column on the disk.

        return True, None

    @classmethod
    def Create(cls, Level, attrib=None, **extra) -> LevelNode:

        obj = LevelNode(tag='Level', Path=LevelNode.PredictPath(Level))

        if isinstance(Level, str):
            obj.attrib['Downsample'] = Level
        else:
            obj.attrib['Downsample'] = '%g' % Level

        return obj

    def __init__(self, tag=None, attrib=None, **extra):

        if tag is None:
            tag = 'Level'

        if attrib is None:
            attrib = {}

        super(LevelNode, self).__init__(tag='Level', attrib=attrib, **extra)

        # Remapping for legacy TileValidationTime
        if 'TileValidationTime' in self.attrib:
            val = self.attrib.get('TileValidationTime', datetime.datetime.min)
            if val is not None and isinstance(val, str):
                val = datetime.datetime.fromisoformat(val)

            self.ValidationTime = val
            del self.attrib['TileValidationTime']
