from __future__ import annotations

from nornir_buildmanager.volumemanager.imagesetbasenode import ImageSetBaseNode


class ImageSetNode(ImageSetBaseNode):
    """Represents single image at various downsample levels"""

    @property
    def Checksum(self) -> str:
        raise NotImplementedError(
            "Checksum on ImageSet... not sure why this would be needed.  Try using checksum of highest resolution image instead?")

    DefaultPath = 'Images'

    def FindDownsampleForSize(self, requested_size):
        """Find the smallest existing image of the requested size or greater.  If it does not exist return the maximum resolution level
        :param tuple requested_size: Either a tuple or integer.  A tuple requires both dimensions to be larger than the requested_size.  A integer requires only one of the dimensions to be larger.
        :return: Downsample level
        """

        level = self.MinResLevel
        while level.Downsample > self.MaxResLevel.Downsample:
            levelImg = self.GetImage(level)
            if levelImg is None:
                level = self.MoreDetailedLevel(level.Downsample)

            dim = self.GetImage(level).Dimensions
            if isinstance(requested_size, tuple):
                if dim[0] >= requested_size[0] and dim[1] >= requested_size[1]:
                    return level.Downsample
            elif dim[0] >= requested_size or dim[1] >= requested_size:
                return level.Downsample

            level = self.MoreDetailedLevel(level.Downsample)

        return self.MaxResLevel.Downsample


    def NameMatchesSectionNumber(self, section_number: int) -> bool:
        """
        When sections are swapped the assembled images have the incorrect name.  In this case we should either regenerate them
        or rename them.  This function tells us if the numbers are not correct.
        :return: True if first image in set has a name with a section number and the section number does not match
        :rtype: bool
        """

        # Determine if we have any images
        try:
            first_image = next(self.Images)
        except StopIteration:
            return False
        
        # Determine if the name starts with a number
        parts = first_image.Path.split('_')
        try:
            return int(parts[0]) == section_number
        except ValueError:
            return False #Naming convention does not use section number at start of name.  Continue

    def CleanIfNameHasSectionNumberMismatch(self, section_number: int) -> bool:

        if not self.NameMatchesSectionNumber(section_number):
            self.Clean("ImageSet file name does not match section number")
            return True

        return False


    def IsLevelValid(self, level_node) -> bool:
        raise NotImplemented("HasImage is being used.  I considered this a dead code path.")

    #         '''
    #         :param str level_full_path: The path to the directories containing the image files
    #         :return: (Bool, String) containing whether all tiles exist and a reason string
    #         '''
    #
    #         level_full_path = level_node.FullPath
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
            tag = 'ImageSet'

        super(ImageSetNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, Type: str = None, attrib: dict = None, **extra) -> ImageSetNode:
        if Type is None:
            Type = ""

        obj = ImageSetNode(Type=Type, Path=ImageSetNode.DefaultPath, attrib=attrib, **extra)

        return obj
