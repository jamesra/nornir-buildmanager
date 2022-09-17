from __future__ import annotations

import abc
import os

import nornir_imageregistration
import nornir_buildmanager
from nornir_buildmanager.volumemanager import XContainerElementWrapper, LevelNode, ImageNode, InputTransformHandler, \
    PyramidLevelHandler
 
from nornir_shared import prettyoutput as prettyoutput


class ImageSetBaseNode(InputTransformHandler,
                       PyramidLevelHandler, XContainerElementWrapper, abc.ABC):

    @classmethod
    def Create(cls, Path, Type, attrib=None, **extra):
        obj = super(ImageSetBaseNode, cls).__init__(tag='ImageSet', Type=Type, Path=Path, attrib=attrib, **extra)
        return obj

    @property
    def Images(self) -> [ImageNode]:
        """Iterate over images in the ImageSet, highest to lowest res"""
        for levelNode in self.Levels:
            image = levelNode.find('Image')
            if image is None:
                continue
            yield image

        return

    def GetImage(self, Downsample) -> ImageNode | None:
        """Returns image node for the specified downsample or None"""

        if not isinstance(Downsample, LevelNode):
            level_node = self.GetLevel(Downsample)
        else:
            level_node = Downsample

        if level_node is None:
            return None

        image = level_node.find('Image')  # type: ImageNode
        if image is None:
            return None

        if not os.path.exists(image.FullPath):
            if image in self:
                self.remove(image)

            return None

        return image

    def HasImage(self, Downsample) -> bool:
        return not self.GetImage(Downsample) is None

    def GetOrCreateImage(self, Downsample, Path=None, GenerateData=True) -> ImageNode:
        """Returns image node for the specified downsample. Generates image if requested image is missing.  If unable to generate a ValueError is raised"""
        [added_level, level_node] = self.GetOrCreateLevel(Downsample, GenerateData=False)

        imageNode = level_node.find("Image")
        if imageNode is None:
            if GenerateData and not self.CanGenerate(Downsample):
                raise nornir_buildmanager.NornirUserException(
                    "%s cannot generate downsample %d image" % (self.FullPath, Downsample))

            if Path is None:
                Path = self.__PredictImageFilename()

            imageNode = ImageNode.Create(Path)
            [level_added, imageNode] = level_node.UpdateOrAddChild(imageNode)
            if not os.path.exists(imageNode.FullPath):
                os.makedirs(os.path.dirname(imageNode.FullPath), exist_ok=True)

                if GenerateData:
                    self.__GenerateMissingImageLevel(OutputImage=imageNode, Downsample=Downsample)

            self.Save()

        return imageNode

    def __PredictImageFilename(self) -> str:
        """Get the path of the highest resolution image in this ImageSet"""
        list_images = list(self.Images)
        if len(list_images) > 0:
            return list_images[0].Path

        raise LookupError("No images found to predict path in imageset %s" % self.FullPath)

    def GetOrPredictImageFullPath(self, Downsample) -> str:
        """Either return what the full path to the image at the downsample is, or predict what it should be if it does not exist without creating it
        :rtype str:
        """
        image_node = self.GetImage(Downsample)
        if image_node is None:
            return os.path.join(self.FullPath, LevelNode.PredictPath(Downsample), self.__PredictImageFilename())
        else:
            return image_node.FullPath

    def __GetImageNearestToLevel(self, Downsample):
        """Returns the nearest existing image and downsample level lower than the requested downsample level"""

        SourceImage = None
        SourceDownsample = Downsample / 2
        while SourceDownsample > 0:
            SourceImage = self.GetImage(SourceDownsample)
            if SourceImage is not None:
                # Only return images that actually are on disk
                if os.path.exists(SourceImage.FullPath):
                    break
                else:
                    # Probably a bad node, remove it
                    self.CleanIfInvalid()

            SourceDownsample = SourceDownsample / 2.0

        return SourceImage, SourceDownsample

    def GenerateLevels(self, Levels):
        node = nornir_buildmanager.operations.tile.BuildImagePyramid(self, Levels, Interlace=False)
        if node is not None:
            node.Save()

    def __GenerateMissingImageLevel(self, OutputImage, Downsample):
        """Creates a downsampled image from available high-res images if needed"""

        (SourceImage, SourceDownsample) = self.__GetImageNearestToLevel(Downsample)

        if SourceImage is None:
            raise nornir_buildmanager.NornirUserException(
                "No source image available to generate missing downsample level {0} : {1}".format(Downsample,
                                                                                                  OutputImage))
            # return None

        OutputImage.Path = SourceImage.Path
        if 'InputImageChecksum' in SourceImage.attrib:
            OutputImage.InputImageChecksum = SourceImage.InputImageChecksum

        nornir_imageregistration.Shrink(SourceImage.FullPath, OutputImage.FullPath,
                                        float(SourceDownsample) / float(Downsample))

        return OutputImage

    @property
    def NeedsValidation(self) -> bool:
        # if super(ImageSetBaseNode, self).NeedsValidation:
        #    return True

        input_needs_validation = nornir_buildmanager.volumemanager.InputTransformHandler.InputTransformNeedsValidation(self)
        return input_needs_validation[0]

    def IsValid(self) -> (bool, str):
        """Check if the image set is valid.  Be careful using this, because it only checks the existing meta-data.
           If you are comparing to a new input transform you should use VMH.IsInputTransformMatched"""

        [valid, reason] = super(ImageSetBaseNode, self).IsValid()
        prettyoutput.Log('Validate: {0}'.format(self.FullPath))
        if valid:
            (valid, reason) = nornir_buildmanager.volumemanager.InputTransformHandler.InputTransformIsValid(self)
            # if valid:
            # [valid, reason] = super(TransformNode, self).IsValid()

        # We can delete a locked transform if it does not exist on disk
        if not valid and not os.path.exists(self.FullPath):
            self.Locked = False

        return valid, reason

    @property
    def Checksum(self):
        raise NotImplementedError(
            "Checksum on ImageSet... not sure why this would be needed.  Try using checksum of highest resolution image instead?")
