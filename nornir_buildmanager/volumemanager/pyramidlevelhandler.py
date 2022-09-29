from _operator import attrgetter

import nornir_buildmanager
import nornir_buildmanager.volumemanager as volumemanager

class PyramidLevelHandler(object):

    @property
    def Levels(self) -> [volumemanager.LevelNode]:
        return sorted(self.findall('Level'), key=attrgetter('Downsample'))

    @property
    def MaxResLevel(self) -> volumemanager.LevelNode:
        """Return the level with the highest resolution"""
        return self.Levels[0]

    @property
    def MinResLevel(self) -> volumemanager.LevelNode:
        """Return the level with the highest resolution"""
        return self.Levels[-1]

    def GetLevel(self, Downsample: float) -> volumemanager.LevelNode:
        """
        :param float Downsample: Level downsample factor from full-res data
        :return: Level node for downsample node
        :rtype LevelNode:
        """
        return self.GetChildByAttrib("Level", "Downsample", "%g" % Downsample)

    def GetOrCreateLevel(self, Downsample, GenerateData: bool = True) -> (bool, volumemanager.LevelNode | None):
        """
            :param float Downsample: Level downsample factor from full-res data
            :param bool GenerateData: True if missing data should be generated.  Defaults to true.
            :return: Level node for downsample node
            :rtype LevelNode:
        """

        if GenerateData:
            existingLevel = self.GetLevel(Downsample)
            if existingLevel is None:
                self.GenerateLevels(Downsample)
            elif isinstance(self,
                            nornir_buildmanager.volumemanager.TilePyramidNode):  # hasattr(self, 'NumberOfTiles'): #If this is a tile pyramid with many images regenerate if the verified tile count != NumberOfTiles.  If there is a mismatch regenerate
                self.TryToMakeLevelValid(existingLevel)

        (added, lnode) = self.UpdateOrAddChildByAttrib(
            nornir_buildmanager.volumemanager.LevelNode.Create(Downsample), "Downsample")
        return added, lnode

    def GenerateLevels(self, Levels):
        """Creates data to populate a level of a pyramid.  Derived class should override"""
        raise NotImplementedError('PyramidLevelHandler.GenerateMissingLevel')

    @property
    def HasLevels(self) -> bool:
        """
        :return: true if the pyramid has any levels
        """
        return len(self.Levels) > 0

    def GetScale(self) -> float | None:
        """Return the size of a pixel at full resolution"""

        Parent = self.Parent
        while Parent is not None:
            if Parent.hasattr('Scale'):
                return Parent.Scale

            Parent = self.Parent

        return None

    def HasLevel(self, Downsample) -> bool:
        return not self.GetLevel(Downsample) is None

    def CanGenerate(self, Downsample) -> bool:
        """
        :return: True if this level could be generated from higher resolution levels
        """
        return not self.MoreDetailedLevel(Downsample) is None

    def CreateLevels(self, levels: list[float]):
        assert (isinstance(levels, list))
        for level in levels:
            self.GetOrCreateLevel(level)

    def LevelIndex(self, downsample: float) -> int:
        """Returns the index number into the level nodes.  Levels with a lower index are more detailed, i.e. less downsampling.  Higher indicies are less detailed."""
        for i, obj in enumerate(self.Levels):
            if float(downsample) == float(obj.Downsample):
                return i

        raise Exception("Downsample level does not exist in levels")

    def MoreDetailedLevel(self, downsample: float) -> volumemanager.LevelNode | None:
        """Return an existing level with more detail than the provided downsample level"""
        BestChoice = None

        LevelList = self.Levels
        LevelList.reverse()

        for Level in LevelList:
            if Level.Downsample < downsample:
                return Level
            else:
                continue

        return BestChoice

    def LessDetailedLevel(self, downsample: float) -> volumemanager.LevelNode | None:
        """Return an existing level with less detail than the provided downsample level"""

        for Level in self.Levels:
            if Level.Downsample > downsample:
                return Level
