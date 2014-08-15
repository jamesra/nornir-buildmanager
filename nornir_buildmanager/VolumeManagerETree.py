
import copy
import datetime
import glob
import logging
import operator
import os
import pickle
import shutil
import sys
import urllib
import math

import VolumeManagerHelpers as VMH

import nornir_buildmanager.Config as Config
import nornir_buildmanager.operations.versions as versions
import nornir_buildmanager.operations.tile as tile
import nornir_imageregistration.transforms.registrationtree
from nornir_imageregistration.files import *
import nornir_shared.checksum
import nornir_shared.misc as misc
import nornir_shared.prettyoutput as prettyoutput
import nornir_shared.reflection as reflection
import xml.etree.ElementTree as ElementTree

 

    
# import
# Used for debugging with conditional break's, each node gets a temporary unique ID
nid = 0

__LoadedVolumeXMLDict__ = dict()


def ValidateAttributesAreStrings(Element, logger=None):

    # Make sure each attribute is a string
    for k, v in enumerate(Element.attrib):
        assert isinstance(v, str)
        if not isinstance(v, str):
            if logger is None:
                logger = logging.getLogger(__name__ + '.' + 'ValidateAttributesAreStrings')
            logger.warning("Attribute is not a string")
            Element.attrib[k] = str(v)


def NodePathKey(NodeA):
    '''Sort section nodes by number'''
    return NodeA.tag


def NodeCompare(NodeA, NodeB):
    '''Sort section nodes by number'''

    cmpVal = cmp(NodeA.tag, NodeB.tag)

    if cmpVal == 0:
        cmpVal = cmp(NodeA.attrib.get('Path', ''), NodeB.attrib.get('Path', ''))

    return cmpVal


class VolumeManager():
    def __init__(self, volumeData, filename):
        self.Data = volumeData
        self.XMLFilename = filename
        pass

    @classmethod
    def Create(cls, VolumePath):
        VolumeManager.Load(VolumePath, Create=True)


    @classmethod
    def Load(cls, VolumePath, Create=False, UseCache=True):
        '''Load the volume information for the specified directory or create one if it doesn't exist'''
        Filename = os.path.join(VolumePath, "VolumeData.xml")
        if not os.path.exists(Filename):
            prettyoutput.Log("Provided volume description file does not exist: " + Filename)

            OldVolume = os.path.join(VolumePath, "Volume.xml")
            if(os.path.exists(OldVolume)):
                VolumeRoot = ElementTree.parse(OldVolume).getroot()
                # Volumes.CreateFromDOM(XMLTree)
                VolumeRoot.attrib['Path'] = VolumePath
                cls.WrapElement(VolumeRoot)
                VolumeManager.__SetElementParent__(VolumeRoot)
                VolumeRoot.Save()


        if not os.path.exists(Filename):
            if(Create):
                if not os.path.exists(VolumePath):
                    os.makedirs(VolumePath)

                VolumeRoot = ElementTree.Element('Volume', {"Name" : os.path.basename(VolumePath), "Path" : VolumePath})
                # VM =  VolumeManager(VolumeData, Filename)
                # return VM
            else:
                return None
        else:
            # 5/16/2012 Loading these XML files is really slow, so they are cached.
            # if not __LoadedVolumeXMLDict__.get(Filename, None) is None and UseCache:
            #    XMLTree = __LoadedVolumeXMLDict__[Filename]
            # else:
            XMLTree = ElementTree.parse(Filename)
            # VolumeData = Volumes.CreateFromDOM(XMLTree)
            # __LoadedVolumeXMLDict__[Filename] = XMLTree

            VolumeRoot = XMLTree.getroot()

        VolumeRoot.attrib['Path'] = VolumePath
        VolumeRoot = XContainerElementWrapper.wrap(VolumeRoot)
        VolumeManager.__SetElementParent__(VolumeRoot)

        prettyoutput.Log("Volume Root: " + VolumeRoot.attrib['Path'])
        # VolumeManager.__RemoveElementsWithoutPath__(VolumeRoot)

        return VolumeRoot
        # return cls.__init__(VolumeData, Filename)

    @classmethod
    def WrapElement(cls, e):
        OverrideClassName = e.tag + 'Node'
        OverrideClass = reflection.get_module_class('nornir_buildmanager.VolumeManagerETree', OverrideClassName, LogErrIfNotFound=False)
        if not OverrideClass is None:
            OverrideClass.wrap(e)
        elif not (e.attrib.get("Path", None) is None):
            if os.path.isfile(e.attrib.get("Path")):
                XFileElementWrapper.wrap(e)
            else:
                XContainerElementWrapper.wrap(e)
        else:
            XElementWrapper.wrap(e)


    @classmethod
    def __SetElementParent__(cls, Element, ParentElement=None):
        Element.Parent = ParentElement

        for i in range(len(Element) - 1, -1, -1):
            e = Element[i]
            if e.tag in versions.DeprecatedNodes:
                del Element[i]

        for e in Element:
            if(isinstance(e, ElementTree.Element)):
                # Find out if we have an override class defined
                cls.WrapElement(e)

            VolumeManager.__SetElementParent__(e, Element)

        return

        return False

    @classmethod
    def __SortNodes__(cls, Element):
        '''Remove all elements that should be found on the file system but are not'''
        Element._children.sort(key=operator.attrgetter('SortKey'))

        for e in Element:
            cls.__SortNodes__(e)

    @classmethod
    def __RemoveEmptyElements__(cls, Element):
        '''Remove all elements that should be found on the file system but are not'''
        for i in range(len(Element) - 1, -1, -1):
            e = Element[i]
            if len(e.keys()) == 0 and len(list(e)) == 0:
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
        return nornir_shared.Checksum.DataChecksum(XMLString)


    @classmethod
    def Save(cls, VolumePath, VolumeObj):
        '''Save the volume to an XML file'''

        '''We cannot include the volume checksum in the calculation because including it changes the checksum'''
#        NewChecksum = cls.CalcVolumeChecksum(VolumeObj)
#        OldChecksum = VolumeObj.get('Checksum', '')
#
#        if OldChecksum == NewChecksum:
#            '''When the checksums match there is nothing new to save'''
#            return

        # cls.__RemoveElementsWithoutPath__(VolumeObj)
        # cls.__SortNodes__(VolumeObj)
        # cls.__RemoveEmptyElements__(VolumeObj)

        if hasattr(VolumeObj, 'Save'):
            VolumeObj.Save(tabLevel=None)
        else:
            cls.Save(VolumePath, VolumeObj.Parent)

#        #Make sure any changes are accounted for by calculating a new checksum
#        NewChecksum = cls.CalcVolumeChecksum(VolumeObj)
#        VolumeObj.attrib['Checksum'] = NewChecksum
#
#        TempXMLFilename = os.path.join(VolumePath, 'TempVolume.XML')
#
#        OutputXML = ElementTree.tostring(VolumeObj, encoding="utf-8")
#
#        hFile = open(TempXMLFilename, 'w')
#        hFile.write(OutputXML)
#        hFile.close()
#
#        XMLFilename = os.path.join(VolumePath, 'Volume.XML')
#
#        shutil.copy(TempXMLFilename, XMLFilename)
#        os.remove(TempXMLFilename)
#
#        #Update the cache we use to load volumes
#        __LoadedVolumeXMLDict__[XMLFilename] = ElementTree.ElementTree(VolumeObj)


