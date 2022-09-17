from __future__ import annotations

import os

import nornir_imageregistration
import nornir_buildmanager.volumemanager as volumemanager
import nornir_shared.checksum

class ImageNode(volumemanager.InputTransformHandler, volumemanager.XFileElementWrapper):
    """Refers to an image file"""
    DefaultName = "image.png"

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Image'

        super(ImageNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, Path: str, attrib=None, **extra):
        return ImageNode(tag='Image', Path=Path, attrib=attrib, **extra)

    def IsValid(self) -> (bool, str):
        if not os.path.exists(self.FullPath):
            return False, 'File does not exist'

        if self.Checksum != nornir_shared.checksum.FilesizeChecksum(self.FullPath):
            return False, "Checksum mismatch"

        return super(ImageNode, self).IsValid()

    @property
    def Checksum(self) -> str:
        checksum = self.get('Checksum', None)
        if checksum is None:
            checksum = nornir_shared.checksum.FilesizeChecksum(self.FullPath)
            self.attrib['Checksum'] = str(checksum)

        return checksum

    @property
    def Dimensions(self):
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
            dims = (int(dims[1]), int(dims[0]))

            # Todo: Remove after initial testing
            actual_dims = nornir_imageregistration.GetImageSize(self.FullPath)
            assert (actual_dims[0] == dims[0])
            assert (actual_dims[1] == dims[1])

        return dims

    @Dimensions.setter
    def Dimensions(self, dims):
        """
        :param tuple dims: (height, width) or None
        """
        if dims is None:
            if 'Dimensions' in self.attrib:
                del self.attrib['Dimensions']
        else:
            self.attrib['Dimensions'] = "{0} {1}".format(dims[1], dims[0])
