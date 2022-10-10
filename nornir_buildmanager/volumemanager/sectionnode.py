from __future__ import annotations

from typing import Generator

import nornir_buildmanager
import nornir_buildmanager.volumemanager

from nornir_buildmanager.volumemanager import ChannelNode, XNamedContainerElementWrapped

class SectionNode(XNamedContainerElementWrapped):

    @classmethod
    def ClassSortKey(cls, self):
        """Required for objects derived from XContainerElementWrapper"""
        return f"Section {self.Number:04d}"

    @property
    def SortKey(self):
        """The default key used for sorting elements"""
        return SectionNode.ClassSortKey(self)

    @property
    def Channels(self) -> Generator[ChannelNode]:
        return self.findall('Channel')

    @property
    def Number(self) -> int:
        return int(self.get('Number', '0'))

    @Number.setter
    def Number(self, Value: int):
        self.attrib['Number'] = str(int(Value))

    def GetChannel(self, Channel: str) -> ChannelNode:
        return self.GetChildByAttrib('Channel', 'Name', Channel)  # type: ChannelNode

    def GetOrCreateChannel(self, ChannelName: str) -> ChannelNode:
        channelObj = self.GetChildByAttrib('Channel', 'Name', ChannelName)
        if channelObj is None:
            channelObj = ChannelNode.Create(ChannelName)
            return self.UpdateOrAddChildByAttrib(channelObj, 'Name')
        else:
            return False, channelObj

    def MatchChannelPattern(self, channelPattern) -> Generator[ChannelNode]:
        return nornir_buildmanager.volumemanager.SearchCollection(self.Channels,
                                    'Name',
                                    channelPattern)

    def MatchChannelFilterPattern(self, channelPattern, filterPattern) -> Generator[ChannelNode]:
        for channelNode in self.MatchChannelPattern(channelPattern):
            result = channelNode.MatchFilterPattern(filterPattern)
            if result is not None:
                yield from result

        return

    @property
    def NeedsValidation(self) -> bool:
        return True

    @classmethod
    def Create(cls, Number, Name=None, Path=None, attrib=None, **extra) -> SectionNode:

        if Name is None:
            Name = nornir_buildmanager.templates.Current.SectionTemplate % Number

        if Path is None:
            Path = nornir_buildmanager.templates.Current.SectionTemplate % Number

        obj = super(SectionNode, cls).Create(tag='Section', Name=Name, Path=Path, attrib=attrib, **extra)
        obj.Number = Number

        return obj
