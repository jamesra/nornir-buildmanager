from __future__ import annotations

import logging
from xml.etree import ElementTree as ElementTree

from . import xcontainerelementwrapper, xelementwrapper, validation

class XLinkedContainerElementWrapper(xcontainerelementwrapper.XContainerElementWrapper):
    """Child elements of XLinkedContainerElementWrapper are saved
       individually in subdirectories and replaced with an element
       postpended with the name "_link".  This greatly speeds Pythons
       glacially slow XML writing by limiting the amount of XML
       generated"""

    def Save(self, tabLevel=None, recurse=True):
        """If recurse = False we only save this element, no child elements are saved"""

        if tabLevel is None:
            tabLevel = 0

        self.sort()

        # pool = Pools.GetGlobalThreadPool()

        logger = logging.getLogger(__name__ + '.' + 'XLinkedContainerElementWrapper')
        # tabs = '\t' * tabLevel
        # logger.info('Saving ' + tabs + str(self))
        xmlfilename = 'VolumeData.xml'
        # Create a copy of ourselves for saving
        SaveElement = ElementTree.Element(self.tag, attrib=self.attrib)
        if self.text is not None:
            SaveElement.text = self.text

        if self.tail is not None:
            SaveElement.tail = self.tail

        validation.ValidateAttributesAreStrings(self, logger)

        # SaveTree = ElementTree.ElementTree(SaveElement)

        # Any child containers we create a link to and remove from our file
        for i in range(len(self) - 1, -1, -1):
            child = self[i]
            if child.tag.endswith('_Link'):
                SaveElement.append(child)
                continue

            if isinstance(child, xcontainerelementwrapper.XContainerElementWrapper) and child.SaveAsLinkedElement:
                LinkElement = xelementwrapper.XElementWrapper(child.tag + '_Link', attrib=child.attrib)
                # SaveElement.append(LinkElement)
                SaveElement.append(LinkElement)

                if recurse:
                    child.Save(tabLevel + 1)

                # logger.warn("Unloading " + child.tag)
                # del self[i]
                # self.append(LinkElement)
            else:
                if isinstance(SaveElement, xelementwrapper.XElementWrapper):
                    validation.ValidateAttributesAreStrings(SaveElement, logger)
                    SaveElement.sort()

                SaveElement.append(child)

        self.__SaveXML(xmlfilename, SaveElement)
