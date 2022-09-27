from __future__ import annotations

import os

from . import xresourceelementwrapper


class NotesNode(xresourceelementwrapper.XResourceElementWrapper):

    @classmethod
    def Create(cls, Text: str = None, SourceFilename: str = None, attrib: dict = None, **extra) -> NotesNode:
        obj = NotesNode(tag='Notes', attrib=attrib, **extra)

        if Text is not None:
            obj.text = Text

        if SourceFilename is not None:
            obj.SourceFilename = SourceFilename
            obj.Path = os.path.basename(SourceFilename)
        else:
            obj.SourceFilename = ""
            obj.Path = os.path.basename(SourceFilename)

        return obj

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Notes'

        super(NotesNode, self).__init__(tag=tag, attrib=attrib, **extra)

    def CleanIfInvalid(self)-> (bool, str):
        return False, None
