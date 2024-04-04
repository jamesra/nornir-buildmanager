from __future__ import annotations

import os

from numpy.typing import NDArray

import nornir_buildmanager.volumemanager as volumemanager
import nornir_imageregistration
import nornir_shared.checksum


class ImageNode(volumemanager.InputTransformHandler, volumemanager.XFileElementWrapper):
    """Refers to an image file"""
    DefaultName = "image.png"

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Image'

        super(ImageNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, Path: str, attrib=None, **extra) -> ImageNode:
        return ImageNode(tag='Image', Path=Path, attrib=attrib, **extra)

    def IsValid(self) -> (bool, str):
        if self.NeedsValidation:
            try:
                if self.Checksum != nornir_shared.checksum.FilesizeChecksum(self.FullPath):
                    return False, "Checksum mismatch"
                else:
                    self.UpdateValidationTime()  # Record the last time we checked the file
            except FileNotFoundError:
                return False, f"File not found {self.FullPath}"

        valid, reason = super(ImageNode, self).IsValid()
        if valid:
            return self.InputTransformIsValid()
        else:
            return valid, reason

    @property
    def Checksum(self) -> str | None:
        """Checksum of the image.  This is the file size in bytes for speed.
         Images are large and we don't need to read the whole file to calculate a checksum."""
        checksum = self.get('Checksum', None)
        if checksum is None:
            try:
                checksum = nornir_shared.checksum.FilesizeChecksum(self.FullPath)
                self.attrib['Checksum'] = str(checksum)
                self._AttributesChanged = True
            except FileNotFoundError:
                self.logger.debug(f'{self.FullPath} not found to calculate checksum')
                # Reasonable to assume we have no checksum because the file does not exist
                return None

        return checksum

    @property
    def InputImageChecksum(self) -> str | None:
        """Checksum of the image used to create this image.  This is the file size in bytes for speed.
         Images are large and we don't need to read the whole file to calculate a checksum."""
        result = self.attrib.get('InputTransformChecksum', None)

        # This is a workaround for RC2, possibly other volumes, from a 2015 era bug where the input transform checksum was set to '...property attribute...'
        if result is not None and 'property' in result:
            del self.attrib['InputTransformChecksum']
            self.AttributesChanged = True
            return None

        return result

    @InputImageChecksum.setter
    def InputImageChecksum(self, input_checksum: str | None) -> str | None:
        """Checksum of the image used to create this image.  This is the file size in bytes for speed.
         Images are large and we don't need to read the whole file to calculate a checksum.
         If None is passed the attribute is removed from the node."""
        if input_checksum is None:
            if 'InputTransformChecksum' in self.attrib:
                del self.attrib['InputTransformChecksum']
        else:
            self.attrib['InputTransformChecksum'] = input_checksum

    @property
    def Dimensions(self) -> tuple[float, float]:
        """
        :return: (height, width)
        """
        dims = self.attrib.get('Dimensions', None)
        if dims is None:
            dims = nornir_imageregistration.GetImageSize(self.FullPath)
            self.attrib['Dimensions'] = "{0:d} {1:d}".format(dims[1], dims[0])
            self._AttributesChanged = True
        else:
            dims = dims.split(' ')
            dims = (int(dims[1]), int(dims[0]))  # Report as [YDim, XDim]

            # Todo: Remove after initial testing
            # actual_dims = nornir_imageregistration.GetImageSize(self.FullPath)
            # assert (actual_dims[0] == dims[0])
            # assert (actual_dims[1] == dims[1])

        return dims

    @Dimensions.setter
    def Dimensions(self, dims: NDArray[np.integer] | tuple[int, int] | None):
        """
        :param tuple dims: (height, width) or None
        """
        if dims is None:
            if 'Dimensions' in self.attrib:
                del self.attrib['Dimensions']
        else:
            self.attrib['Dimensions'] = f"{dims[1]} {dims[0]}"
