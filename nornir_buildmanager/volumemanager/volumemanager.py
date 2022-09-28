from __future__ import annotations

import operator
import os
from xml.etree import ElementTree as ElementTree

import nornir_shared.checksum
import nornir_buildmanager.volumemanager
from nornir_shared import prettyoutput as prettyoutput




# __LoadedVolumeXMLDict__ = dict()

class VolumeManager:

    def __init__(self, volumeData, filename):
        self.Data = volumeData
        self.XMLFilename = filename
        return

    @classmethod
    def Create(cls, VolumePath: str):
        VolumeManager.Load(VolumePath, Create=True)

    @classmethod
    def Load(cls, VolumePath: str, Create: bool = False, UseCache: bool = True):
        """Load the volume information for the specified directory or create one if it doesn't exist"""
        Filename = os.path.join(VolumePath, "VolumeData.xml")
        if not os.path.exists(Filename):
            prettyoutput.Log("Provided volume description file does not exist: " + Filename)

            OldVolume = os.path.join(VolumePath, "Volume.xml")
            if os.path.exists(OldVolume):
                VolumeRoot = ElementTree.parse(OldVolume).getroot()
                # Volumes.CreateFromDOM(XMLTree)
                VolumeRoot.attrib['Path'] = VolumePath
                (wrapped, VolumeRoot) = nornir_buildmanager.volumemanager.WrapElement(VolumeRoot)
                nornir_buildmanager.volumemanager.SetElementParent(VolumeRoot, None)
                VolumeRoot.Save()

        SaveNewVolume = False
        if not os.path.exists(Filename):
            if Create:
                os.makedirs(VolumePath, exist_ok=True)

                VolumeRoot = ElementTree.Element('Volume', {"Name": os.path.basename(VolumePath), "Path": VolumePath})
                SaveNewVolume = True
                # VM =  VolumeManager(VolumeData, Filename)
                # return VM
            else:
                return None
        else:
            # 5/16/2012 Loading these XML files is really slow, so they are cached.
            # if not __LoadedVolumeXMLDict__.get(Filename, None) is None and UseCache:
            #    XMLTree = __LoadedVolumeXMLDict__[Filename]
            # else:

            # I could use ElementTree.parse here.  However, there was a rare
            # bug where saving the file would encounter a permissions error
            # loading the file and closing it myself seems to have solved
            # the problem
            RawXML = None
            with open(Filename, 'rb') as hFile:
                RawXML = hFile.read()
            VolumeRoot = ElementTree.fromstring(RawXML)
            # VolumeData = Volumes.CreateFromDOM(XMLTree)
            # __LoadedVolumeXMLDict__[Filename] = XMLTree

            # VolumeRoot = XMLTree.getroot()

        VolumeRoot.attrib['Path'] = VolumePath
        VolumeRoot = nornir_buildmanager.volumemanager.VolumeNode.wrap(VolumeRoot)
        nornir_buildmanager.volumemanager.SetElementParent(VolumeRoot, None)

        if SaveNewVolume:
            VolumeRoot.Save()

        prettyoutput.Log("Volume Root: " + VolumeRoot.attrib['Path'])
        # VolumeManager.__RemoveElementsWithoutPath__(VolumeRoot)

        return VolumeRoot
        # return cls.__init__(VolumeData, Filename)





    @classmethod
    def __SortNodes__(cls, element):
        """Remove all elements that should be found on the file system but are not"""
        element._children.sort(key=operator.attrgetter('SortKey'))

        for e in element:
            cls.__SortNodes__(e)

    @classmethod
    def __RemoveEmptyElements__(cls, Element):
        """Remove all elements that should be found on the file system but are not"""
        for i in range(len(Element) - 1, -1, -1):
            e = Element[i]
            if len(list(e.keys())) == 0 and len(list(e)) == 0:
                Element.remove(e)
                continue

            cls.__RemoveEmptyElements__(e)

        return

    def __str__(self):
        return self.VolumeData.toxml()

    @classmethod
    def CalcVolumeChecksum(cls, VolumeObj):
        XMLString = ElementTree.tostring(VolumeObj, encoding="utf-8")
        OldChecksum = VolumeObj.get('Checksum', '')
        XMLString = XMLString.replace(OldChecksum, "", 1)
        return nornir_shared.checksum.DataChecksum(XMLString)

    @classmethod
    def SaveSingleFile(cls, VolumeObj, xmlfile_fullpath):
        """Save the volume to a single XML file"""

        fullpath = os.path.dirname(xmlfile_fullpath)

        os.makedirs(fullpath, exist_ok=True)

        # prettyoutput.Log("Saving %s" % xmlfilename)

        # prettyoutput.Log("Saving %s" % XMLFilename)

        OutputXML = ElementTree.tostring(VolumeObj, encoding="utf-8")
        # print OutputXML
        with open(xmlfile_fullpath, 'wb') as hFile:
            hFile.write(OutputXML)

    @classmethod
    def Save(cls, VolumeObj):
        """Save the volume to an XML file, putting sub-elements in seperate folders"""

        # We cannot include the volume checksum in the calculation because including it changes the checksum'''
        if hasattr(VolumeObj, 'Save'):
            VolumeObj.Save(tabLevel=None)
        elif hasattr(VolumeObj, 'Parent'):
            cls.Save(VolumeObj.Parent)
        else:
            raise ValueError("Trying to save element with no Save function or parent {0}".format(str(VolumeObj)))
