from __future__ import annotations

import os

from . import xfileelementwrapper
from nornir_imageregistration.files import stosfile, mosaicfile


class MosaicBaseNode(xfileelementwrapper.XFileElementWrapper):

    @classmethod
    def GetFilename(cls, Name: str, Type: str, Ext: str = None):
        """
        Returns the filename for a given mosaic
        :param str Name: Name of the mosaic
        :param str Type: Type/Settings information for the mosaic
        :param str Ext: Extension to use, defaults to .mosaic
        :rtype: str
        """
        if Ext is None:
            Ext = '.mosaic'
        path = Name + Type + Ext
        return path

    def _CalcChecksum(self):
        (file, ext) = os.path.splitext(self.Path)
        ext = ext.lower()

        # Checking for the file here is a waste of time
        # since both stos and mosaic file loaders also check
        # if not os.path.exists(self.FullPath):
        # return None

        if ext == '.stos':
            return stosfile.StosFile.LoadChecksum(self.FullPath)
        elif ext == '.mosaic':
            return mosaicfile.MosaicFile.LoadChecksum(self.FullPath)
        else:
            raise Exception("Cannot compute checksum for unknown transform type")

    def ResetChecksum(self):
        """Recalculate the checksum for the element"""
        if 'Checksum' in self.attrib:
            del self.attrib['Checksum']

        self.attrib['Checksum'] = self._CalcChecksum()
        self._AttributesChanged = True

    @property
    def Checksum(self) -> str:
        """Checksum of the file resource when the node was last updated"""
        checksum = self.attrib.get('Checksum', None)
        if checksum is None:
            checksum = self._CalcChecksum()
            self.attrib['Checksum'] = checksum
            return checksum

        return checksum

    @Checksum.setter
    def Checksum(self, val):
        """Checksum of the file resource when the node was last updated"""
        self.attrib['Checksum'] = val
        raise DeprecationWarning(
            "Checksums for mosaic elements will not be directly settable soon.  Use ResetChecksum instead")

    def IsValid(self) -> (bool, str):
        result = super(MosaicBaseNode, self).IsValid()

        if result[0]:
            knownChecksum = self.attrib.get('Checksum', None)
            if knownChecksum is not None:
                fileChecksum = self._CalcChecksum()

                if not knownChecksum == fileChecksum:
                    return False, "File checksum does not match meta-data"

        return result

    @classmethod
    def Create(cls, tag, Name, Type, Path=None, attrib=None, **extra):

        if Path is None:
            Path = MosaicBaseNode.GetFilename(Name, Type)

        obj = MosaicBaseNode(tag=tag, Path=Path, Name=Name, Type=Type, attrib=attrib, **extra)

        return obj

    @property
    def InputTransformName(self) -> str:
        return self.get('InputTransformName', '')

    @InputTransformName.setter
    def InputTransformName(self, Value):
        self.attrib['InputTransformName'] = Value

    @property
    def InputImageDir(self) -> str:
        return self.get('InputTransform', '')

    @InputImageDir.setter
    def InputImageDir(self, Value):
        self.attrib['InputImageDir'] = Value

    @property
    def InputTransformChecksum(self) -> str:
        return self.get('InputTransformChecksum', '')

    @InputTransformChecksum.setter
    def InputTransformChecksum(self, Value):
        self.attrib['InputTransformChecksum'] = Value

    @property
    def Type(self) -> str:
        return self.attrib.get('Type', '')

    @Type.setter
    def Type(self, Value):
        self.attrib['Type'] = Value
