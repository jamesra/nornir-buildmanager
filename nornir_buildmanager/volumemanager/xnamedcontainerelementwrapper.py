from __future__ import annotations

from . import xcontainerelementwrapper

class XNamedContainerElementWrapped(xcontainerelementwrapper.XContainerElementWrapper):
    """XML meta-data for a container whose sub-elements are contained within a directory on the file system whose name is not constant.  Such as a channel name."""

    @property
    def Name(self) -> str:
        return self.get('Name', '')

    @Name.setter
    def Name(self, Value):
        self.attrib['Name'] = Value

    def __init__(self, tag, attrib=None, **extra):
        super(XNamedContainerElementWrapped, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, tag, Name, Path=None, attrib=None, **extra) -> XNamedContainerElementWrapped:
        if Path is None:
            Path = Name

        if attrib is None:
            attrib = {}

        obj = cls(tag=tag, Path=Path, Name=Name, attrib=attrib, **extra)

        return obj