class XPropertiesElementWrapper(ElementTree.Element):
    @property
    def SortKey(self):
        '''The default key used for sorting elements'''
        return self.tag

    @property
    def Name(self):
        return self.attrib.get("Name", None)

    @Name.setter
    def Name(self, value):
        self.attrib["Name"] = value

    @property
    def Parent(self):
        return self._Parent

    @Parent.setter
    def Parent(self, Value):
        self.__dict__['_Parent'] = Value
        if '__fullpath' in self.__dict__:
            del self.__dict__['__fullpath']

    '''Wrapper for a properties element'''
    def __init__(self, tag, attrib=None, **extra):
        super(XPropertiesElementWrapper, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def wrap(cls, dictElement):

        dictElement.__class__ = cls
        return dictElement


    def __getEntry__(self, key):
        prettyoutput.Log(key)
        iterator = self.find('Entry[@Name="' + str(key) + '"]')
        if iterator is None:
            return None

        return iterator


    def __getattr__(self, name):

        '''Called when an attribute lookup has not found the attribute in the usual places (i.e. it is not an instance attribute nor is it found in the class tree for self). name is the attribute name. This method should return the (computed) attribute value or raise an AttributeError exception.

        Note that if the attribute is found through the normal mechanism, __getattr__() is not called. (This is an intentional asymmetry between __getattr__() and __setattr__().) This is done both for efficiency reasons and because otherwise __getattr__() would have no way to access other attributes of the instance. Note that at least for instance variables, you can fake total control by not inserting any values in the instance attribute dictionary (but instead inserting them in another object). See the __getattribute__() method below for a way to actually get total control in new-style classes.'''
        if '_children' in self.__dict__:
            entry = self.__getEntry__(name)
            if(entry is not None):
                valstr = entry.attrib["Value"]
                valstr = str(urllib.unquote_plus(valstr))
                return pickle.loads(valstr)

        raise AttributeError(name)


    def __setattr__(self, name, value):


        '''Called when an attribute assignment is attempted. This is called instead of the normal mechanism (i.e. store the value in the instance dictionary). name is the attribute name, value is the value to be assigned to it.'''
        if(hasattr(self.__class__, name)):
            attribute = getattr(self.__class__, name)
            if(isinstance(attribute, property)):
                attribute.fset(self, value)
                return
            else:
                super(XPropertiesElementWrapper, self).__setattr__(name, value)
                return

        if(name in self.__dict__):
            self.__dict__[name] = value
        elif(name[0] == '_'):
            self.__dict__[name] = value
        else:
                valstr = pickle.dumps(value, 0)
                valstr = urllib.quote_plus(valstr)

                entry = self.__getEntry__(name)
                if entry is None:
                    entry = ElementTree.Element("Entry", {'Name':name, 'Value':valstr})
                    self.append(entry)
                else:
                    entry.attrib['Value'] = valstr

    def __delattr__(self, name):
        '''Like __setattr__() but for attribute deletion instead of assignment. This should only be implemented if del obj.name is meaningful for the object.'''
        if(name in self.__dict__):
            self.__dict__.pop(name)
        else:
            entry = self.find('Entry[@Name="' + str(name) + '"]')
            if not entry is None:
                self.remove(entry)


class XElementWrapper(ElementTree.Element):

    logger = logging.getLogger(__name__ + '.' + 'XElementWrapper')

    def sort(self):
        '''Order child elements'''
        self._children.sort(key=operator.attrgetter('SortKey'))

        for c in self._children:
            if len(c._children) <= 1:
                continue

            if isinstance(c, XElementWrapper):
                c.sort()

    @property
    def CreationTime(self):
        datestr = self.get('CreationDate', datetime.datetime.max)
        return datetime.datetime.strptime(datestr)

    @property
    def SortKey(self):
        '''The default key used for sorting elements'''
        return self.tag

    def __str__(self):
        strList = ElementTree.tostringlist(self)
        outStr = ""
        for s in strList:
            outStr = outStr + " " + s.decode('utf-8')
            if s == '>':
                break

        return outStr

    def _GetAttribFromParent(self, attribName):
        p = self._Parent
        while p is not None:
            if attribName in p.attrib:
                return p.attrib[attribName]

            if hasattr(p, '_Parent'):
                p = p._Parent
            else:
                return None

    @property
    def Checksum(self):
        return self.get('Checksum', "")

    @Checksum.setter
    def Checksum(self, Value):
        if not isinstance(Value, str):
            XElementWrapper.logger.warning('Setting non string value on XElement.Checksum, automatically corrected: ' + str(Value))
        self.attrib['Checksum'] = str(Value)
        return

    @property
    def Version(self):
        return float(self.attrib.get('Version', 1.0))

    @Version.setter
    def Version(self, Value):
        self.attrib['Version'] = str(Value)


    @property
    def Root(self):
        '''The root of the element tree'''
        node = self
        while not node.Parent is None:
            node = node.Parent

        return node


    @property
    def Parent(self):
        return self._Parent

    @Parent.setter
    def Parent(self, Value):
        self.__dict__['_Parent'] = Value
        if '__fullpath' in self.__dict__:
            del self.__dict__['__fullpath']

    @classmethod
    def __GetCreationTimeString__(cls):
        now = datetime.datetime.now()
        now = now.replace(microsecond=0)
        return str(now)

    def __init__(self, tag, attrib=None, **extra):

        global nid
        self.__dict__['id'] = nid
        nid = nid + 1

        if(attrib is None):
            attrib = {}
        else:
            StringAttrib = {}
            for k in attrib.keys():
                if not isinstance(attrib[k], str):
                    XElementWrapper.logger.info('Setting non string value on <' + str(tag) + '>, automatically corrected: ' + k + ' -> ' + str(attrib[k]))
                    StringAttrib[k] = str(attrib[k])
                else:
                    StringAttrib[k] = attrib[k]

            attrib = StringAttrib


        super(XElementWrapper, self).__init__(tag=tag, attrib=attrib, **extra)

        self._Parent = None

        if not self.tag.endswith("_Link"):
            self.attrib['CreationDate'] = XElementWrapper.__GetCreationTimeString__()
            self.Version = versions.GetLatestVersionForNodeType(tag)

    @classmethod
    def RemoveDuplicateElements(cls, tagName):
        '''For nodes that should not be duplicated this function removes all but the last created element'''
        pass


    def IsParent(self, node):
        '''Returns true if the node is a parent'''
        if self.Parent is None:
            return False

        if self.Parent == node:
            return True
        else:
            return self.Parent.IsParent(node)

    def IsValid(self):
        '''This function should be overridden by derrived classes.  It returns true if the file system or other external 
           resources match the state recorded within the element.
           
           Returns Tuple of state and a string with a reason'''

        if not 'Version' in self.attrib:
            if versions.GetLatestVersionForNodeType(self.tag) > 1.0:
                return [False, "Node version outdated"]

        if not versions.IsNodeVersionCompatible(self.tag, self.Version):
            return [False, "Node version outdated"]

        return [True, ""]


    def CleanIfInvalid(self):
        '''Remove the contents of this node if it is out of date, returns true if node was cleaned'''
        Valid = self.IsValid()

        if isinstance(Valid, bool):
            Valid = [Valid, ""]


        if not Valid[0]:

            self.Clean(Valid[1])

        return not Valid[0]


    def Clean(self, reason=None):
        '''Remove node from element tree and remove any external resources such as files'''

        DisplayStr = ' --- Cleaning ' + self.ToElementString() + ". "

        '''Remove the contents referred to by this node from the disk'''
        prettyoutput.Log(DisplayStr)
        if not reason is None:
            prettyoutput.Log("  --- " + reason)

        # Make sure we clean child elements if needed
        children = copy.copy(self._children)
        for child in children:
            if isinstance(child, XElementWrapper):
                child.Clean(reason="Parent was removed")

        if not self.Parent is None:
            try:
                self.Parent.remove(self)
            except:
                # Sometimes we have not been added to the parent at this point
                pass

    @classmethod
    def wrap(cls, dictElement):
        '''Change the class of an ElementTree.Element(PropertyElementName) to add our wrapper functions'''
        if(dictElement.__class__ == cls):
            return dictElement

        if(isinstance(dictElement, cls)):
            return dictElement

        dictElement.__class__ = cls

        if not 'CreationDate' in dictElement.attrib:
            cls.logger.info("Populating missing CreationDate attribute " + dictElement.ToElementString())
            dictElement.attrib['CreationDate'] = XElementWrapper.__GetCreationTimeString__()

        if(isinstance(dictElement, XContainerElementWrapper)):
            if(not 'Path' in dictElement.attrib):
                print dictElement.ToElementString() + " no path attribute but being set as container"
            assert('Path' in dictElement.attrib)

        return dictElement


    def ToElementString(self):
        strList = ElementTree.tostringlist(self)
        outStr = ""
        for s in strList:
            outStr = outStr + " " + s.decode('utf-8')
            if s == '>':
                break

        return outStr


    def __getattr__(self, name):

        '''Called when an attribute lookup has not found the attribute in the usual places (i.e. it is not an instance attribute nor is it found in the class tree for self). name is the attribute name. This method should return the (computed) attribute value or raise an AttributeError exception.

        Note that if the attribute is found through the normal mechanism, __getattr__() is not called. (This is an intentional asymmetry between __getattr__() and __setattr__().) This is done both for efficiency reasons and because otherwise __getattr__() would have no way to access other attributes of the instance. Note that at least for instance variables, you can fake total control by not inserting any values in the instance attribute dictionary (but instead inserting them in another object). See the __getattribute__() method below for a way to actually get total control in new-style classes.'''
        if(name in self.attrib):
            return self.attrib[name]

        if name in self.__dict__:
            return self.__dict__[name]

        superClass = super(XElementWrapper, self)
        if not superClass is None:
            if hasattr(superClass, '__getattr__'):
                return superClass.__getattr__(name)

        raise AttributeError(name)

    def __setattr__(self, name, value):

        '''Called when an attribute assignment is attempted. This is called instead of the normal mechanism (i.e. store the value in the instance dictionary). name is the attribute name, value is the value to be assigned to it.'''
        if(hasattr(self.__class__, name)):
            attribute = getattr(self.__class__, name)
            if isinstance(attribute, property):
                if not attribute.fset is None:
                    attribute.fset(self, value)
                    return
                else:
                    assert (not attribute.fset is None)  # Why are we trying to set a property without a setter?
            else:
                super(XElementWrapper, self).__setattr__(name, value)
                return

        if(name in self.__dict__):
            self.__dict__[name] = value
        elif(name[0] == '_'):
            self.__dict__[name] = value
        elif(self.attrib is not None):
            if not isinstance(value, str):
                XElementWrapper.logger.info('Setting non string value on <' + str(self.tag) + '>, automatically corrected: ' + name + ' -> ' + str(value))

                if isinstance(value, float):
                    self.attrib[name] = '%g' % value
                else:
                    self.attrib[name] = str(value)
            else:
                self.attrib[name] = value


    def __delattr__(self, name):

        '''Like __setattr__() but for attribute deletion instead of assignment. This should only be implemented if del obj.name is meaningful for the object.'''
        if(name in self.__dict__):
            self.__dict__.pop(name)
        elif(name in self.attrib):
            self.attrib.pop(name)


    def CompareAttributes(self, dictAttrib):
        '''Compare the passed dictionary with the attributes on the node, return entries which do not match'''
        mismatched = list()

        for entry, val in dictAttrib.items():
            if hasattr(self, entry):
                if getattr(self, entry) != val:
                    mismatched.append(entry)
            else:
                mismatched.append(entry[0])

        return mismatched


    def RemoveOldChildrenByAttrib(self, ElementName, AttribName, AttribValue):
        '''If multiple children match the criteria, we remove all but the child with the latest creation date'''
        Children = self.GetChildrenByAttrib(ElementName, AttribName, AttribValue)
        if Children is None:
            return

        if len(Children) < 2:
            return

        OldestChild = Children[0]
        for iChild in range(1, len(Children)):
            Child = Children[iChild]
            if not hasattr(Child, 'CreationDate'):
                self.remove(Child)
            else:
                if not hasattr(OldestChild, 'CreationDate'):
                    self.remove(OldestChild)
                    OldestChild = Child
                else:
                    if OldestChild.CreationDate < Child.CreationDate:
                        self.remove(OldestChild)
                        OldestChild = Child
                    else:
                        self.remove(Child)


    def GetChildrenByAttrib(self, ElementName, AttribName, AttribValue):
        XPathStr = "%(ElementName)s[@%(AttribName)s='%(AttribValue)s']" % {'ElementName' : ElementName, 'AttribName' : AttribName, 'AttribValue' : AttribValue}
        Children = self.findall(XPathStr)

        if Children is None:
            return []

        return list(Children)

    def GetChildByAttrib(self, ElementName, AttribName, AttribValue):

        XPathStr = ""
        if isinstance(AttribValue, float):
            XPathStr = "%(ElementName)s[@%(AttribName)s='%(AttribValue)g']" % {'ElementName' : ElementName, 'AttribName' : AttribName, 'AttribValue' : AttribValue}
        else:
            XPathStr = "%(ElementName)s[@%(AttribName)s='%(AttribValue)s']" % {'ElementName' : ElementName, 'AttribName' : AttribName, 'AttribValue' : AttribValue}

        assert(len(XPathStr) > 0)
        Child = self.find(XPathStr)
        # if(len(Children) > 1):
        #    prettyoutput.LogErr("Multiple nodes found fitting criteria: " + XPathStr)
        #    return Children

        # if len(Children) == 0:
        #    return None

        if Child is None:
            return None

        return Child

    def Contains(self, Element):
        for c in self:
            for k, v in c.attrib:
                if k == 'CreationDate':
                    continue
                if k in self.attrib:
                    if not v == self.attrib[k]:
                        return False
        return True

    def UpdateOrAddChildByAttrib(self, Element, AttribNames=None):
        if(AttribNames is None):
            AttribNames = ['Name']
        elif isinstance(AttribNames, str):
            AttribNames = [AttribNames]
        elif not isinstance(AttribNames, list):
            raise Exception("Unexpected attribute names for UpdateOrAddChildByAttrib")

        attribXPathTemplate = "@%(AttribName)s='%(AttribValue)s'"
        attribXPaths = []
        for AttribName in AttribNames:
            val = Element.attrib[AttribName]
            attribXPaths.append(attribXPathTemplate % {'AttribName' : AttribName,
                                                       'AttribValue' : val})

        XPathStr = "%(ElementName)s[%(QueryString)s]" % {'ElementName' : Element.tag,
                                                         'QueryString' : ' and '.join(attribXPaths)}
        return self.UpdateOrAddChild(Element, XPathStr)

    def UpdateOrAddChild(self, Element, XPath=None):
        '''Adds an element using the specified XPath.  If the XPath is unspecified the element name is used
           Returns a tuple with (True/False, Element).
           True indicates the element did not exist and was added.
           False indicates the element existed and the existing value is returned.
           '''

        if(XPath is None):
            XPath = Element.tag

        NewNodeCreated = False

        '''Eliminates duplicates if they are found'''
#        if self.Contains(Element):
#            return
        # MatchingChildren = list(self.findall(XPath))
        # if(len(MatchingChildren) > 1):
            # for i in range(1,len(MatchingChiElement.Parent = selfdren)):
                # self.remove(MatchingChildren[i])

        '''Returns the existing element if it exists, adds ChildElement with specified attributes if it does not exist.'''
        Child = self.find(XPath)
        if Child is None:
            if not Element is None:
                self.append(Element)
                assert(Element in self)
                Child = Element
                NewNodeCreated = True
            else:
                # No data provided to create the child element
                return None

        # Make sure the parent is set correctly
        VolumeManager.WrapElement(Child)
        Child.Parent = self

        return (NewNodeCreated, Child)

    def append(self, Child):
        assert(not self == Child)
        super(XElementWrapper, self).append(Child)
        Child.Parent = self
        assert(Child in self)


    def FindParent(self, ParentTag):
        '''Find parent with specified tag'''
        assert (not ParentTag is None)
        P = self._Parent
        while P is not None:
            if(P.tag == ParentTag):
                return P
            P = P._Parent
        return None

    def FindFromParent(self, xpath):
        '''Run find on xpath on each parent, return first hit'''
#        assert (not ParentTag is None)
        P = self._Parent
        while P is not None:
            result = P.find(xpath)
            if not result is None:
                return result

            P = P._Parent
        return None

    def ReplaceChildWithLink(self, child):
        if isinstance(child, XContainerElementWrapper):
            if not child in self:
                return

            LinkElement = XElementWrapper(child.tag + '_Link', attrib=child.attrib)
            # SaveElement.append(LinkElement)
            self.append(LinkElement)
            self.remove(child)



    # replacement for find function that loads subdirectory xml files
    def find(self, xpath):

        (UnlinkedElementsXPath, LinkedElementsXPath, RemainingXPath) = self.__ElementLinkNameFromXPath(xpath)

        matchiterator = super(XElementWrapper, self).iterfind(UnlinkedElementsXPath)
        for match in matchiterator:
            # Run in a loop because find returns the first match, if the first match is invalid look for another
#             NotValid = match.CleanIfInvalid()
#             if NotValid:
#                 continue
#
            if len(RemainingXPath) > 0:
                foundChild = match.find(RemainingXPath)


                # Continue searching links if we don't find a result on the loaded elements
                if not foundChild is None:
                    return foundChild
            else:
                return match

        if not isinstance(self, XContainerElementWrapper):
            return None

        SubContainersIterator = super(XElementWrapper, self).findall(LinkedElementsXPath)

        if SubContainersIterator is None:
            return None

        # Run in a loop because the match may not exist on the first element returned by find
        for SubContainer in SubContainersIterator:

            # Remove the linked element from ourselves, we are about to load it for real.
            self.remove(SubContainer)

            SubContainerPath = os.path.join(self.FullPath, SubContainer.attrib["Path"])

            # OK, open the subcontainer, add it to ourselves as an element and run the rest of the search
            SubContainerElement = self.LoadSubElement(SubContainerPath)
            if(SubContainerElement is None):
                continue

            if len(RemainingXPath) > 0:
                result = SubContainerElement.find(RemainingXPath)
                if not result is None:
                    return result
            else:
                return SubContainerElement

        return None


    def __ElementLinkNameFromXPath(self, xpath):
        # OK, check if we have a linked element to load.

        if '\\' in xpath:
            Logger = logging.getLogger(__name__ + '.' + '__ElementLinkNameFromXPath')
            Logger.warn("Backslash found in xpath query, is this intentional or should it be a forward slash?")
            Logger.warn("XPath: " + xpath)

        parts = xpath.split('/')
        UnlinkedElementsXPath = parts[0]
        SubContainerName = UnlinkedElementsXPath.split('[')[0]
        LinkedSubContainerName = SubContainerName + "_Link"
        LinkedElementsXPath = UnlinkedElementsXPath.replace(SubContainerName, LinkedSubContainerName, 1)
        RemainingXPath = xpath[len(UnlinkedElementsXPath) + 1:]
        return (UnlinkedElementsXPath, LinkedElementsXPath, RemainingXPath)


    def findall(self, match):

        (UnlinkedElementsXPath, LinkedElementsXPath, RemainingXPath) = self.__ElementLinkNameFromXPath(match)

        # TODO: Need to modify to only search one level at a time


 #       if not isinstance(self, XContainerElementWrapper):
 #           return
        # OK, check for linked elements that also meet the criteria

        LinkMatches = super(XElementWrapper, self).findall(LinkedElementsXPath)
        if LinkMatches is None:
            return  # matches

        for link in LinkMatches:
            self.remove(link)
            SubContainerPath = os.path.join(self.FullPath, link.attrib["Path"])

            SubContainerElement = self.LoadSubElement(SubContainerPath)
            if(SubContainerElement is None):
                continue

#            if len(RemainingXPath) > 0:
#                subContainerMatches = SubContainerElement.findall(RemainingXPath)
#                if subContainerMatches is not None:
#                    for m in subContainerMatches:
#                        yield m
#                    # matches.extend(subContainerMatches)
#            else:
#                yield SubContainerElement

            # print "Unloading " + str(SubContainerElement)
            # self.remove(SubContainerElement)
            # self.append(link)
                # matches.append(SubContainerElement)

        # return matches
        matches = super(XElementWrapper, self).findall(UnlinkedElementsXPath)

        for m in matches:
#             NotValid = m.CleanIfInvalid()
#             if NotValid:
#                 continue

            if len(RemainingXPath) > 0:
                subContainerMatches = m.findall(RemainingXPath)
                if subContainerMatches is not None:
                    for m in subContainerMatches:
                        yield m
            else:
                yield m

class XResourceElementWrapper(XElementWrapper):
    '''Wrapper for an XML element that refers to a file'''

    @property
    def Path(self):
        return self.attrib.get('Path', '')

    @Path.setter
    def Path(self, val):
        self.attrib['Path'] = val

        if hasattr(self, '__fullpath'):
            del self.__dict__['__fullpath']

    @property
    def FullPath(self):

        FullPathStr = self.__dict__.get('__fullpath', None)

        if FullPathStr is None:
            FullPathStr = self.Path

            if(not hasattr(self, '_Parent')):
                return FullPathStr

            IterElem = self.Parent

            while not IterElem is None:
                if hasattr(IterElem, 'FullPath'):
                    FullPathStr = os.path.join(IterElem.FullPath, FullPathStr)
                    IterElem = None
                    break

                elif hasattr(IterElem, '_Parent'):
                    IterElem = IterElem._Parent
                else:
                    raise Exception("FullPath could not be generated for resource")

            if os.path.isdir(FullPathStr):  # Don't create a directory for files
                if not os.path.exists(FullPathStr):
                    prettyoutput.Log("Creating missing directory for FullPath: " + FullPathStr)
                    os.makedirs(FullPathStr)

#            if not os.path.isdir(FullPathStr): #Don't create a directory for files
#                if not os.path.exists(FullPathStr):
#                    prettyoutput.Log("Creating missing directory for FullPath: " + FullPathStr)
#                    os.makedirs(FullPathStr)
#            else:
#                dirname = os.path.dirname(FullPathStr)
#                if not os.path.exists(dirname):
#                    prettyoutput.Log("Creating missing directory for FullPath: " + FullPathStr)
#                    os.makedirs(dirname)

            self.__dict__['__fullpath'] = FullPathStr

        return FullPathStr

    PropertyElementName = 'Properties'

    @property
    def Properties(self):
        PropertyNode = self.find(XContainerElementWrapper.PropertyElementName)
        if(PropertyNode is None):
            PropertyNode = XPropertiesElementWrapper.wrap(ElementTree.Element(XContainerElementWrapper.PropertyElementName))
            self.append(PropertyNode)
        else:
            XPropertiesElementWrapper.wrap(PropertyNode)

        assert(isinstance(PropertyNode, XPropertiesElementWrapper))

        return PropertyNode

    def ToElementString(self):
        outStr = self.FullPath
        return outStr

    def Clean(self, reason=None):
        '''Remove the contents referred to by this node from the disk'''
        if os.path.exists(self.FullPath):
            try:
                if os.path.isdir(self.FullPath):
                    shutil.rmtree(self.FullPath)
                else:
                    os.remove(self.FullPath)
            except:
                Logger = logging.getLogger(__name__ + '.' + 'Clean')
                Logger.warning('Could not delete cleaned directory: ' + self.FullPath)
                pass

        return super(XResourceElementWrapper, self).Clean(reason=reason)

class XFileElementWrapper(XResourceElementWrapper):
    '''Refers to a file generated by the pipeline'''

    @property
    def Name(self):

        if not 'Name' in self.attrib:
            return self._GetAttribFromParent('Name')

        return self.attrib['Name']

    @Name.setter
    def Name(self, value):
         self.attrib['Name'] = value


    @property
    def Type(self):
        if not 'Type' in self.attrib:
            return self._GetAttribFromParent('Type')

        return self.attrib['Type']

    @Type.setter
    def Type(self, value):
         self.attrib['Type'] = value

    @property
    def Path(self):
        return self.attrib.get('Path', '')

    @Path.setter
    def Path(self, val):
        self.attrib['Path'] = val
        directory = os.path.dirname(self.FullPath)

        if not os.path.exists(directory):
            os.makedirs(directory)

        if hasattr(self, '__fullpath'):
            del self.__dict__['__fullpath']
        return

    def IsValid(self):
        if not os.path.exists(self.FullPath):
            return [False, 'File does not exist']

        return super(XFileElementWrapper, self).IsValid()

    def __init__(self, tag, Path, attrib, **extra):
        super(XFileElementWrapper, self).__init__(tag=tag, attrib=attrib, **extra)
        self.attrib['Path'] = Path

    @property
    def Checksum(self):
        '''Checksum of the file resource when the node was last updated'''
        checksum = self.get('Checksum', None)
        if checksum is None:
            if os.path.exists(self.FullPath):
                checksum = nornir_shared.checksum.FileChecksum(self.FullPath)
                self.attrib['Checksum'] = str(checksum)

        return checksum

class XContainerElementWrapper(XResourceElementWrapper):

    @property
    def SortKey(self):
        '''The default key used for sorting elements'''

        tag = self.tag
        if tag.endswith("_Link"):
            tag = tag[:-len("_Link")]
            tag = tag + "Node"

            current_module = sys.modules[__name__]
            if hasattr(current_module, tag):
                tagClass = getattr(current_module, tag)
                # nornir_shared.Reflection.get_class(tag)

                if not tagClass is None:
                    if hasattr(tagClass, "ClassSortKey"):
                        return tagClass.ClassSortKey(self)

        return self.tag

    @property
    def Name(self):
        return self.get('Name', '')

    @Name.setter
    def Name(self, Value):
        self.attrib['Name'] = Value

    @property
    def Path(self):
        return self.attrib.get('Path', '')

    @Path.setter
    def Path(self, val):

        super(XContainerElementWrapper, self.__class__).Path.fset(self, val)

        if not os.path.isdir(self.FullPath):
            assert not os.path.isfile(self.FullPath)  # , "Container element path attribute refers to a file.  Remove any '.' from names if it is a directory")
            os.makedirs(self.FullPath)

        return

    def IsValid(self):
        ResourcePath = self.FullPath
        if not os.path.isdir(ResourcePath):
            return [False, 'Directory does not exist']

        if os.path.isdir(ResourcePath) and not self.Parent is None:
            if len(os.listdir(ResourcePath)) == 0:
                return [False, 'Directory is empty']

        return super(XContainerElementWrapper, self).IsValid()

    def UpdateSubElements(self):
        '''Recursively searches directories for VolumeData.xml files.
           Adds discovered nodes into the volume. 
           Removes missing nodes from the volume.'''

        dirNames = os.listdir(self.FullPath)
        dirList = []

        for dirname in dirNames:
            volumeDataFullPath = os.path.join(dirname, "VolumeData.xml")
            if os.path.exists(volumeDataFullPath):
                # Check to be sure that this is a new node
                existingChild = self.find("[@Path='" + os.path.basename(dirname) + "']")

                if not existingChild is None:
                    continue

                # Load the VolumeData.xml, take the root element name and create a link in our element
                self.LoadSubElement(volumeDataFullPath)

        for child in self:
            if hasattr(child, "UpdateSubElements"):
                child.UpdateSubElements()

        self.CleanIfInvalid()
        self.Save(recurse=False)

    def LoadSubElement(self, Path):
        logger = logging.getLogger(__name__ + '.' + 'LoadSubElement')
        Filename = os.path.join(Path, "VolumeData.xml")
        if not os.path.exists(Filename):
            logger.error(Filename + " does not exist")
            return None

        # print Filename
        try:
            XMLTree = ElementTree.parse(Filename)
        except Exception as e:
            logger.error("Parse error for " + Filename)
            logger.error(str(e))
            return None

        XMLElement = XMLTree.getroot()

        VolumeManager.WrapElement(XMLElement)
       # SubContainer = XContainerElementWrapper.wrap(XMLElement)

        VolumeManager.__SetElementParent__(XMLElement, self)

        self.append(XMLElement)

        NotValid = XMLElement.CleanIfInvalid()
        if NotValid:
            return None

        return XMLElement


    def __init__(self, tag, Name, Path=None, attrib=None, **extra):

        if Path is None:
            Path = Name

        if(attrib is None):
            attrib = {}

        super(XContainerElementWrapper, self).__init__(tag=tag, attrib=attrib, **extra)

        self.attrib['Name'] = Name
        self.attrib['Path'] = Path



    def Save(self, tabLevel=None, recurse=True):
        '''If recurse = False we only save this element, no child elements are saved'''
        
        if tabLevel is None:
            tabLevel = 0
            if hasattr(self, 'FullPath'):
                logger = logging.getLogger(__name__ + '.' + 'Save')
                logger.info("Saving " + self.FullPath)

        self.sort()

        # pool = Pools.GetGlobalThreadPool()
         
        #tabs = '\t' * tabLevel

        #if hasattr(self, 'FullPath'):
        #    logger.info("Saving " + self.FullPath)

        # logger.info('Saving ' + tabs + str(self))
        xmlfilename = 'VolumeData.xml'

        # Create a copy of ourselves for saving.  If this is not done we have the potential to change a collection during iteration
        # which would break the pipeline manager in subtle ways
        SaveElement = ElementTree.Element(self.tag, attrib=self.attrib)
        if(not self.text is None):
            SaveElement.text = self.text

        ValidateAttributesAreStrings(self)

        # SaveTree = ElementTree.ElementTree(SaveElement)

        # Any child containers we create a link to and remove from our file
        for i in range(len(self) - 1, -1, -1):
            child = self[i]
            if child.tag.endswith('_Link'):
                SaveElement.append(child)
                continue

            if isinstance(child, XContainerElementWrapper):
                LinkElement = XElementWrapper(child.tag + '_Link', attrib=child.attrib)
                # SaveElement.append(LinkElement)
                SaveElement.append(LinkElement)

                if(recurse):
                    child.Save(tabLevel + 1)

                # logger.warn("Unloading " + child.tag)
                # del self[i]
                # self.append(LinkElement)
            else:
                if isinstance(SaveElement, XElementWrapper):
                    ValidateAttributesAreStrings(SaveElement)
                    SaveElement.sort()

                SaveElement.append(child)

        self.__SaveXML(xmlfilename, SaveElement)
#        pool.add_task("Saving self.FullPath",   self.__SaveXML, xmlfilename, SaveElement)

        # If we are the root of all saves then make sure they have all completed before returning
        # if(tabLevel == 0 or recurse==False):
            # pool.wait_completion()


    def __SaveXML(self, xmlfilename, SaveElement):
        '''Intended to be called on a thread from the save function'''
        if not os.path.exists(self.FullPath):
            os.makedirs(self.FullPath)

        # prettyoutput.Log("Saving %s" % xmlfilename)

        TempXMLFilename = os.path.join(self.FullPath, 'Temp_' + xmlfilename)
        XMLFilename = os.path.join(self.FullPath, xmlfilename)

        # prettyoutput.Log("Saving %s" % XMLFilename)

        OutputXML = ElementTree.tostring(SaveElement, encoding="utf-8")
       # print OutputXML
        with open(TempXMLFilename, 'w') as hFile:
            hFile.write(OutputXML)
            hFile.close()

        shutil.copy(TempXMLFilename, XMLFilename)
        try:
            os.remove(TempXMLFilename)
        except:
            pass


class XLinkedContainerElementWrapper(XContainerElementWrapper):
    '''Child elements of XLinkedContainerElementWrapper are saved
       individually in subdirectories and replaced with an element
       postpended with the name "_link".  This greatly speeds Pythons
       glacially slow XML writing by limiting the amount of XML
       generated'''


    def Save(self, tabLevel=None, recurse=True):
        '''If recurse = False we only save this element, no child elements are saved'''

        if tabLevel is None:
            tabLevel = 0

        self.sort()

        # pool = Pools.GetGlobalThreadPool()

        logger = logging.getLogger(__name__ + '.' + 'XLinkedContainerElementWrapper')
        tabs = '\t' * tabLevel
        #logger.info('Saving ' + tabs + str(self))
        xmlfilename = 'VolumeData.xml'
        # Create a copy of ourselves for saving
        SaveElement = ElementTree.Element(self.tag, attrib=self.attrib)
        if(not self.text is None):
            SaveElement.text = self.text

        ValidateAttributesAreStrings(self, logger)

        # SaveTree = ElementTree.ElementTree(SaveElement)

        # Any child containers we create a link to and remove from our file
        for i in range(len(self) - 1, -1, -1):
            child = self[i]
            if child.tag.endswith('_Link'):
                SaveElement.append(child)
                continue

            if isinstance(child, XContainerElementWrapper):
                LinkElement = XElementWrapper(child.tag + '_Link', attrib=child.attrib)
                # SaveElement.append(LinkElement)
                SaveElement.append(LinkElement)

                if(recurse):
                    child.Save(tabLevel + 1)

                # logger.warn("Unloading " + child.tag)
                # del self[i]
                # self.append(LinkElement)
            else:
                if isinstance(SaveElement, XElementWrapper):
                    ValidateAttributesAreStrings(SaveElement, logger)
                    SaveElement.sort()

                SaveElement.append(child)

        self.__SaveXML(xmlfilename, SaveElement)
#        pool.add_task("Saving self.FullPath",   self.__SaveXML, xmlfilename, SaveElement)

        # If we are the root of all saves then make sure they have all completed before returning
        # if(tabLevel == 0 or recurse==False):
            # pool.wait_completion()


class BlockNode(XContainerElementWrapper):

    @property
    def Sections(self):
        return self.findall('Section')

    def GetSection(self, Number):
        return self.GetChildByAttrib('Section', 'Number', Number)

    def __init__(self, Name, Path=None, attrib=None, **extra):
        super(BlockNode, self).__init__(tag='Block', Name=Name, Path=Path, attrib=attrib, **extra)


class ChannelNode(XContainerElementWrapper):

    @property
    def Filters(self):
        return self.findall('Filter')

    def GetFilter(self, Filter):
        return self.GetChildByAttrib('Filter', 'Name', Filter)

    def GetOrCreateFilter(self, Name):
        (added, filterNode) = self.UpdateOrAddChildByAttrib(FilterNode(Name), 'Name')
        return filterNode

    def MatchFilterPattern(self, filterPattern):
        return VMH.SearchCollection(self.Filters,
                                   'Name',
                                    filterPattern)

    def GetTransform(self, transform_name):
        return self.GetChildByAttrib('Transform', 'Name', transform_name)

    def __init__(self, Name, Path, attrib=None, **extra):
        super(ChannelNode, self).__init__(tag='Channel', Name=Name, Path=Path, attrib=attrib, **extra)


class FilterNode(XContainerElementWrapper):

    DefaultMaskName = "Mask"

    @property
    def MaxIntensityCutoff(self):
        if 'MaxIntensityCutoff' in self.attrib:
            return float(self.attrib['MaxIntensityCutoff'])

        return None

    @property
    def MinIntensityCutoff(self):
        if 'MinIntensityCutoff' in self.attrib:
            return float(self.attrib['MinIntensityCutoff'])
        return None

    @property
    def Gamma(self):
        if 'Gamma' in self.attrib:
            return float(self.attrib['Gamma'])
        return None

    @property
    def BitsPerPixel(self):
        if 'BitsPerPixel' in self.attrib:
            return int(self.attrib['BitsPerPixel'])
        return None

    @BitsPerPixel.setter
    def BitsPerPixel(self, val):
        self.attrib['BitsPerPixel'] = '%d' % val

    @property
    def Locked(self):
        if 'Locked' in self.attrib:
            return bool(int(self.attrib['Locked']))

        return False;

    @Locked.setter
    def Locked(self, val):
        if not val:
            if 'Locked' in self.attrib:
                del self.attrib['Locked']
                return

        self.attrib['Locked'] = '%d' % val

    @property
    def TilePyramid(self):
        # pyramid = self.GetChildByAttrib('TilePyramid', "Name", TilePyramidNode.Name)
        # There should be only one Imageset, so use find
        pyramid = self.find('TilePyramid')
        if pyramid is None:
            pyramid = TilePyramidNode(NumberOfTiles=0)
            self.append(pyramid)

        return pyramid
    
    @property
    def HasImageset(self):
        return not self.find('Imageset') is None 

    @property
    def Imageset(self):
        '''Get the image set for the filter, create if missing'''
        # imageset = self.GetChildByAttrib('ImageSet', 'Name', ImageSetNode.Name)
        # There should be only one Imageset, so use find
        imageset = self.find('ImageSet')
        if imageset is None:
            imageset = ImageSetNode()
            self.append(imageset)

        return imageset

    @property
    def MaskName(self):
        '''The default mask to use for this filter'''
        m = self.attrib.get("MaskName", None)
        if not m is None:
            if len(m) == 0:
                m = None

        return m

    @MaskName.setter
    def MaskName(self, val):
        if val is None:
            if 'MaskName' in self.attrib:
                del self.attrib['MaskName']
        else:
            self.attrib['MaskName'] = val

    @property
    def DefaultMaskFilter(self):
        maskname = self.MaskName
        if maskname is None:
            maskname = FilterNode.DefaultMaskName

        return self.GetMaskFilter(maskname)

    def GetMaskFilter(self, MaskName):
        if MaskName is None:
            return None

        return self.Parent.GetChildByAttrib('Filter', 'Name', MaskName)

    def GetOrCreateMaskFilter(self, maskname=None):
        if maskname is None:
            maskname = FilterNode.DefaultMaskName

        assert(isinstance(maskname, str))

        return self.Parent.GetOrCreateFilter(maskname)

    def GetImage(self, Downsample):
        imageset = self.Imageset
        return imageset.GetImage(Downsample)

    def GetOrCreateImage(self, Downsample):
        imageset = self.Imageset
        return imageset.GetOrCreateImage(Downsample)

    def GetMaskImage(self, Downsample):
        maskFilter = self.DefaultMaskFilter
        if maskFilter is None:
            return None

        return maskFilter.GetImage(Downsample)

    def GetOrCreateMaskImage(self, Downsample):
        maskFilter = self.GetOrCreateMaskFilter()
        return maskFilter.GetOrCreateImage(Downsample)

    def GetHistogram(self):
        return self.find('Histogram')
        
        pass

    def __init__(self, Name, Path=None, attrib=None, **extra):
        if Path is None:
            Path = Name

        super(FilterNode, self).__init__(tag='Filter', Name=Name, Path=Path, attrib=attrib, **extra)


class NotesNode(XResourceElementWrapper):

    def __init__(self, Text=None, SourceFilename=None, attrib=None, **extra):

        super(NotesNode, self).__init__(tag='Notes', attrib=attrib, **extra)

        if not Text is None:
            self.text = Text

        if not SourceFilename is None:
            self.SourceFilename = SourceFilename
            self.Path = os.path.basename(SourceFilename)
        else:
            self.SourceFilename = ""
            self.Path = os.path.basename(SourceFilename)


    def CleanIfInvalid(self):
        return False


class SectionNode(XContainerElementWrapper):

    @classmethod
    def ClassSortKey(cls, self):
        '''Required for objects derived from XContainerElementWrapper'''
        return "Section " + (Config.Current.SectionTemplate % int(self.Number))

    @property
    def SortKey(self):
        '''The default key used for sorting elements'''
        return SectionNode.ClassSortKey(self)

    @property
    def Channels(self):
        return self.findall('Channel')

    @property
    def Number(self):
        return int(self.get('Number', '0'))

    @Number.setter
    def Number(self, Value):
        self.attrib['Number'] = str(int(Value))

    def GetChannel(self, Channel):
        return self.GetChildByAttrib('Channel', 'Name', Channel)

    def MatchChannelPattern(self, channelPattern):
        return VMH.SearchCollection(self.Channels,
                                             'Name',
                                             channelPattern)

    def MatchChannelFilterPattern(self, channelPattern, filterPattern):
        filterNodes = []
        for channelNode in self.MatchChannelPattern(channelPattern):
            result = channelNode.MatchFilterPattern(filterPattern)
            if not result is None:
                filterNodes.extend(result)

        return filterNodes


    def __init__(self, Number, Name=None, Path=None, attrib=None, **extra):

        if Name is None:
            Name = str(Number)

        if Path is None:
            Path = Config.Current.SectionTemplate % Number

        super(SectionNode, self).__init__(tag='Section', Name=Name, Path=Path, attrib=attrib, **extra)
        self.Number = Number


class StosGroupNode(XContainerElementWrapper):

    @property
    def Downsample(self):
        return float(self.attrib['Downsample'])

    @Downsample.setter
    def Downsample(self, val):
        '''The default key used for sorting elements'''
        self.attrib['Downsample'] = '%g' % val

    @property
    def SectionMappings(self):
        return list(self.findall('SectionMappings'))

    def GetSectionMapping(self, MappedSectionNumber):
        return self.GetChildByAttrib('SectionMappings', 'MappedSectionNumber', MappedSectionNumber)

    def GetOrCreateSectionMapping(self, MappedSectionNumber):
        (added, sectionMappings) = self.UpdateOrAddChildByAttrib(SectionMappingsNode(MappedSectionNumber=MappedSectionNumber), 'MappedSectionNumber')
        return sectionMappings

    def TransformsForMapping(self, MappedSectionNumber, ControlSectionNumber):
        sectionMapping = self.GetSectionMapping(MappedSectionNumber)
        if sectionMapping is None:
            return []

        return sectionMapping.TransformsToSection(ControlSectionNumber)


    def GetStosTransformNode(self, ControlFilter, MappedFilter):
        MappedSectionNode = MappedFilter.FindParent("Section")
        MappedChannelNode = MappedFilter.FindParent("Channel")
        ControlSectionNode = ControlFilter.FindParent("Section")
        ControlChannelNode = ControlFilter.FindParent("Channel")

        SectionMappingsNode = self.GetOrCreateSectionMapping(MappedSectionNode.Number)

        stosNode = SectionMappingsNode.FindStosTransform(ControlSectionNode.Number,
                                                               ControlChannelNode.Name,
                                                                ControlFilter.Name,
                                                                 MappedSectionNode.Number,
                                                                  MappedChannelNode.Name,
                                                                   MappedFilter.Name)

        return stosNode


    def CreateStosTransformNode(self, ControlFilter, MappedFilter, OutputType, OutputPath):

        stosNode = self.GetStosTransformNode(ControlFilter, MappedFilter)

        if stosNode is None:
            MappedSectionNode = MappedFilter.FindParent("Section")
            MappedChannelNode = MappedFilter.FindParent("Channel")
            ControlSectionNode = ControlFilter.FindParent("Section")
            ControlChannelNode = ControlFilter.FindParent("Channel")

            SectionMappingsNode = self.GetOrCreateSectionMapping(MappedSectionNode.Number)

            stosNode = TransformNode(str(ControlSectionNode.Number), OutputType, OutputPath, {'ControlSectionNumber' : str(ControlSectionNode.Number),
                                                                                             'MappedSectionNumber' : str(MappedSectionNode.Number),
                                                                                             'MappedChannelName' : str(MappedChannelNode.Name),
                                                                                             'MappedFilterName' : str(MappedFilter.Name),
                                                                                             'MappedImageChecksum' : str(MappedFilter.Imageset.Checksum),
                                                                                             'ControlChannelName' : str(ControlChannelNode.Name),
                                                                                             'ControlFilterName' : str(ControlFilter.Name),
                                                                                             'ControlImageChecksum' : str(ControlFilter.Imageset.Checksum)})

        #        WORKAROUND: The etree implementation has a serious shortcoming in that it cannot handle the 'and' operator in XPath queries.
        #        (added, stosNode) = SectionMappingsNode.UpdateOrAddChildByAttrib(stosNode, ['ControlSectionNumber',
        #                                                                                    'ControlChannelName',
        #                                                                                    'ControlFilterName',
        #                                                                                    'MappedSectionNumber',
        #                                                                                    'MappedChannelName',
        #                                                                                    'MappedFilterName'])


            SectionMappingsNode.append(stosNode)

        if not hasattr(stosNode, "ControlChannelName") or not hasattr(stosNode, "MappedChannelName"):

            MappedChannelNode = MappedFilter.FindParent("Channel")
            ControlChannelNode = ControlFilter.FindParent("Channel")

            renamedPath = os.path.join(os.path.dirname(stosNode.FullPath), stosNode.Path)
            XElementWrapper.logger.warn("Renaming stos transform for backwards compatability")
            XElementWrapper.logger.warn(renamedPath + " -> " + stosNode.FullPath)
            shutil.move(renamedPath, stosNode.FullPath)
            stosNode.Path = OutputPath
            stosNode.MappedChannelName = MappedChannelNode.Name
            stosNode.MappedFilterName = MappedFilter.Name
            stosNode.ControlChannelName = ControlChannelNode.Name
            stosNode.ControlFilterName = ControlFilter.Name
            stosNode.ControlImageChecksum = str(ControlFilter.Imageset.Checksum)
            stosNode.MappedImageChecksum = str(MappedFilter.Imageset.Checksum)

        return stosNode

    def __init__(self, Name, Downsample, attrib=None, **extra):

        super(StosGroupNode, self).__init__(tag='StosGroup', Name=Name, Path=Name, attrib=attrib, **extra)
        self.Downsample = Downsample


class StosMapNode(XElementWrapper):

    @property
    def CenterSection(self):
        if 'CenterSection' in self.attrib:
            val = self.attrib['CenterSection']
            if len(val) == 0:
                return None
            else:
                return int(val)

        return None

    @CenterSection.setter
    def CenterSection(self, val):
        if val is None:
            self.attrib['CenterSection'] = ""
        else:
            assert(isinstance(val, int))
            self.attrib['CenterSection'] = str(val)

    @property
    def Mappings(self):
        return list(self.findall('Mapping'))

    def MappedToControls(self):
        '''Return dictionary of possible control sections for a given mapped section number'''
        MappedToControlCandidateList = {}
        for mappingNode in self.Mappings:
            for mappedSection in mappingNode.Mapped:
                if mappedSection in MappedToControlCandidateList:
                    MappedToControlCandidateList[mappedSection].append(mappingNode.Control)
                else:
                    MappedToControlCandidateList[mappedSection] = [mappingNode.Control]

        return MappedToControlCandidateList

    def GetMappingsForControl(self, Control):
        mappings = self.findall("Mapping[@Control='" + str(Control) + "']")
        if mappings is None:
            return []

        return list(mappings)

    def ClearBannedControlMappings(self, NonStosSectionNumbers):
        '''Remove any control sections from a mapping which cannot be a control'''

        removed = False
        for InvalidControlSection in NonStosSectionNumbers:
            mapNodes = self.GetMappingsForControl(InvalidControlSection)
            for mapNode in mapNodes:
                removed = True
                self.remove(mapNode)

        return removed

    @property
    def AllowDuplicates(self):
        return self.attrib.get('AllowDuplicates', True)

    def AddMapping(self, Control, Mapped):
        '''Create a mapping to a control section'''

        val = None
        if isinstance(Mapped, nornir_imageregistration.transforms.registrationtree.RegistrationTreeNode):
            val = Mapped.SectionNumber
        elif isinstance(Mapped, int):
            val = Mapped
        else:
            raise TypeError("Mapped should be an int or RegistrationTreeNode")

        childMapping = self.GetChildByAttrib('Mapping', 'Control', Control)
        if childMapping is None:
            childMapping = MappingNode(Control, val)
            self.append(childMapping)
        else:
            if not val in childMapping.Mapped:
                childMapping.AddMapping(val)
        return

    def FindAllControlsForMapped(self, MappedSection):
        '''Given a section to be mapped, return the first control section found'''
        for m in self:
            if not m.tag == 'Mapping':
                continue

            if(MappedSection in m.Mapped):
                yield m.Control

        return

    def RemoveDuplicateControlEntries(self, Control):
        '''If there are two entries with the same control number we merge the mapping list and delete the duplicate'''

        mappings = list(self.GetMappingsForControl(Control))
        if len(mappings) < 2:
            return False

        mergeMapping = mappings[0]
        for i in range(1, len(mappings)):
            mappingNode = mappings[i]
            for mappedSection in mappingNode.Mapped:
                mergeMapping.AddMapping(mappedSection)

            self.remove(mappingNode)
            XElementWrapper.logger.warn('Moving duplicate mapping ' + str(Control) + ' <- ' + str(mappedSection))

        return True

    def IsValid(self):
        '''Check for mappings whose control section is in the non-stos section numbers list'''

        if not hasattr(self, 'Parent'):
            return super(StosMapNode, self).IsValid()

        NonStosSectionsNode = self.Parent.find('NonStosSectionNumbers')

        AlreadyMappedSections = []

        if NonStosSectionsNode is None:
            return super(StosMapNode, self).IsValid()

        NonStosSections = misc.ListFromAttribute(NonStosSectionsNode.text)

        MappingNodes = list(self.findall('Mapping'))

        for i in range(len(MappingNodes) - 1, -1, -1):
            Mapping = MappingNodes[i]
            self.RemoveDuplicateControlEntries(Mapping.Control)

        MappingNodes = list(self.findall('Mapping'))

        for i in range(len(MappingNodes) - 1, -1, -1):
            Mapping = MappingNodes[i]

            if Mapping.Control in NonStosSections:
                Mapping.Clean()
                XElementWrapper.logger.warn('Mappings for control section ' + str(Mapping.Control) + ' removed due to existence in NonStosSectionNumbers element')
            else:
                MappedSections = Mapping.Mapped
                for i in range(len(MappedSections) - 1, -1, -1):
                    MappedSection = MappedSections[i]
                    if MappedSection in AlreadyMappedSections and not self.AllowDuplicates:
                        del MappedSections[i]
                        XElementWrapper.logger.warn('Removing duplicate mapping ' + str(MappedSection) + ' -> ' + str(Mapping.Control))
                    else:
                        AlreadyMappedSections.append(MappedSection)

                if len(MappedSections) == 0:
                    Mapping.Clean()
                    XElementWrapper.logger.warn('No mappings remain for control section ' + str(Mapping.Control))
                elif len(MappedSections) != Mapping.Mapped:
                    Mapping.Mapped = MappedSections


        return super(StosMapNode, self).IsValid()

    def __init__(self, Name, attrib=None, **extra):

        super(StosMapNode, self).__init__(tag='StosMap', Name=Name, attrib=attrib, **extra)




class MappingNode(XElementWrapper):

    @property
    def SortKey(self):
        '''The default key used for sorting elements'''
        return self.tag + ' ' + (Config.Current.SectionTemplate % self.Control)

    @property
    def Control(self):
        return int(self.attrib['Control'])

    @property
    def Mapped(self):
        mappedList = misc.ListFromAttribute(self.get('Mapped', []))
        mappedList.sort()
        return mappedList

    @Mapped.setter
    def Mapped(self, value):
        AdjacentSectionString = ''
        if isinstance(value, list):
            value.sort()
            AdjacentSectionString = ','.join(str(x) for x in value)
        else:
            assert(isinstance(value, int))
            AdjacentSectionString = str(value)

        self.attrib['Mapped'] = AdjacentSectionString

    def AddMapping(self, value):
        intval = int(value)
        Mappings = self.Mapped
        if intval in Mappings:
            return
        else:
            Mappings.append(value)
            self.Mapped = Mappings

    def RemoveMapping(self, value):
        intval = int(value)
        Mappings = self.Mapped
        Mappings.remove(intval)
        self.Mapped = Mappings


    def __str__(self):
        return "%d <- %s" % (self.Control, str(Mapped))


    def __init__(self, ControlNumber, MappedNumbers, attrib=None, **extra):
        super(MappingNode, self).__init__(tag='Mapping', attrib=attrib, **extra)

        self.attrib['Control'] = str(ControlNumber)

        if not MappedNumbers is None:
            self.Mapped = MappedNumbers


class MosaicBaseNode(XFileElementWrapper):

    @classmethod
    def GetFilename(cls, Name, Type):
        Path = Name + Type + '.mosaic'
        return Path

    def _CalcChecksum(self):
        (file, ext) = os.path.splitext(self.Path)
        ext = ext.lower()

        if not os.path.exists(self.FullPath):
            return None

        if ext == '.stos':
            return stosfile.StosFile.LoadChecksum(self.FullPath)
        elif ext == '.mosaic':
            return mosaicfile.MosaicFile.LoadChecksum(self.FullPath)
        else:
            raise Exception("Cannot compute checksum for unknown transform type")

        return None

    def ResetChecksum(self):
        '''Recalculate the checksum for the element'''
        if 'Checksum' in self.attrib:
            del self.attrib['Checksum']

        self.attrib['Checksum'] = self._CalcChecksum()

    @property
    def Checksum(self):
        '''Checksum of the file resource when the node was last updated'''
        checksum = self.attrib.get('Checksum', None)
        if checksum is None:
            checksum = self._CalcChecksum()
            self.attrib['Checksum'] = checksum
            return checksum

        return checksum

    @Checksum.setter
    def Checksum(self, val):
        '''Checksum of the file resource when the node was last updated'''
        self.attrib['Checksum'] = val
        raise DeprecationWarning("Checksums for mosaic elements will not be directly settable soon.  Use ResetChecksum instead")

    def IsValid(self):
        
        result = super(MosaicBaseNode, self).IsValid()
        
        if result[0]:
            knownChecksum = self.attrib.get('Checksum', None)
            if not knownChecksum is None:
                fileChecksum = self._CalcChecksum()
    
                if not knownChecksum == fileChecksum:
                    return [False, "File checksum does not match meta-data"]

        return result 

    def __init__(self, tag, Name, Type, Path=None, attrib=None, **extra):

        if Path is None:
            Path = MosaicBaseNode.GetFilename(Name, Type)

        super(MosaicBaseNode, self).__init__(tag=tag, Path=Path, attrib=attrib, **extra)
        self.attrib['Name'] = Name
        self.attrib['Type'] = Type


    @property
    def InputTransformName(self):
        return self.get('InputTransformName', '')

    @InputTransformName.setter
    def InputTransformName(self, Value):
        self.attrib['InputTransformName'] = Value

    @property
    def InputImageDir(self):
        return self.get('InputTransform', '')

    @InputImageDir.setter
    def InputImageDir(self, Value):
        self.attrib['InputImageDir'] = Value

    @property
    def InputTransformChecksum(self):
        return self.get('InputTransformChecksum', '')

    @InputTransformChecksum.setter
    def InputTransformChecksum(self, Value):
        self.attrib['InputTransformChecksum'] = Value

    @property
    def Type(self):
        return self.attrib.get('Type', '')

    @Type.setter
    def Type(self, Value):
        self.attrib['Type'] = Value


class TransformNode(VMH.InputTransformHandler, MosaicBaseNode):

    def __init__(self, Name, Type, Path=None, attrib=None, **extra):
        super(TransformNode, self).__init__(tag='Transform', Name=Name, Type=Type, Path=Path, attrib=attrib, **extra)

    @property
    def CropBox(self):
        '''Returns boundaries of transform output if available, otherwise none
           :rtype tuple:
           :return (Xo, Yo, Width, Height):
        '''

        if 'CropBox' in self.attrib:
            return nornir_shared.misc.ListFromAttribute(self.attrib['CropBox'])
        else:
            return None

    def CropBoxDownsampled(self, downsample):
        (Xo, Yo, Width, Height) = self.CropBox
        Xo = Xo // float(downsample)
        Yo = Yo // float(downsample)
        Width = math.ceil(Width / float(downsample))
        Height = math.ceil(Height / float(downsample))

        return (Xo, Yo, Width, Height)

    @CropBox.setter
    def CropBox(self, bounds):
        '''Sets boundaries in fixed space for output from the transform.
        :param bounds tuple:  (Xo, Yo, Width, Height) or (Width, Height)
        '''
        if len(bounds) == 4:
            self.attrib['CropBox'] = "%g,%g,%g,%g" % bounds
        elif len(bounds) == 2:
            self.attrib['CropBox'] = "0,0,%g,%g" % bounds
        elif bounds is None:
            if 'CropBox' in self.attrib:
                del self.attrib['CropBox']
        else:
            raise Exception("Invalid argument passed to TransformNode.CropBox %s.  Expected 2 or 4 element tuple." % str(bounds))



    def IsValid(self):
        valid = VMH.InputTransformHandler.InputTransformIsValid(self)
        if valid:
            return super(TransformNode, self).IsValid()





class ImageSetBaseNode(VMH.InputTransformHandler, VMH.PyramidLevelHandler, XContainerElementWrapper):

    def __init__(self, Name, Type, Path, attrib=None, **extra):
        super(ImageSetBaseNode, self).__init__(tag='ImageSet', Name=Name, Path=Path, attrib=attrib, **extra)
        self.attrib['Name'] = Name
        self.attrib['Type'] = Type
        self.attrib['Path'] = Path

    def GetImage(self, Downsample):
        '''Returns image node for the specified downsample or None'''

        levelNode = self.GetLevel(Downsample)
        if levelNode is None:
            return None

        image = levelNode.find('Image')
        if not os.path.exists(image.FullPath):
            if image in self:
                self.remove(image)

            return None

        return image

    def GetOrCreateImage(self, Downsample, Path=None, GenerateData=True):
        '''Returns image node for the specified downsample. Generates image if requested and image is missing.  If unable to generate an image node is returned'''
        LevelNode = self.GetOrCreateLevel(Downsample, GenerateData=False)

        imageNode = LevelNode.find("Image")
        if imageNode is None:
            if Path is None:
                Path = ImageNode.DefaultName

            imageNode = ImageNode(Path)
            [added, imageNode] = LevelNode.UpdateOrAddChild(imageNode)
            if not os.path.exists(imageNode.FullPath):
                if not os.path.exists(os.path.dirname(imageNode.FullPath)):
                    os.makedirs(os.path.dirname(imageNode.FullPath))

                if GenerateData:
                    self.__GenerateMissingImageLevel(OutputImage=imageNode, Downsample=Downsample)

        return imageNode

    def __GetImageNearestToLevel(self, Downsample):
        '''Returns the nearest existing image and downsample level lower than the requested downsample level'''

        SourceImage = None
        SourceDownsample = Downsample / 2
        while SourceDownsample > 0:
            SourceImage = self.GetImage(SourceDownsample)
            if not SourceImage is None:

                # Only return images that actually are on disk
                if os.path.exists(SourceImage.FullPath):
                    break
                else:
                    # Probably a bad node, remove it
                    self.CleanIfInvalid()

            SourceDownsample = SourceDownsample / 2.0

        return (SourceImage, SourceDownsample)

    def GenerateLevels(self, Levels):
        tile.BuildImagePyramid(self, Levels, Interlace=False)

    def __GenerateMissingImageLevel(self, OutputImage, Downsample):
        '''Creates a downsampled image from available high-res images if needed'''

        (SourceImage, SourceDownsample) = self.__GetImageNearestToLevel(Downsample)

        if SourceImage is None:
            # raise Exception("No source image available to generate missing downsample level: " + OutputImage)
            return None

        OutputImage.Path = SourceImage.Path
        if 'InputImageChecksum' in SourceImage.attrib:
            OutputImage.InputImageChecksum = SourceImage.InputImageChecksum

        ShrinkP = nornir_shared.images.Shrink(SourceImage.FullPath, OutputImage.FullPath, float(Downsample) / float(SourceDownsample))
        ShrinkP.wait()

        return OutputImage

    def IsValid(self):
        valid = VMH.InputTransformHandler.InputTransformIsValid(self)
        if valid:
            return super(ImageSetBaseNode, self).IsValid()


class ImageSetNode(ImageSetBaseNode):

    DefaultName = 'Images'
    DefaultPath = 'Images'

    def __init__(self, Type=None, attrib=None, **extra):

        if Type is None:
            Type = ""

        # if Path is None:
        #    Path = ImageSetNode.Name + Type

        super(ImageSetNode, self).__init__(Name=ImageSetNode.DefaultName, Type=Type, Path=ImageSetNode.DefaultPath, attrib=attrib, **extra)


class ImageNode(VMH.InputTransformHandler, XFileElementWrapper):

    DefaultName = "image.png"

    def __init__(self, Path, attrib=None, **extra):

        super(ImageNode, self).__init__(tag='Image', Path=Path, attrib=attrib, **extra)


    def IsValid(self):
        if not os.path.exists(self.FullPath):
            return [False, 'File does not exist']

        if(self.Checksum != nornir_shared.checksum.FilesizeChecksum(self.FullPath)):
            return [False, "Checksum mismatch"]

        return super(ImageNode, self).IsValid()


    @property
    def Checksum(self):
        checksum = self.get('Checksum', None)
        if checksum is None:
            checksum = nornir_shared.checksum.FilesizeChecksum(self.FullPath)
            self.attrib['Checksum'] = str(checksum)

        return checksum


class DataNode(XFileElementWrapper):
    '''Refers to an external file containing data'''
    def __init__(self, Path, attrib=None, **extra):

        super(DataNode, self).__init__(tag='Data', Path=Path, attrib=attrib, **extra)


class SectionMappingsNode(XElementWrapper):

    @property
    def SortKey(self):
        '''The default key used for sorting elements'''
        return self.tag + ' ' + (Config.Current.SectionTemplate % self.MappedSectionNumber)

    @property
    def MappedSectionNumber(self):
        return int(self.attrib['MappedSectionNumber'])

    @property
    def Transforms(self):
        return list(self.findall('Transform'))

    @property
    def Images(self):
        return list(self.findall('Image'))

    def TransformsToSection(self, sectionNumber):
        return self.GetChildrenByAttrib('Transform', 'ControlSectionNumber', sectionNumber)

    def FindStosTransform(self, ControlSectionNumber, ControlChannelName, ControlFilterName, MappedSectionNumber, MappedChannelName, MappedFilterName):
        '''WORKAROUND: The etree implementation has a serious shortcoming in that it cannot handle the 'and' operator in XPath queries.  This function is a workaround for a multiple criteria find query'''
        for t in self.Transforms:
            if int(t.ControlSectionNumber) != ControlSectionNumber:
                continue

            if t.ControlChannelName != ControlChannelName:
                continue

            if t.ControlFilterName != ControlFilterName:
                continue

            if int(t.MappedSectionNumber) != MappedSectionNumber:
                continue

            if t.MappedChannelName != MappedChannelName:
                continue

            if t.MappedFilterName != MappedFilterName:
                continue

            return t

        return None

    @classmethod
    def _CheckForFilterExistence(self, block, section_number, channel_name, filter_name):

        section_node = block.GetSection(section_number)
        if section_node is None:
            return (False, "Transform section not found %d.%s.%s" % (section_number, channel_name, filter_name))

        channel_node = section_node.GetChannel(channel_name)
        if channel_node is None:
            return (False, "Transform channel not found %d.%s.%s" % (section_number, channel_name, filter_name))

        filter_node = channel_node.GetFilter(filter_name)
        if filter_node is None:
            return (False, "Transform filter not found %d.%s.%s" % (section_number, channel_name, filter_name))

        return (True, None)


    def CleanIfInvalid(self):
        self.CleanTransformsIfInvalid(self)
        XElementWrapper.CleanIfInvalid(self)


    def CleanTransformsIfInvalid(self):
        block = self.FindParent('Block')

        # Check the transforms and make sure the input data still exists
        for t in self.Transforms:
            ControlResult = SectionMappingsNode._CheckForFilterExistence(block, t.ControlSectionNumber, t.ControlChannelName, t.ControlFilterName)
            if ControlResult[0] == False:
                prettyoutput.Log("Cleaning transform " + t.Path + " control input did not exist: " + ControlResult[1])
                t.Clean()
                continue

            MappedResult = SectionMappingsNode._CheckForFilterExistence(block, t.MappedSectionNumber, t.MappedChannelName, t.MappedFilterName)
            if MappedResult[0] == False:
                prettyoutput.Log("Cleaning transform " + t.Path + " mapped input did not exist: " + MappedResult[1])
                t.Clean()
                continue


    def __init__(self, MappedSectionNumber=None, attrib=None, **extra):
        super(SectionMappingsNode, self).__init__(tag='SectionMappings', attrib=attrib, **extra)

        if not MappedSectionNumber is None:
            self.attrib['MappedSectionNumber'] = str(MappedSectionNumber)


class TilePyramidNode(XContainerElementWrapper, VMH.PyramidLevelHandler):

    DefaultName = 'TilePyramid'
    DefaultPath = 'TilePyramid'

    @property
    def LevelFormat(self):
        return self.attrib['LevelFormat']

    @LevelFormat.setter
    def LevelFormat(self, val):
        assert(isinstance(val, str))
        self.attrib['LevelFormat'] = val

    @property
    def NumberOfTiles(self):
        return int(self.attrib.get('NumberOfTiles', 0))

    @NumberOfTiles.setter
    def NumberOfTiles(self, val):
        self.attrib['NumberOfTiles'] = '%d' % val

    @property
    def ImageFormatExt(self):
        return self.attrib['ImageFormatExt']

    @ImageFormatExt.setter
    def ImageFormatExt(self, val):
        assert(isinstance(val, str))
        self.attrib['ImageFormatExt'] = val


    def __init__(self, NumberOfTiles=0, LevelFormat=None, ImageFormatExt=None, attrib=None, **extra):

        if LevelFormat is None:
            LevelFormat = Config.Current.LevelFormat

        if ImageFormatExt is None:
            ImageFormatExt = '.png'

        super(TilePyramidNode, self).__init__(tag='TilePyramid',
                                               Name=TilePyramidNode.DefaultName,
                                               Path=TilePyramidNode.DefaultPath,
                                               attrib=attrib, **extra)

        self.attrib['NumberOfTiles'] = str(NumberOfTiles)
        self.attrib['LevelFormat'] = LevelFormat
        self.attrib['ImageFormatExt'] = ImageFormatExt

    def GenerateLevels(self, Levels):
        tile.BuildTilePyramids(self, Levels)

class TilesetNode(XContainerElementWrapper, VMH.PyramidLevelHandler):

    DefaultName = 'Tileset'
    DefaultPath = 'Tileset'

    @property
    def FilePrefix(self):
        return self.attrib['FilePrefix']

    @FilePrefix.setter
    def FilePrefix(self, val):
        self.attrib['FilePrefix'] = val

    @property
    def FilePostfix(self):
        return self.attrib['FilePostfix']

    @FilePostfix.setter
    def FilePostfix(self, val):
        self.attrib['FilePostfix'] = val

    @property
    def TileXDim(self):
        return int(self.attrib['TileXDim'])

    @TileXDim.setter
    def TileXDim(self, val):
        self.attrib['TileXDim'] = '%d' % int(val)

    @property
    def TileYDim(self):
        return int(self.attrib['TileYDim'])

    @TileYDim.setter
    def TileYDim(self, val):
        self.attrib['TileYDim'] = '%d' % int(val)

    def __init__(self, attrib=None, **extra):
        super(TilesetNode, self).__init__(tag='Tileset', Name=TilesetNode.DefaultName, attrib=attrib, **extra)

        self.Name = TilesetNode.DefaultName
        if(not 'Path' in self.attrib):
            self.attrib['Path'] = TilesetNode.DefaultPath

    def GenerateLevels(self, Levels):
        tile.BuildTilesetPyramid(self)


class LevelNode(XContainerElementWrapper):

    @classmethod
    def ClassSortKey(cls, self):
        '''Required for objects derived from XContainerElementWrapper'''
        return "Level" + ' ' + Config.Current.DownsampleFormat % float(self.Downsample)

    @property
    def SortKey(self):
        '''The default key used for sorting elements'''
        return LevelNode.ClassSortKey(self)

    @property
    def Name(self):
        return '%g' % self.Downsample

    @Name.setter
    def Name(self, Value):
        assert False  # , "Attempting to set name on LevelNode")

    @property
    def Downsample(self):
        assert('Downsample' in self.attrib)
        return float(self.attrib.get('Downsample', ''))

    @Downsample.setter
    def Downsample(self, Value):
        self.attrib['Downsample'] = '%g' % Value

    def IsValid(self):
        '''Remove level directories without files, or with more files than they should have'''

        PyramidNode = self.Parent

        if not os.path.exists(self.FullPath):
            return [False, "Path does not exist"]

        if(isinstance(PyramidNode, TilePyramidNode)):
            globfullpath = os.path.join(self.FullPath, '*' + PyramidNode.ImageFormatExt)

            files = glob.glob(globfullpath)

            if(len(files) == 0):
                return [False, "No files in level"]

            FileNumberMatch = len(files) <= PyramidNode.NumberOfTiles

            if not FileNumberMatch:
                return [False, "File count mismatch for level"]
        elif(isinstance(PyramidNode, TilesetNode)):
            # Make sure each level has at least one tile from the last column on the disk.
            FilePrefix = PyramidNode.FilePrefix
            FilePostfix = PyramidNode.FilePostfix
            GridXDim = int(self.GridDimX) - 1
            GridYDim = int(self.GridDimY) - 1

            GridXString = Config.Current.GridTileCoordTemplate % GridXDim
            # MatchString = os.path.join(OutputDir, FilePrefix + 'X%' + Config.GridTileCoordFormat % GridXDim + '_Y*' + FilePostfix)
            MatchString = os.path.join(self.FullPath, Config.Current.GridTileMatchStringTemplate % {'prefix' :FilePrefix,
                                                                                        'X' :  GridXString,
                                                                                        'Y' : '*',
                                                                                        'postfix' :  FilePostfix})

            # Start with the middle because it is more likely to have a match earlier
            TestIndicies = range(GridYDim / 2, GridYDim)
            TestIndicies.extend(range((GridYDim / 2) + 1, -1, -1))
            for iY in TestIndicies:
                # MatchString = os.path.join(OutputDir, FilePrefix +
                #                           'X' + Config.GridTileCoordFormat % GridXDim +
                #                           '_Y' + Config.GridTileCoordFormat % iY +
                #                           FilePostfix)
                MatchString = os.path.join(self.FullPath, Config.Current.GridTileMatchStringTemplate % {'prefix' :  FilePrefix,
                                                                                        'X' : GridXString,
                                                                                        'Y' : Config.Current.GridTileCoordTemplate % iY,
                                                                                        'postfix' : FilePostfix})
                if(os.path.exists(MatchString)):
                    return [True, "Last column found"]

                MatchString = os.path.join(self.FullPath, Config.Current.GridTileMatchStringTemplate % {'prefix' :  FilePrefix,
                                                                                        'X' : str(GridXDim),
                                                                                        'Y' : str(iY),
                                                                                        'postfix' : FilePostfix})
                if(os.path.exists(MatchString)):
                    return [True, "Last column found"]



            return [False, "Last column of tileset not found"]

        return super(LevelNode, self).IsValid()


    def __init__(self, Level, attrib=None, **extra):

        if(attrib is None):
            attrib = {}

        attrib['Path'] = Config.Current.LevelFormat % int(Level)

        if isinstance(Level, str):
            attrib['Downsample'] = Level
        else:
            attrib['Downsample'] = '%g' % Level

        super(XContainerElementWrapper, self).__init__(tag='Level', attrib=attrib, **extra)


class HistogramBase(XElementWrapper):

    @property
    def DataNode(self):
        return self.find('Data')

    @property
    def ImageNode(self):
        return self.find('Image')

    @property
    def DataFullPath(self):
        if self.DataNode is None:
            return ""

        return self.DataNode.FullPath

    @property
    def ImageFullPath(self):
        if self.ImageNode is None:
            return ""

        return self.ImageNode.FullPath

    @property
    def Checksum(self):
        if(self.DataNode is None):
            return ""
        else:
            return self.DataNode.Checksum

    def IsValid(self):
        '''Remove this node if our output does not exist'''
        if self.DataNode is None:
            return [False, "No data node found"]
        else:
            if not os.path.exists(self.DataNode.FullPath):
                return [False, "No file to match data node"]

        '''Check for the transform node and ensure the checksums match'''
        # TransformNode = self.Parent.find('Transform')

        return True

    def __init__(self, tag, attrib, **extra):
        super(HistogramBase, self).__init__(tag=tag, attrib=attrib, **extra)


class AutoLevelHintNode(XElementWrapper):

    @property
    def UserRequestedMinIntensityCutoff(self):
        '''Returns None or a float'''
        val = self.attrib.get('UserRequestedMinIntensityCutoff', None)
        if val is None or  len(val) == 0:
            return None
        return float(val)

    @UserRequestedMinIntensityCutoff.setter
    def UserRequestedMinIntensityCutoff(self, val):
        if val is None:
            self.attrib['UserRequestedMinIntensityCutoff'] = ""
        else:
            if math.isnan(val):
                self.attrib['UserRequestedMinIntensityCutoff'] = ""
            else:
                self.attrib['UserRequestedMinIntensityCutoff'] = "%g" % val

    @property
    def UserRequestedMaxIntensityCutoff(self):
        '''Returns None or a float'''
        val = self.attrib.get('UserRequestedMaxIntensityCutoff', None)
        if val is None or len(val) == 0:
            return None
        return float(val)

    @UserRequestedMaxIntensityCutoff.setter
    def UserRequestedMaxIntensityCutoff(self, val):
        if val is None:
            self.attrib['UserRequestedMaxIntensityCutoff'] = ""
        else:
            if math.isnan(val):
                self.attrib['UserRequestedMaxIntensityCutoff'] = ""
            else:
                self.attrib['UserRequestedMaxIntensityCutoff'] = "%g" % val

    @property
    def UserRequestedGamma(self):
        '''Returns None or a float'''
        val = self.attrib.get('UserRequestedGamma', None)
        if val is None or len(val) == 0:
            return None
        return float(val)

    @UserRequestedGamma.setter
    def UserRequestedGamma(self, val):
        if val is None:
            self.attrib['UserRequestedGamma'] = ""
        else:
            if math.isnan(val):
                self.attrib['UserRequestedGamma'] = ""
            else:
                self.attrib['UserRequestedGamma'] = "%g" % val

    def __init__(self, MinIntensityCutoff=None, MaxIntensityCutoff=None):
        attrib = {'UserRequestedMinIntensityCutoff' : "",
                  'UserRequestedMaxIntensityCutoff' : "",
                  'UserRequestedGamma' : ""}
        super(AutoLevelHintNode, self).__init__(tag='AutoLevelHint', attrib=attrib)


class HistogramNode(HistogramBase):

    def __init__(self, InputTransformNode, Type, attrib, **extra):
        super(HistogramNode, self).__init__(tag='Histogram', attrib=attrib, **extra)
        self.attrib['InputTransformType'] = InputTransformNode.Type
        self.attrib['InputTransformChecksum'] = InputTransformNode.Checksum
        self.attrib['Type'] = Type

    def GetAutoLevelHint(self):
        return self.find('AutoLevelHint')

    def GetOrCreateAutoLevelHint(self):
        if not self.GetAutoLevelHint() is None:
            return self.GetAutoLevelHint()
        else:
            # Create a new AutoLevelData node using the calculated values as overrides so users can find and edit it later
            [added, AutoLevelDataNode] = self.UpdateOrAddChild(AutoLevelHintNode())
            return self.GetAutoLevelHint()

class PruneNode(HistogramBase):

    @property
    def Overlap(self):
        return float(self.attrib['Overlap'])

    @property
    def UserRequestedCutoff(self):
        val = self.attrib.get('UserRequestedCutoff', None)
        if isinstance(val, str):
            if len(val) == 0:
                return None

        if not val is None:
            val = float(val)

        return val

    @UserRequestedCutoff.setter
    def UserRequestedCutoff(self, val):
        if val is None:
            val = ""

        self.attrib['UserRequestedCutoff'] = str(val)

    def __init__(self, Type, Overlap, attrib=None, **extra):
        super(PruneNode, self).__init__(tag='Prune', attrib=attrib, **extra)
        self.attrib['Type'] = Type
        self.attrib['Overlap'] = str(Overlap)

        if not 'UserRequestedCutoff' in self.attrib:
            self.attrib['UserRequestedCutoff'] = ""

if __name__ == '__main__':
    VolumeManager.Load("C:\Temp")

    tagdict = XPropertiesElementWrapper.wrap(ElementTree.Element("Tag"))
    tagdict.V = 5
    tagdict.Path = "path"

    tagdict.Value = 34.56

    tagdict.Value = 43.7

    print(ElementTree.tostring(tagdict))

    del tagdict.Value
    del tagdict.Path

    print(ElementTree.tostring(tagdict))


    tagdict = XElementWrapper.wrap(ElementTree.Element("Tag"))
    tagdict.V = 5
    tagdict.Path = "path"

    tagdict.Value = 34.56

    tagdict.Value = 43.7

    print(ElementTree.tostring(tagdict))

    del tagdict.Value
    del tagdict.Path

    print(ElementTree.tostring(tagdict))

    tagdict = XContainerElementWrapper.wrap(ElementTree.Element("Tag"))
    tagdict.V = 5
    tagdict.Path = "path"

    tagdict.Value = 34.56

    tagdict.Value = 43.7

    tagdict.Properties.Path = "Path2"
    tagdict.Properties.Value = 75
    tagdict.Properties.Value = 57

    print(ElementTree.tostring(tagdict))

    del tagdict.Value
    del tagdict.Path

    del tagdict.Properties.Value
    del tagdict.Properties.Path

    print(ElementTree.tostring(tagdict))



