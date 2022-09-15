from __future__ import annotations
import abc
import datetime
import logging
import math
import operator
import os
import shutil
import sys

import nornir_buildmanager 
from nornir_imageregistration.files import *
import nornir_imageregistration.transforms.registrationtree
import nornir_shared.checksum
import nornir_shared.files
# import nornir_pools

import nornir_buildmanager.VolumeManagerHelpers as VMH
import nornir_buildmanager.operations.tile as tile
import nornir_buildmanager.operations.versions as versions
import nornir_shared.misc as misc
import nornir_shared.prettyoutput as prettyoutput
import nornir_shared.reflection as reflection
import xml.etree.ElementTree as ElementTree
import concurrent.futures

# Used for debugging with conditional break's, each node gets a temporary unique ID
nid = 0

__LoadedVolumeXMLDict__ = dict()


def ValidateAttributesAreStrings(Element, logger=None):
    # Make sure each attribute is a string
    for k, v in enumerate(Element.attrib):
        assert isinstance(v, str)
        if v is None or not isinstance(v, str):
            if logger is None:
                logger = logging.getLogger(__name__ + '.' + 'ValidateAttributesAreStrings')
            logger.warning("Attribute is not a string")
            Element.attrib[k] = str(v)


def NodePathKey(NodeA):
    """Sort section nodes by number"""
    return NodeA.tag


def NodeCompare(NodeA, NodeB):
    """Sort section nodes by number"""

    cmpVal = cmp(NodeA.tag, NodeB.tag)

    if cmpVal == 0:
        cmpVal = cmp(NodeA.attrib.get('Path', ''), NodeB.attrib.get('Path', ''))

    return cmpVal


class VolumeManager:

    def __init__(self, volumeData, filename):
        self.Data = volumeData
        self.XMLFilename = filename
        return

    @classmethod
    def Create(cls, VolumePath):
        VolumeManager.Load(VolumePath, Create=True)

    @classmethod
    def Load(cls, VolumePath, Create=False, UseCache=True):
        """Load the volume information for the specified directory or create one if it doesn't exist"""
        Filename = os.path.join(VolumePath, "VolumeData.xml")
        if not os.path.exists(Filename):
            prettyoutput.Log("Provided volume description file does not exist: " + Filename)

            OldVolume = os.path.join(VolumePath, "Volume.xml")
            if os.path.exists(OldVolume):
                VolumeRoot = ElementTree.parse(OldVolume).getroot()
                # Volumes.CreateFromDOM(XMLTree)
                VolumeRoot.attrib['Path'] = VolumePath
                (wrapped, VolumeRoot) = cls.WrapElement(VolumeRoot)
                VolumeManager.__SetElementParent__(VolumeRoot)
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
        VolumeRoot = VolumeNode.wrap(VolumeRoot)
        VolumeManager.__SetElementParent__(VolumeRoot)

        if SaveNewVolume:
            VolumeRoot.Save()

        prettyoutput.Log("Volume Root: " + VolumeRoot.attrib['Path'])
        # VolumeManager.__RemoveElementsWithoutPath__(VolumeRoot)

        return VolumeRoot
        # return cls.__init__(VolumeData, Filename)

    @staticmethod
    def WrapElement(e):
        """
        Returns a new class that represents the passed XML element
        :param ElementTree.Element e: Element to be represented by a class
        :return: (bool, An object inheriting from XElementWrapper) Returns true if the element had to be wrapped
        """

        assert (e.tag.endswith('_Link') is False), "Cannot wrap a link element that has not been loaded"

        OverrideClassName = e.tag + 'Node'
        OverrideClass = reflection.get_module_class('nornir_buildmanager.VolumeManagerETree', OverrideClassName,
                                                    LogErrIfNotFound=False)

        if OverrideClass is None:
            if "Path" in e.attrib:
                if os.path.isfile(e.attrib.get("Path")):
                    # TODO: Do we ever hit this path and do we need to make the os.path.isfile check anymore?
                    OverrideClass = XFileElementWrapper
                else:
                    OverrideClass = XContainerElementWrapper
            else:
                OverrideClass = XElementWrapper

        if isinstance(e, OverrideClass):
            return False, e
        else:
            return True, OverrideClass.wrap(e)

    @staticmethod
    def __SetElementParent__(Element, ParentElement=None):
        Element.SetParentNoChangeFlag(ParentElement)

        for i in range(len(Element) - 1, -1, -1):
            e = Element[i]
            if e.tag in versions.DeprecatedNodes:
                del Element[i]

        for i in range(0, len(Element)):
            e = Element[i]
            if isinstance(e, XElementWrapper):
                # Find out if we have an override class defined
                e.OnParentChanged()

        return

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


class XElementWrapper(ElementTree.Element):
    logger = logging.getLogger(__name__ + '.' + 'XElementWrapper')

    def sort(self):
        """Order child elements"""

        if not hasattr(self, 'SortKey'):
            return

        sortkey = self.SortKey
        if sortkey is None:
            return None

        if len(self) <= 1:
            return

        withKeys = filter(lambda child: hasattr(child, 'SortKey'), self)
        withoutKeys = filter(lambda child: not hasattr(child, 'SortKey'), self)
        linked = filter(lambda child: child.tag.endswith('_Link'), withoutKeys)
        other = filter(lambda child: not child.tag.endswith('_Link'), withoutKeys)

        sorted_withKeys = sorted(withKeys, key=operator.attrgetter('SortKey'), reverse=True)
        sorted_withoutKeys = sorted(withoutKeys, key=operator.attrgetter('tag'), reverse=True)
        sorted_linked = sorted(linked, key=lambda child: child.attrib['Path'], reverse=True)
        sorted_other = sorted(other, key=lambda child: str(child), reverse=True)

        self[:] = sorted_withKeys + sorted_linked + sorted_other + sorted_withoutKeys

        # self._children.sort(key=operator.attrgetter('SortKey'))

        for c in self:
            if isinstance(c, XElementWrapper):
                c.sort()

    @property
    def CreationTime(self) -> datetime.datetime:
        datestr = self.get('CreationDate', datetime.datetime.max)
        return datetime.datetime.fromisoformat(datestr)

    @property
    def SortKey(self):
        """The default key used for sorting elements"""
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
    def Checksum(self) -> str:
        return self.get('Checksum', "")

    @Checksum.setter
    def Checksum(self, Value):
        if not isinstance(Value, str):
            XElementWrapper.logger.warning(
                'Setting non string value on XElement.Checksum, automatically corrected: ' + str(Value))
        self.attrib['Checksum'] = str(Value)
        return

    @property
    def Version(self):
        return float(self.attrib.get('Version', 1.0))

    @Version.setter
    def Version(self, Value):
        self.attrib['Version'] = str(Value)

    @property
    def AttributesChanged(self):
        """
        :return: Boolean indicating if an attribute has changed.  Used to indicate
        the element needs to be saved to disk.
        :rtype: bool
        """
        return self._AttributesChanged

    @AttributesChanged.setter
    def AttributesChanged(self, Value):
        self._AttributesChanged = Value

    #         if Value:
    #             self.MarkNonContainerParentChanged()

    @property
    def ChildrenChanged(self) -> bool:
        """
        :return: Boolean indicating if a child (a direct child, not nested) of this element has changed.  Used to indicate
        the element needs to be saved to disk.
        :rtype: bool
        """
        return self._ChildrenChanged

    @ChildrenChanged.setter
    def ChildrenChanged(self, Value):
        self._ChildrenChanged = Value

    @property
    def ElementHasChangesToSave(self) -> bool:
        """Check this and child elements (which are not linked containers that will save themselves) for changes to save.  We need to note any nested elements that would save with this element"""

        if self.AttributesChanged or self.ChildrenChanged:
            return True

        ReturnValue = False
        for child in self:
            if child.tag.endswith('_Link'):
                continue

            if isinstance(child, XContainerElementWrapper):
                if child.SaveAsLinkedElement is False:
                    ReturnValue = ReturnValue or child.ElementHasChangesToSave
            else:
                ReturnValue = ReturnValue or child.ElementHasChangesToSave

        return ReturnValue

    def ResetElementChangeFlags(self):
        """Set this and child elements (which are not linked containers that
           will save themselves) change flags to false.  Called after the
           element is saved. for changes to save."""

        self._AttributesChanged = False
        self._ChildrenChanged = False

        for child in self:
            if child.tag.endswith('_Link'):
                continue

            if isinstance(child, XContainerElementWrapper):
                if child.SaveAsLinkedElement is False:
                    child.ResetElementChangeFlags()
            else:
                child.ResetElementChangeFlags()

        return

    #         if Value:
    #             self.MarkNonContainerParentChanged()
    #
    #     @property
    #     def MarkNonContainerParentChanged(self):
    #         '''
    #         Sets the parent's ChildrenChanged flag to True if the parent is not a ContainerElement or does not have the SaveAsLinkedElement attribute set to True
    #         '''
    #
    #         parent = self.Parent
    #         if parent is None:
    #             return
    #
    #         if not isinstance(parent, XContainerElementWrapper):
    #             parent._ChildrenChanged = True
    #             parent.MarkNonContainerParentChanged()
    #             return
    #
    #         if parent.SaveAsLinkedElement == False:
    #             parent._ChildrenChanged = True
    #             parent.MarkNonContainerParentChanged()
    #

    @property
    def Root(self) -> XElementWrapper:
        """The root of the element tree"""
        node = self
        while node.Parent is not None:
            node = node.Parent

        return node

    @property
    def Parent(self) -> XElementWrapper:
        return self._Parent

    def SetParentNoChangeFlag(self, value):
        """
        This is not a setter to avoid triggering the Attribute Changed flag with the
        __setattr__ override for this class
        """
        self.__dict__['_Parent'] = value
        self.OnParentChanged()

    @Parent.setter
    def Parent(self, Value):
        """
        Setting the parent with this method will set the Attribute Changed flag
        """
        self.__dict__['_Parent'] = Value
        self.OnParentChanged()

    def OnParentChanged(self):
        """Actions that should occur when our parent changes"""
        if '__fullpath' in self.__dict__:
            del self.__dict__['__fullpath']

    def indexofchild(self, obj) -> int:
        """Return the index of a child element"""
        for i, x in enumerate(self):
            if x == obj:
                return i

        raise ValueError("Element:\t{0}\n is not a child of:\n\t{1}".format(str(obj), str(self)))

    @classmethod
    def __GetCreationTimeString__(cls) -> str:
        now = datetime.datetime.utcnow()
        now = now.replace(microsecond=0)
        return str(now)

    def __init__(self, tag, attrib=None, **extra):

        global nid
        self.__dict__['id'] = nid
        nid = nid + 1

        self._AttributesChanged = False
        self._ChildrenChanged = False

        if attrib is None:
            attrib = {}
        else:
            StringAttrib = {}
            for k in list(attrib.keys()):
                if not isinstance(attrib[k], str):
                    XElementWrapper.logger.info(
                        'Setting non string value on <' + str(tag) + '>, automatically corrected: ' + k + ' -> ' + str(
                            attrib[k]))
                    StringAttrib[k] = str(attrib[k])
                else:
                    StringAttrib[k] = attrib[k]

            attrib = StringAttrib

        super(XElementWrapper, self).__init__(tag, attrib=attrib, **extra)

        self._Parent = None

        if not self.tag.endswith("_Link"):
            if 'CreationDate' not in self.attrib:
                self.attrib['CreationDate'] = XElementWrapper.__GetCreationTimeString__()

            self.Version = versions.GetLatestVersionForNodeType(tag)

    @classmethod
    def RemoveDuplicateElements(cls, tagName):
        """For nodes that should not be duplicated this function removes all but the last created element"""
        pass

    def IsParent(self, node) -> bool:
        """Returns true if the node is a parent"""
        if self.Parent is None:
            return False

        if self.Parent == node:
            return True
        else:
            return self.Parent.IsParent(node)

    @property
    def NeedsValidation(self) -> bool:
        raise NotImplemented("NeedsValidation should be implemented in derived class {0}".format(str(self)))

    def IsValidLazy(self) -> (bool, str):
        """
        First checks if the XElement requires validation before invoking IsValid
        """

        if self.NeedsValidation:
            return self.IsValid()
        else:
            return [True, "NeedsValidation flag not set"]

    def IsValid(self) -> (bool, str):
        """This function should be overridden by derrived classes.  It returns true if the file system or other external
           resources match the state recorded within the element.

           IsValid should always do the full work of validation and then update
           any meta-data, such as ValidationTime, to indicate the validation was
           done.  NeedsValidation should be used to determine if an element needs
           an IsValid call or if a check of the XElement state was sufficient to
           believe it is in a valid state.  IsValidLazy will only call IsValid
           on elements whose NeedsValidation call is True.

           Returns Tuple of state and a string with a reason"""

        if 'Version' not in self.attrib:
            if versions.GetLatestVersionForNodeType(self.tag) > 1.0:
                return False, "Node version outdated"

        if not versions.IsNodeVersionCompatible(self.tag, self.Version):
            return False, "Node version outdated"

        return True, ""

    def CleanIfInvalid(self) -> (bool, str):
        """Remove the contents of this node if it is out of date, returns true if node was cleaned"""
        Valid = self.IsValid()

        if isinstance(Valid, bool):
            Valid = (Valid, "")

        if not Valid[0]:
            return self.Clean(Valid[1])

        return Valid

    def Clean(self, reason=None):
        """Remove node from element tree and remove any external resources such as files"""

        DisplayStr = ' --- Cleaning ' + self.ToElementString() + ". "

        '''Remove the contents referred to by this node from the disk'''
        prettyoutput.Log(DisplayStr)
        if reason is not None:
            prettyoutput.Log("  --- " + reason)

        # Make sure we clean child elements if needed
        children = list(self)
        for child in children:
            if isinstance(child, XElementWrapper):
                child.Clean(reason="Parent was removed")

        if self.Parent is not None:
            try:
                self.Parent.remove(self)
            except:
                # Sometimes we have not been added to the parent at this point
                pass

    def Copy(self):
        """Creates a copy of the element"""
        t = type(self)
        cpy = t(tag=self.tag, attrib=self.attrib.copy())

        if self.text is not None:
            cpy.text = self.text

        if self.tail is not None:
            cpy.tail = self.tail

        if len(self) > 0:
            Warning(
                "Copying an element with children, possibly undefined behavior")  # Child elements are not included in copies and I have not tested that at all

        return cpy

    @classmethod
    def __CreateFromElement(cls, dictElement):
        """Create an instance of this class using an ElementTree.Element.
           Override to customize the creation of derived classes"""

        newElement = cls(tag=dictElement.tag, attrib=dictElement.attrib)

        if dictElement.text is not None:
            newElement.text = dictElement.text

        if dictElement.tail is not None:
            newElement.tail = dictElement.tail

        for i in range(0, len(dictElement)):
            newElement.insert(i, dictElement[i])

        return newElement

    @classmethod
    def wrap(cls, dictElement) -> XElementWrapper:
        """Change the class of an ElementTree.Element(PropertyElementName) to add our wrapper functions"""
        if isinstance(dictElement, cls):
            return dictElement

        newElement = cls.__CreateFromElement(dictElement)
        # dictElement.__class__ = cls
        assert (newElement is not None)
        assert (isinstance(newElement, cls))

        if 'CreationDate' not in newElement.attrib:
            cls.logger.info("Populating missing CreationDate attribute " + newElement.ToElementString())
            newElement.attrib['CreationDate'] = XElementWrapper.__GetCreationTimeString__()

        if isinstance(newElement, XContainerElementWrapper):
            if 'Path' not in newElement.attrib:
                prettyoutput.Log(newElement.ToElementString() + " no path attribute but being set as container")
            assert ('Path' in newElement.attrib)

        # Also convert all non-link child elements
        for c in newElement:
            if c.tag.endswith('_Link'):
                continue

            c = newElement._ReplaceChildIfUnwrapped(c)

        return newElement

    def ToElementString(self) -> str:
        strList = ElementTree.tostringlist(self)
        outStr = ""
        for s in strList:
            outStr = outStr + " " + s.decode('utf-8')
            if s == '>':
                break

        return outStr

    def __getattr__(self, name):

        """Called when an attribute lookup has not found the attribute in the usual places (i.e. it is not an instance attribute nor is it found in the class tree for self). name is the attribute name. This method should return the (computed) attribute value or raise an AttributeError exception.

        Note that if the attribute is found through the normal mechanism, __getattr__() is not called. (This
        is an intentional asymmetry between __getattr__() and __setattr__().) This is done both for
        efficiency reasons and because otherwise __getattr__() would have no way to access other attributes
        of the instance. Note that at least for instance variables, you can fake total control by not
        inserting any values in the instance attribute dictionary (but instead inserting them in another
        object). See the __getattribute__() method below for a way to actually get total control in
         new-style classes."""

        if name in self.__dict__:
            return self.__dict__[name]

        superClass = super(XElementWrapper, self)
        if superClass is not None:
            try:
                if hasattr(superClass, '__getattr__'):
                    return superClass.__getattr__(name)
            except AttributeError:
                pass

        if name in self.attrib:
            return self.attrib[name]

        raise AttributeError(name)

    def __setattr__(self, name, value):
        """Called when an attribute assignment is attempted. This is called instead of the
           normal mechanism (i.e. store the value in the instance dictionary). name is the
           attribute name, value is the value to be assigned to it."""
        if hasattr(self.__class__, name):
            attribute = getattr(self.__class__, name)
            if isinstance(attribute, property):
                if attribute.fset is not None:
                    # Mark the _AttributesChanged flag if the value has been updated
                    if attribute.fget is not None:
                        self._AttributesChanged = self._AttributesChanged or attribute.fget(self) != value
                    else:
                        self._AttributesChanged = True
                    attribute.fset(self, value)
                    return
                else:
                    assert (attribute.fset is not None)  # Why are we trying to set a property without a setter?
            else:
                super(XElementWrapper, self).__setattr__(name, value)
                return

        if name in self.__dict__:
            self.__dict__[name] = value
        elif name[0] == '_':
            self.__dict__[name] = value
        elif self.attrib is not None:
            originalValue = None
            if name in self.attrib:
                originalValue = self.attrib[name]

            if not isinstance(value, str):
                XElementWrapper.logger.info('Setting non string value on <' + str(
                    self.tag) + '>, automatically corrected: ' + name + ' -> ' + str(value))

                strVal = None
                if isinstance(value, float):
                    strVal = '%g' % value
                else:
                    strVal = str(value)

                self.attrib[name] = strVal
                self._AttributesChanged = self._AttributesChanged or (strVal != originalValue)
            else:
                self.attrib[name] = value
                self._AttributesChanged = self._AttributesChanged or (value != originalValue)

    def __delattr__(self, name):

        """Like __setattr__() but for attribute deletion instead of assignment. This should only be implemented if del obj.name is meaningful for the object."""
        if name in self.__dict__:
            self.__dict__.pop(name)
        elif name in self.attrib:
            self._AttributesChanged = True
            self.attrib.pop(name)

    def CompareAttributes(self, dictAttrib):
        """Compare the passed dictionary with the attributes on the node, return entries which do not match"""
        mismatched = list()

        for entry, val in list(dictAttrib.items()):
            if hasattr(self, entry):
                if getattr(self, entry) != val:
                    mismatched.append(entry)
            else:
                mismatched.append(entry[0])

        return mismatched

    def RemoveOldChildrenByAttrib(self, ElementName, AttribName, AttribValue):
        """If multiple children match the criteria, we remove all but the child with the latest creation date"""
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

    def GetChildrenByAttrib(self, ElementName: str, AttribName: str, AttribValue) -> [XElementWrapper]:
        XPathStr = "%(ElementName)s[@%(AttribName)s='%(AttribValue)s']" % {'ElementName': ElementName,
                                                                           'AttribName': AttribName,
                                                                           'AttribValue': AttribValue}
        children = self.findall(XPathStr)

        if children is None:
            return ()

        return children

    def GetChildByAttrib(self, ElementName: str, AttribName: str, AttribValue) -> XElementWrapper | None:

        XPathStr = ""
        if isinstance(AttribValue, float):
            XPathStr = "%(ElementName)s[@%(AttribName)s='%(AttribValue)g']" % {'ElementName': ElementName,
                                                                               'AttribName': AttribName,
                                                                               'AttribValue': AttribValue}
        else:
            XPathStr = "%(ElementName)s[@%(AttribName)s='%(AttribValue)s']" % {'ElementName': ElementName,
                                                                               'AttribName': AttribName,
                                                                               'AttribValue': AttribValue}

        assert (len(XPathStr) > 0)
        Child = self.find(XPathStr)
        # if(len(Children) > 1):
        #    prettyoutput.LogErr("Multiple nodes found fitting criteria: " + XPathStr)
        #    return Children

        # if len(Children) == 0:
        #    return None

        if Child is None:
            return None

        return Child

    def Contains(self, Element: XElementWrapper) -> bool:
        for c in self:
            for k, v in c.attrib:
                if k == 'CreationDate':
                    continue
                if k in self.attrib:
                    if not v == self.attrib[k]:
                        return False
        return True

    def UpdateOrAddChildByAttrib(self, Element, AttribNames=None) -> (bool, XElementWrapper):
        if AttribNames is None:
            AttribNames = ['Name']
        elif isinstance(AttribNames, str):
            AttribNames = [AttribNames]
        elif not isinstance(AttribNames, list):
            raise Exception("Unexpected attribute names for UpdateOrAddChildByAttrib")

        attribXPathTemplate = "@%(AttribName)s='%(AttribValue)s'"
        attribXPaths = []

        for AttribName in AttribNames:
            val = Element.attrib[AttribName]
            attribXPaths.append(attribXPathTemplate % {'AttribName': AttribName,
                                                       'AttribValue': val})

        XPathStr = "%(ElementName)s[%(QueryString)s]" % {'ElementName': Element.tag,
                                                         'QueryString': ' and '.join(attribXPaths)}
        return self.UpdateOrAddChild(Element, XPathStr)

    def UpdateOrAddChild(self, Element: XElementWrapper, XPath: str = None) -> (bool, XElementWrapper):
        """Adds an element using the specified XPath.  If the XPath is unspecified the element name is used
           Returns a tuple with (True/False, Element).
           True indicates the element did not exist and was added.
           False indicates the element existed and the existing value is returned.
           """

        if XPath is None:
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
        child = self.find(XPath)
        if child is None:
            if Element is not None:
                self.append(Element)
                assert (Element in self)
                child = Element
                NewNodeCreated = True
            else:
                # No data provided to create the child element
                return False, None

        # Make sure the parent is set correctly
        (wrapped, child) = VolumeManager.WrapElement(child)

        if wrapped:
            VolumeManager.__SetElementParent__(child, self)
        # Child.Parent = self

        if NewNodeCreated:
            assert (self.ChildrenChanged is True), "ChildrenChanged must be true if we report adding a child element"

        return NewNodeCreated, child

    def AddChild(self, new_child_element):
        DeprecationWarning("Use append instead of AddChild on XElementWrapper based objects")
        return self.append(new_child_element)

    def append(self, Child):
        assert (not self == Child)
        self._ChildrenChanged = True
        super(XElementWrapper, self).append(Child)
        Child.Parent = self
        assert (Child in self)

    def remove(self, Child):
        assert (not self == Child)
        self._ChildrenChanged = True
        super(XElementWrapper, self).remove(Child)
        assert (Child not in self)

    def FindParent(self, ParentTag: str) -> XElementWrapper | None:
        """Find parent with specified tag"""
        assert (ParentTag is not None)
        p = self.Parent
        while p is not None:
            if p.tag == ParentTag:
                return p
            p = p.Parent
        return None

    def FindFromParent(self, xpath: str) -> XElementWrapper | None:
        """Run find on xpath on each parent, return first hit"""
        #        assert (not ParentTag is None)
        p = self.Parent
        while p is not None:
            result = p.find(xpath)
            if result is not None:
                return result

            p = p.Parent
        return None

    def FindAllFromParent(self, xpath: str) -> [XElementWrapper]:
        """Run findall on xpath on each parent, return results only first nearest parent with resuls"""
        #        assert (not ParentTag is None)
        p = self.Parent
        while p is not None:
            result = p.findall(xpath)
            if result is not None:
                return result

            p = p.Parent
        return None

    def _ReplaceChildElementInPlace(self, old, new):

        # print("Removing {0}".format(str(old)))
        i = self.indexofchild(old)

        self[i] = new
        # self.remove(old)
        # self.insert(i, new)

        VolumeManager.__SetElementParent__(new, self)

    def ReplaceChildWithLink(self, child):
        if isinstance(child, XContainerElementWrapper):
            if child not in self:
                return

            LinkElement = XElementWrapper(child.tag + '_Link', attrib=child.attrib)
            # SaveElement.append(LinkElement)
            self._ReplaceChildElementInPlace(child, LinkElement)

    def _ReplaceChildIfUnwrapped(self, child):
        if isinstance(child, XElementWrapper):
            return child

        assert (child in self), "ReplaceChildIfUnwrapped: {0} not a child of {1} as expected".format(str(child),
                                                                                                     str(self))

        (wrapped, wrappedElement) = VolumeManager.WrapElement(child)

        if wrapped:
            self._ReplaceChildElementInPlace(child, wrappedElement)
            wrappedElement._AttributesChanged = False  # Setting the parent will set this flag, but if we loaded it there was no change

        return wrappedElement

    # replacement for find function that loads subdirectory xml files
    def find(self, path, namespaces=None) -> XElementWrapper | None:

        (UnlinkedElementsXPath, LinkedElementsXPath, RemainingXPath, UsedWildcard) = self.__ElementLinkNameFromXPath(
            xpath)

        if isinstance(self, XContainerElementWrapper):  # Only containers have linked elements
            LinkMatches = super(XElementWrapper, self).findall(LinkedElementsXPath)
            if LinkMatches is None:
                return None

            if UsedWildcard:
                LinkMatches = list(filter(lambda e: e.tag.endswith('_Link'), LinkMatches))

            num_matches = len(LinkMatches)
            if num_matches > 0:
                # if num_matches > 1:
                #    prettyoutput.Log("Need to load {0} links".format(num_matches))
                self._replace_links(LinkMatches)

                if self.ElementHasChangesToSave:
                    self.Save()

        matchiterator = super(XElementWrapper, self).iterfind(UnlinkedElementsXPath)
        for match in matchiterator:
            # Run in a loop because find returns the first match, if the first match is invalid look for another
            #             NotValid = match.CleanIfInvalid()
            #             if NotValid:
            #                 continue

            match = self._ReplaceChildIfUnwrapped(match)
            #
            if len(RemainingXPath) > 0:
                foundChild = match.find(RemainingXPath)

                # Continue searching links if we don't find a result on the loaded elements
                if foundChild is not None:
                    assert (isinstance(foundChild, XElementWrapper))
                    return foundChild
            else:
                return match

        return None

    def findall(self, path, namespaces=None) -> [XElementWrapper]:
        sm = None
        match = path
        (UnlinkedElementsXPath, LinkedElementsXPath, RemainingXPath, UsedWildcard) = self.__ElementLinkNameFromXPath(
            match)

        # TODO: Need to modify to only search one level at a time
        # OK, check for linked elements that also meet the criteria

        LinkMatches = list(super(XElementWrapper, self).findall(LinkedElementsXPath))
        if LinkMatches is None:
            return  # matches

        if UsedWildcard:
            LinkMatches = list(filter(lambda e: e.tag.endswith('_Link'), LinkMatches))

        num_matches = len(LinkMatches)
        if num_matches > 0:

            # if num_matches > 1:
            #    prettyoutput.Log("Need to load {0} links".format(num_matches))
            self._replace_links(LinkMatches)

            if self.ElementHasChangesToSave:
                self.Save()

        # return matches
        matches = super(XElementWrapper, self).findall(UnlinkedElementsXPath)

        # Since this is a generator function, we need to load all children that are links before we 
        # return the first node.  If we do not the caller may load other links
        # from the parent node and then the matches will no longer be present in the
        # parent.

        for m in matches:
            #             NotValid = m.CleanIfInvalid()
            #             if NotValid:
            #                 continue
            if '_Link' in m.tag:
                # TODO: Can this code path ever execute?  Seems like we pre-load the links above
                m_replaced = self._replace_link(m)
                if m_replaced is None:
                    continue
                else:
                    m = m_replaced
            else:
                m = self._ReplaceChildIfUnwrapped(m)

        loaded_matches = super(XElementWrapper, self).findall(UnlinkedElementsXPath)
        for m in loaded_matches:
            if len(RemainingXPath) > 0:
                subContainerMatches = list(m.findall(RemainingXPath))
                if subContainerMatches is not None:
                    for sm in subContainerMatches:
                        assert (isinstance(sm, XElementWrapper))  # T
                        # if not isinstance(sm, XElementWrapper):
                        # m.remove(sm)
                        # sm = VolumeManager.WrapElement(sm)
                        # m.insert(sm)

                        (yield sm)
            else:
                (yield m)

    @classmethod
    def __ElementLinkNameFromXPath(cls, xpath) -> (str, str, str, bool):
        """
        :Return: The name to search for the linked and unlinked version of the search term.
                 If only attributes are specified the Link search term will return all
                 elements. (UnlinkedElementPath, LinkedElementPath, RemainingPath, HasWildcard)
                 If the xpath has a wildcard (HasWildcard) a function searching with LinkedElementPath
                 must manually check each child element tag to see if it ends in _Link.
        """

        if '\\' in xpath:
            Logger = logging.getLogger(__name__ + '.' + '__ElementLinkNameFromXPath')
            Logger.warning("Backslash found in xpath query, is this intentional or should it be a forward slash?")
            Logger.warning("XPath: " + xpath)

        parts = xpath.split('/')
        UnlinkedElementsXPath = parts[0]
        SubContainerParts = UnlinkedElementsXPath.split('[')
        SubContainerName = SubContainerParts[0]

        HaveSubContainerName = not (SubContainerName is None or len(SubContainerName) == 0)
        SubContainerIsWildcard = SubContainerName == '*'

        if not HaveSubContainerName:
            SubContainerName = '*'
            LinkedElementsXPath = SubContainerName + UnlinkedElementsXPath
            SubContainerIsWildcard = True
        else:
            if not SubContainerIsWildcard:
                LinkedSubContainerName = SubContainerName + '_Link'
                LinkedElementsXPath = UnlinkedElementsXPath.replace(SubContainerName, LinkedSubContainerName, 1)
            else:
                # ElementTree does not let use say '*_link' when searching tag names.  So we have to return 
                # all tags and filter out _links later.
                LinkedElementsXPath = UnlinkedElementsXPath

        RemainingXPath = xpath[len(UnlinkedElementsXPath) + 1:]
        return UnlinkedElementsXPath, LinkedElementsXPath, RemainingXPath, SubContainerIsWildcard

    def LoadAllLinkedNodes(self):
        """Recursively load all the linked nodes on this element"""

        child_nodes = list(self)
        linked_nodes = list(filter(lambda x: x.tag.endswith('_Link'), child_nodes))

        if len(linked_nodes) > 0:
            assert hasattr(self,
                           '_replace_links'), 'Nodes with linked children must implement _replace_links to load those links'

        if hasattr(self, '_replace_links'):
            self._replace_links(linked_nodes)

        # Check all of our child nodes for links
        for n in self:
            n.LoadAllLinkedNodes()

        #         for n in child_nodes:
        #             if n.tag.endswith('_Link'):
        #                 n_replaced = self._replace_link(n)
        #                 if n_replaced is None:
        #                     continue
        #                 n = n_replaced
        #
        #                 n.LoadAllLinkedNodes()

        return


class XResourceElementWrapper(VMH.Lockable, XElementWrapper):
    """Wrapper for an XML element that refers to a file or directory"""

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

            if not hasattr(self, '_Parent'):
                return FullPathStr

            IterElem = self.Parent

            while IterElem is not None:
                if hasattr(IterElem, 'FullPath'):
                    FullPathStr = os.path.join(IterElem.FullPath, FullPathStr)
                    IterElem = None
                    break

                elif hasattr(IterElem, '_Parent'):
                    IterElem = IterElem._Parent
                else:
                    raise Exception("FullPath could not be generated for resource")
            #
            #             if os.path.isdir(FullPathStr):  # Don't create a directory for files
            #                 if not os.path.exists(FullPathStr):
            #                     prettyoutput.Log("Creating missing directory for FullPath: " + FullPathStr)
            #                     os.makedirs(FullPathStr)

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

    @property
    def ValidationTime(self):
        """
        An optional attribute to record the last time we validated the state
        of the file or directory this element represents.
        :return: Returns None if the attribute has not been set, otherwise an integer
        """
        val = self.attrib.get('ValidationTime', datetime.datetime.min)
        if val is not None and isinstance(val, str):
            val = datetime.datetime.fromisoformat(val)

        return val

    @ValidationTime.setter
    def ValidationTime(self, val):
        if val is None:
            if 'ValidationTime' in self.attrib:
                del self.attrib['ValidationTime']
        else:
            assert (isinstance(val, datetime.datetime))
            self.attrib['ValidationTime'] = str(val)

    def UpdateValidationTime(self):
        """
        Sets ValidationTime to the LastModified time on the file or directory
        """

        self.ValidationTime = self.LastFileSystemModificationTime

    @property
    def ChangesSinceLastValidation(self):
        """
        :return: True if the modification time on the directory is later than our last validation time, or None if the path doesn't exist
        """
        dir_mod_time = self.LastFileSystemModificationTime
        if dir_mod_time is None:
            return None

        return self.ValidationTime < dir_mod_time

    @property
    def LastFileSystemModificationTime(self):
        """
        :return: The most recent time the resource's file or directory was
        modified. Used to indicate that a verification needs to be repeated.
        None is returned if the file or directory does not exist
        :rtype: datetime.datetime
        """
        try:
            level_stats = os.stat(self.FullPath)
            level_last_filesystem_modification = datetime.datetime.utcfromtimestamp(level_stats.st_mtime)
            return level_last_filesystem_modification
        except FileNotFoundError:
            return None

    @property
    def NeedsValidation(self) -> bool:

        if isinstance(self, XContainerElementWrapper):
            if self.SaveAsLinkedElement:
                raise Exception(
                    "Container elements ({0}) that save as links must not use directory modification time to check for changes because the meta-data saves in the same directory".format(
                        self.tag))

        changes = self.ChangesSinceLastValidation
        if changes is None:
            return True

        return changes

    def ToElementString(self):
        outStr = self.FullPath
        return outStr

    def Clean(self, reason=None):
        if self.Locked:
            Logger = logging.getLogger(__name__ + '.' + 'Clean')
            Logger.warning('Could not delete resource with locked flag set: %s' % self.FullPath)
            if reason is not None:
                Logger.warning('Reason for attempt: %s' % reason)
            return False

        '''Remove the contents referred to by this node from the disk'''
        if os.path.exists(self.FullPath):
            try:
                if os.path.isdir(self.FullPath):
                    nornir_shared.files.rmtree(self.FullPath)
                else:
                    os.remove(self.FullPath)
            except Exception as e:
                Logger = logging.getLogger(__name__ + '.' + 'Clean')
                Logger.warning('Could not delete cleaned directory: {self.FullPath}\n{e}')

        return super(XResourceElementWrapper, self).Clean(reason=reason)


class XFileElementWrapper(XResourceElementWrapper):
    """Refers to a file generated by the pipeline"""

    @property
    def Name(self) -> str:
        if 'Name' not in self.attrib:
            return self._GetAttribFromParent('Name')

        return self.attrib.get('Name', None)

    @Name.setter
    def Name(self, value):
        assert (isinstance(value, str))
        self.attrib['Name'] = value

    @property
    def Type(self) -> str:
        if 'Type' not in self.attrib:
            return self._GetAttribFromParent('Type')

        return self.attrib['Type']

    @Type.setter
    def Type(self, value):
        self.attrib['Type'] = value

    @property
    def Path(self) -> str:
        return self.attrib.get('Path', '')

    @Path.setter
    def Path(self, val):
        self.attrib['Path'] = val
        directory = os.path.dirname(self.FullPath)

        if directory is not None and len(directory) > 0:
            try:
                os.makedirs(directory)
            except (OSError, FileExistsError):
                if not os.path.isdir(directory):
                    raise ValueError(
                        f"{self.__class__}.Path property was set to an existing file or non-directory object {self.FullPath}")

        if hasattr(self, '__fullpath'):
            del self.__dict__['__fullpath']
        return

    def IsValid(self) -> (bool, str):
        """
        Checks that the file exists by attempting to update the validation time
        """

        try:
            self.ValidationTime = self.LastFileSystemModificationTime
        except FileNotFoundError:
            return False, 'File does not exist'

        result = super(XFileElementWrapper, self).IsValid()

        return result

    @classmethod
    def Create(cls, tag, Path, attrib, **extra):
        obj = XFileElementWrapper(tag=tag, attrib=attrib, **extra)
        obj.attrib['Path'] = Path
        return obj

    @property
    def Checksum(self) -> str:
        """Checksum of the file resource when the node was last updated"""
        checksum = self.get('Checksum', None)
        if checksum is None:
            if os.path.exists(self.FullPath):
                checksum = nornir_shared.checksum.FileChecksum(self.FullPath)
                self.attrib['Checksum'] = str(checksum)
                self._AttributesChanged = True

        return checksum


class XContainerElementWrapper(XResourceElementWrapper):
    """XML meta-data for a container whose sub-elements are contained within a directory on the file system.  The directories container will always be the same, such as TilePyramid"""

    @property
    def SaveAsLinkedElement(self):
        """
        When set to true, the element will be saved as a link element in the subdirectory
        It may be set to false to prevent saving meta-data from updating the modification
        time of the directory.  When set to false the element remains under the
        parent element wherever that XML file may be.
        """
        return True

    @property
    def SortKey(self):
        """The default key used for sorting elements"""

        tag = self.tag
        if tag.endswith("_Link"):
            tag = tag[:-len("_Link")]
            tag = tag + "Node"

            current_module = sys.modules[__name__]
            if hasattr(current_module, tag):
                tagClass = getattr(current_module, tag)
                # nornir_shared.Reflection.get_class(tag)

                if tagClass is not None:
                    if hasattr(tagClass, "ClassSortKey"):
                        return tagClass.ClassSortKey(self)

        return self.tag

    @property
    def Path(self) -> str:
        return self.attrib.get('Path', '')

    @Path.setter
    def Path(self, val):

        super(XContainerElementWrapper, self.__class__).Path.fset(self, val)

        try:
            os.makedirs(self.FullPath)
        except (OSError, FileExistsError):
            if not os.path.isdir(self.FullPath):
                raise ValueError(
                    "{0}.Path property was set to an existing file or non-directory file system object {1}".format(
                        self.__class__, self.FullPath))

        return

    def IsValid(self) -> (bool, str):
        ResourcePath = self.FullPath

        if self.Parent is not None:  # Don't check for validity if our node has not been added to the tree yet
            try:
                files_found = False
                with os.scandir(ResourcePath) as pathscan:
                    for item in pathscan:
                        if item.name[0] == '.':  # Avoid the .desktop_ini files of the world
                            continue

                        if item.is_file() or item.is_dir():
                            files_found = True
                            break

                if not files_found:
                    return False, 'Directory is empty: {0}' % ResourcePath

            except FileNotFoundError:
                return False, '{0} does not exist' % ResourcePath

        elif not os.path.isdir(ResourcePath):
            return False, 'Directory does not exist'

        return super(XContainerElementWrapper, self).IsValid()

    def RepairMissingLinkElements(self, recurse=True):
        """
        Searches all subdirectories under the element.  Any VolumeData.xml files
        found are loaded and a link is created for the top level element.  Written
        to repair the case where a VolumeData.xml is deleted and we want to recover
        at least some of the data.
        """

        if self.SaveAsLinkedElement is False:
            return  # This element is not saved as a linked element

        with os.scandir(self.FullPath) as pathscan:
            for path in filter(lambda p: p.is_dir() and p.name[0] != '.', pathscan):

                possible_meta_data_path = os.path.join(path, 'VolumeData.XML')

                if not os.path.exists(possible_meta_data_path):
                    continue

                # prettyoutput.Log("Found potential linked element: {0}".format(item.path))

                dirname = os.path.dirname(possible_meta_data_path)

                expected_path = os.path.basename(dirname)

                # Check to be sure that this is a new node
                existingChild = self.find("*[@Path='{0}']".format(expected_path))

                if existingChild is not None:
                    continue

                prettyoutput.Log("Found missing linked container {0}".format(dirname))

                # Load the VolumeData.xml, take the root element name and create a link in our element
                loadedElement = self._load_wrap_setparent_link_element(dirname)
                if loadedElement is not None:
                    self.append(loadedElement)
                    prettyoutput.Log("\tAdded: {0}".format(loadedElement))
                    self.ChildrenChanged = True

            if recurse:
                for child in self:
                    if hasattr(child, "RepairMissingLinkElements"):
                        child.RepairMissingLinkElements()

    @staticmethod
    def _load_link_element(fullpath: str):
        """Loads an XML file from the file system and returns the root element"""
        filename = os.path.join(fullpath, "VolumeData.xml")
        xml_tree = ElementTree.parse(filename)
        return xml_tree.getroot()

    @staticmethod
    def _load_wrap_link_element(fullpath: str):
        """Loads an xml file containing a subset of our meta-data referred to by a LINK element.  Wraps the loaded XML in the correct meta-data class"""

        XMLElement = XContainerElementWrapper._load_link_element(fullpath)
        (wrapped, NewElement) = VolumeManager.WrapElement(XMLElement)
        # SubContainer = XContainerElementWrapper.wrap(XMLElement)

        return wrapped, NewElement

    def _load_wrap_setparent_link_element(self, fullpath: str):
        """Loads an xml file containing a subset of our meta-data referred to by a LINK element.  Wraps the loaded XML in the correct meta-data class"""

        XMLElement = XContainerElementWrapper._load_link_element(fullpath)
        (wrapped, NewElement) = VolumeManager.WrapElement(XMLElement)
        # SubContainer = XContainerElementWrapper.wrap(XMLElement)

        if wrapped:
            VolumeManager.__SetElementParent__(NewElement, self)

        return NewElement

    def _replace_link(self, link_node, fullpath: str = None) -> XElementWrapper | None:
        """Load the linked node.  Remove link node and replace with loaded node.  Checks that the loaded node is valid"""

        if fullpath is None:
            fullpath = self.FullPath

        SubContainerPath = os.path.join(fullpath, link_node.attrib["Path"])

        try:
            loaded_element = self._load_wrap_setparent_link_element(SubContainerPath)
        except IOError as e:
            self.remove(link_node)
            # logger = logging.getLogger(__name__ + '.' + '_load_link_element')
            prettyoutput.LogErr(
                "Removing link node after IOError loading linked XML file: {0}\n{1}".format(fullpath, str(e)))
            return None
        except ElementTree.ParseError as e:
            # logger = logging.getLogger(__name__ + '.' + '_load_link_element')
            prettyoutput.LogErr("Parse error loading linked XML file: {0}\n{1}".format(fullpath, str(e)))
            self.remove(link_node)
            return None
        except Exception as e:
            # logger = logging.getLogger(__name__ + '.' + '_load_link_element')
            prettyoutput.LogErr("Unexpected error loading linked XML file: {0}\n{1}".format(fullpath, str(e)))
            raise e

        self._ReplaceChildElementInPlace(old=link_node, new=loaded_element)

        # Check to ensure the newly loaded element is valid
        if loaded_element.NeedsValidation:
            cleaned = loaded_element.CleanIfInvalid()
            if cleaned:
                return None

        return loaded_element

    def _replace_links(self, link_nodes: [XElementWrapper], fullpath: str = None):
        """Load the linked nodes.  Remove link node and replace with loaded node.  Checks that the loaded node is valid"""

        # Ensure we are actually working on a list
        if len(link_nodes) == 0:
            return []
        elif len(link_nodes) == 1:
            return [self._replace_link(link_nodes[0], fullpath=fullpath)]

        if fullpath is None:
            fullpath = self.FullPath

        SubContainerPaths = [os.path.join(fullpath, link_node.attrib["Path"]) for link_node in link_nodes]

        loaded_elements = []

        # Use a different threadpool so that if callars are already on a thread we don't create deadlocks where they are waiting for load tasks to be returned from the Queue
        with concurrent.futures.ThreadPoolExecutor() as pool:
            # pool = nornir_pools.GetThreadPool('ReplaceLinks')

            tasks = []
            for i, fullpath in enumerate(SubContainerPaths):
                t = pool.submit(XContainerElementWrapper._load_wrap_link_element, fullpath)
                t.link_node = link_nodes[i]
                tasks.append(t)

            clean_tasks = []

            for task in concurrent.futures.as_completed(tasks):
                try:
                    link_node = task.link_node
                    (wrapped, wrapped_loaded_element) = task.result()
                except IOError as e:
                    self.remove(link_node)
                    # logger = logging.getLogger(__name__ + '.' + '_load_link_element')
                    prettyoutput.LogErr(
                        "Removing link node after IOError loading linked XML file: {0}\n{1}".format(fullpath, str(e)))
                    continue
                except ElementTree.ParseError as e:
                    # logger = logging.getLogger(__name__ + '.' + '_load_link_element')
                    prettyoutput.LogErr("Parse error loading linked XML file: {0}\n{1}".format(fullpath, str(e)))
                    self.remove(link_node)
                    continue
                except Exception as e:
                    # logger = logging.getLogger(__name__ + '.' + '_load_link_element')
                    prettyoutput.LogErr("Unexpected error loading linked XML file: {0}\n{1}".format(fullpath, str(e)))
                    continue

                # (wrapped, wrapped_loaded_element) = VolumeManager.WrapElement(loaded_element)
                # SubContainer = XContainerElementWrapper.wrap(XMLElement)

                if wrapped:
                    VolumeManager.__SetElementParent__(wrapped_loaded_element, self)

                self._ReplaceChildElementInPlace(old=link_node, new=wrapped_loaded_element)

                if wrapped_loaded_element.NeedsValidation:
                    t = pool.submit(wrapped_loaded_element.IsValid)
                    # t = pool.add_task("CleanIfInvalid " + fullpath, wrapped_loaded_element.IsValid)
                    clean_tasks.append(t)

                # Check to ensure the newly loaded element is valid
                # Cleaned = wrapped_loaded_element.CleanIfInvalid()

            for clean_task in concurrent.futures.as_completed(clean_tasks):
                IsValid = clean_task.result()

                if IsValid:
                    loaded_elements.append(wrapped_loaded_element)
                else:
                    wrapped_loaded_element.CleanIfInvalid()

        return loaded_elements

    def __init__(self, tag, attrib=None, **extra):

        if attrib is None:
            attrib = {}

        super(XContainerElementWrapper, self).__init__(tag=tag, attrib=attrib, **extra)

        # if Path is None:
        assert ('Path' in self.attrib)
        # else:     
        # self.attrib['Path'] = Path

    def Save(self, tabLevel=None, recurse=True):
        """
        Public version of Save, if this element is not flagged SaveAsLinkedElement
        then we need to save the parent to ensure our data is retained
        """

        if self.SaveAsLinkedElement:
            return self._Save()

        elif self.Parent is not None:
            return self.Parent.Save()

        raise NotImplemented("Cannot save a container node that is not linked without a parent node to save it under")

    def _Save(self, tabLevel=None, recurse=True):
        """
        Called by another Save function.  This function is either called by a
        parent element or by ourselves if SaveAsLinkedElement is True.

        If recurse = False we only save this element, no child elements are saved
        """

        AnyChangesFound = self.ElementHasChangesToSave

        if tabLevel is None:
            tabLevel = 0

        #         if hasattr(self, 'FullPath'):
        #             logger = logging.getLogger(__name__ + '.' + 'Save')
        #             logger.info("Saving " + self.FullPath)

        # Don't do work sorting children or validating attributes if there is no indication they've changed
        if self.ChildrenChanged:
            self.sort()

        if self.AttributesChanged:
            ValidateAttributesAreStrings(self)

        # pool = Pools.GetGlobalThreadPool()

        # tabs = '\t' * tabLevel

        # if hasattr(self, 'FullPath'):
        #    logger.info("Saving " + self.FullPath)

        # logger.info('Saving ' + tabs + str(self))
        xmlfilename = 'VolumeData.xml'

        ValidateAttributesAreStrings(self)

        # Create a copy of ourselves for saving.  If this is not done we have the potential to change a collection during iteration
        # which would break the pipeline manager in subtle ways
        SaveElement = ElementTree.Element(self.tag, attrib=self.attrib)
        if self.text is not None:
            SaveElement.text = self.text

        if self.tail is not None:
            SaveElement.tail = self.tail

        # SaveTree = ElementTree.ElementTree(SaveElement)

        # Any child containers we create a link to and remove from our file
        for i in range(len(self) - 1, -1, -1):
            child = self[i]
            if child.tag.endswith('_Link'):
                SaveElement.append(child)
            elif isinstance(child, XContainerElementWrapper):
                AnyChangesFound = AnyChangesFound or child.AttributesChanged  # Since linked elements display the elements attributes, we should update if they've changed

                # Save the child first so it can validate attributes before we attempt to copy them to a link element
                if recurse:
                    child._Save(tabLevel + 1)

                if child.SaveAsLinkedElement:
                    linktag = child.tag + '_Link'

                    # Sanity check to prevent duplicate link bugs
                    if __debug__:
                        existingNode = SaveElement.find(linktag + "[@Path='{0}']".format(child.Path))
                        if existingNode is not None:
                            raise AssertionError("Found duplicate element when saving {0}\nDuplicate: {1}".format(
                                ElementTree.tostring(SaveElement, encoding="utf-8"),
                                ElementTree.tostring(existingNode, encoding="utf-8")))

                    LinkElement = XElementWrapper(linktag, attrib=child.attrib)
                    # SaveElement.append(LinkElement)
                    SaveElement.append(LinkElement)
                else:
                    SaveElement.append(child)

                # logger.warn("Unloading " + child.tag)
                # del self[i]
                # self.append(LinkElement)
            else:
                if isinstance(child,
                              XElementWrapper):  # Elements not converted to an XElementWrapper should not have changed.
                    AnyChangesFound = AnyChangesFound or child.AttributesChanged or child.ChildrenChanged

                    # Don't bother doing prep work on the child element if no changes are recorded
                    if child.AttributesChanged:
                        ValidateAttributesAreStrings(SaveElement)

                    if child.ChildrenChanged:
                        child.sort()

                    child._AttributesChanged = False
                    child._ChildrenChanged = False

                # Add a reference to the child element to the element we are serializing to XML
                SaveElement.append(child)

        if AnyChangesFound and self.SaveAsLinkedElement:
            self.__SaveXML(xmlfilename, SaveElement)
            self.ResetElementChangeFlags()
            # prettyoutput.Log("Saving " + self.FullPath + ", state change recorded in that container or child elements.");
        # elif not AnyChangesFound:
        # prettyoutput.Log("Skipping " + self.FullPath + ", no state change recorded in that container or child elements. (Child containers may have changes but could be saved directly instead)");

    #        pool.add_task("Saving self.FullPath",   self.__SaveXML, xmlfilename, SaveElement)

    # If we are the root of all saves then make sure they have all completed before returning
    # if(tabLevel == 0 or recurse==False):
    # pool.wait_completion()

    def __SaveXML(self, xmlfilename, SaveElement):
        """Intended to be called on a thread from the save function"""
        try:
            OutputXML = ElementTree.tostring(SaveElement, encoding="utf-8")
        except Exception as e:
            prettyoutput.Log(f"Cannot encode output XML:\n{e}")
            raise e

        assert (len(OutputXML)), "Trying to save an entirely empty XML file... why?"

        if len(OutputXML) == 0:
            raise Exception(f"No meta data produced for XML element {SaveElement} writing to {xmlfilename}")

        try:
            os.makedirs(self.FullPath, exist_ok=True)
        except (OSError, FileExistsError, WindowsError) as e:
            if not os.path.isdir(self.FullPath):
                raise ValueError(
                    "{0} is trying to save to a non directory path {1}\n{2}".format(str(SaveElement), self.FullPath,
                                                                                    str(e)))

        # prettyoutput.Log("Saving %s" % xmlfilename)
        BackupXMLFilename = f"{os.path.basename(xmlfilename)}.backup.xml"
        BackupXMLFullPath = os.path.join(self.FullPath, BackupXMLFilename)
        XMLFilename = os.path.join(self.FullPath, xmlfilename)

        # If the current VolumeData.xml has data, then create a backup copy
        # This should prevent us removing valid backups if the current VolumeData.xml
        # has zero bytes
        try:
            statinfo = os.stat(XMLFilename)
            if statinfo.st_size > 0:

                try:
                    # Attempt to create a backup of the meta-data file before we replace it, just in case
                    os.remove(BackupXMLFullPath)
                except FileNotFoundError:
                    # It is OK if a backup file does not exist
                    pass

                # Move the current file to the backup location, write the new data        
                shutil.move(XMLFilename, BackupXMLFullPath)
            else:
                # This is a rare issue where I'd write a file but have zero bytes on disk.  
                # If this error occurs check into replacing the zero byte file with the backup if it exists
                prettyoutput.LogErr(f"{XMLFilename} had zero size, did not backup on write")
        except FileNotFoundError:
            pass

        # prettyoutput.Log("Saving %s" % XMLFilename)
        # print OutputXML
        with open(XMLFilename, 'wb') as hFile:
            hFile.write(OutputXML)


class XNamedContainerElementWrapped(XContainerElementWrapper):
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
    def Create(cls, tag, Name, Path=None, attrib=None, **extra):
        if Path is None:
            Path = Name

        if attrib is None:
            attrib = {}

        obj = cls(tag=tag, Path=Path, Name=Name, attrib=attrib, **extra)

        return obj


class XLinkedContainerElementWrapper(XContainerElementWrapper):
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

        ValidateAttributesAreStrings(self, logger)

        # SaveTree = ElementTree.ElementTree(SaveElement)

        # Any child containers we create a link to and remove from our file
        for i in range(len(self) - 1, -1, -1):
            child = self[i]
            if child.tag.endswith('_Link'):
                SaveElement.append(child)
                continue

            if isinstance(child, XContainerElementWrapper) and child.SaveAsLinkedElement:
                LinkElement = XElementWrapper(child.tag + '_Link', attrib=child.attrib)
                # SaveElement.append(LinkElement)
                SaveElement.append(LinkElement)

                if recurse:
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
class VolumeNode(XNamedContainerElementWrapped):
    """The root of a volume's XML Meta-data"""

    @property
    def Blocks(self) -> [BlockNode]:
        return self.findall('Block')

    def GetBlock(self, name) -> BlockNode:
        return self.GetChildByAttrib('Block', 'Name', name)

    @property
    def NeedsValidation(self) -> bool:
        return True

    @classmethod
    def Create(cls, Name: str, Path: str = None, **extra):
        return super(VolumeNode, cls).Create(tag='Volume', Name=Name, Path=Path, **extra)


class BlockNode(XNamedContainerElementWrapped):

    @property
    def Sections(self) -> [SectionNode]:
        return self.findall('Section')

    @property
    def StosGroups(self) -> [StosGroupNode]:
        return self.findall('StosGroup')

    @property
    def StosMaps(self) -> [StosMapNode]:
        return self.findall('StosMap')

    def GetSection(self, Number: int) -> SectionNode:
        return self.GetChildByAttrib('Section', 'Number', Number)

    def GetOrCreateSection(self, Number: int) -> (bool, SectionNode):
        """
        :param Number: Section Number
        :return: (bool, SectionNode) True if a node was created.  The section node element.
        """
        section_obj = self.GetSection(Number)

        if section_obj is None:
            SectionName = ('%' + nornir_buildmanager.templates.Current.SectionFormat) % Number
            SectionPath = ('%' + nornir_buildmanager.templates.Current.SectionFormat) % Number

            section_obj = SectionNode.Create(Number,
                                             SectionName,
                                             SectionPath)
            return self.UpdateOrAddChildByAttrib(section_obj, 'Number')
        else:
            return False, section_obj

    def GetStosGroup(self, group_name: str, downsample) -> StosGroupNode | None:
        for stos_group in self.findall("StosGroup[@Name='%s']" % group_name):
            if stos_group.Downsample == downsample:
                return stos_group

        return None

    def GetOrCreateStosGroup(self, group_name: str, downsample) -> (bool, StosGroupNode):
        """:Return: Tuple of (created, stos_group)"""

        existing_stos_group = self.GetStosGroup(group_name, downsample)
        if existing_stos_group is not None:
            return False, existing_stos_group

        OutputStosGroupNode = StosGroupNode.Create(group_name, Downsample=downsample)
        self.append(OutputStosGroupNode)

        return True, OutputStosGroupNode

    def GetStosMap(self, map_name) -> StosMapNode:
        return self.GetChildByAttrib('StosMap', 'Name', map_name)

    def GetOrCreateStosMap(self, map_name) -> StosMapNode:
        stos_map_node = self.GetStosMap(map_name)
        if stos_map_node is None:
            stos_map_node = StosMapNode.Create(map_name)
            self.append(stos_map_node)
            return stos_map_node
        else:
            return stos_map_node

    def RemoveStosMap(self, map_name) -> bool:
        """:return: True if a map was found and removed"""
        stos_map_node = self.GetStosMap(map_name)
        if stos_map_node is not None:
            self.remove(stos_map_node)
            return True

        return False

    def RemoveStosGroup(self, group_name, downsample) -> bool:
        """:return: True if a StosGroup was found and removed"""
        existing_stos_group = self.GetStosGroup(group_name, downsample)
        if existing_stos_group is not None:
            self.remove(existing_stos_group)
            return True

        return False

    def MarkSectionsAsDamaged(self, section_number_list):
        """Add the sections in the list to the NonStosSectionNumbers"""
        if not isinstance(section_number_list, set) or isinstance(section_number_list, frozenset):
            section_number_list = frozenset(section_number_list)

        self.NonStosSectionNumbers = frozenset(section_number_list.union(self.NonStosSectionNumbers))

    def MarkSectionsAsUndamaged(self, section_number_list):
        if not isinstance(section_number_list, set) or isinstance(section_number_list, frozenset):
            section_number_list = frozenset(section_number_list)

        existing_set = self.NonStosSectionNumbers
        self.NonStosSectionNumbers = existing_set.difference(section_number_list)

    @property
    def NonStosSectionNumbers(self) -> [int]:
        """A list of integers indicating which section numbers should not be control sections for slice to slice registration"""
        StosExemptNode = XElementWrapper(tag='NonStosSectionNumbers')
        (added, StosExemptNode) = self.UpdateOrAddChild(StosExemptNode)

        # Fetch the list of the exempt nodes from the element text
        ExemptString = StosExemptNode.text

        if ExemptString is None or len(ExemptString) == 0:
            return frozenset([])

        # OK, parse the exempt string to a different list
        NonStosSectionNumbers = frozenset(sorted([int(x) for x in ExemptString.split(',')]))

        ##################
        # Temporary fix for old meta-data that was not sorted.  It can be
        # deleted after running an align on each legacy volume
        ExpectedText = BlockNode.NonStosNumbersToString(NonStosSectionNumbers)
        if ExpectedText != StosExemptNode.text:
            self.NonStosSectionNumbers = NonStosSectionNumbers
        ################

        return NonStosSectionNumbers

    @NonStosSectionNumbers.setter
    def NonStosSectionNumbers(self, value) -> [int]:
        """A list of integers indicating which section numbers should not be control sections for slice to slice registration"""
        StosExemptNode = XElementWrapper(tag='NonStosSectionNumbers')
        (added, StosExemptNode) = self.UpdateOrAddChild(StosExemptNode)

        ExpectedText = BlockNode.NonStosNumbersToString(value)
        if StosExemptNode.text != ExpectedText:
            StosExemptNode.text = ExpectedText
            StosExemptNode._AttributesChanged = True

    @staticmethod
    def NonStosNumbersToString(value) -> str:
        """Converts a string, integer, list, set, or frozen set to a comma
        delimited string"""
        if isinstance(value, str):
            return value
        elif isinstance(value, int):
            return str(value)
        elif isinstance(value, list):
            value.sort()
            return ','.join(list(map(str, value)))
        elif isinstance(value, set) or isinstance(value, frozenset):
            listValue = list(value)
            listValue.sort()
            return ','.join(list(map(str, listValue)))

        raise NotImplementedError()

    @property
    def NeedsValidation(self) -> bool:
        return True

    @classmethod
    def Create(cls, Name, Path=None, **extra) -> BlockNode:
        return super(BlockNode, cls).Create(tag='Block', Name=Name, Path=Path, **extra)


class ChannelNode(XNamedContainerElementWrapped):

    def __init__(self, **kwargs):
        super(ChannelNode, self).__init__(**kwargs)
        self._scale = None

    @property
    def Filters(self) -> [FilterNode]:
        return self.findall('Filter')

    def GetFilter(self, Filter: str) -> FilterNode | None:
        return self.GetChildByAttrib('Filter', 'Name', Filter)

    def HasFilter(self, FilterName: str) -> bool:
        return not self.GetFilter(FilterName) is None

    def GetOrCreateFilter(self, Name: str) -> (bool, FilterNode):
        (added, filterNode) = self.UpdateOrAddChildByAttrib(FilterNode.Create(Name), 'Name')
        return added, filterNode

    def MatchFilterPattern(self, filterPattern: str) -> [FilterNode]:
        return VMH.SearchCollection(self.Filters,
                                    'Name',
                                    filterPattern)

    def GetTransform(self, transform_name) -> TransformNode | None:
        return self.GetChildByAttrib('Transform', 'Name', transform_name)

    def RemoveFilterOnContrastMismatch(self, FilterName, MinIntensityCutoff, MaxIntensityCutoff, Gamma):
        """
        Return: true if filter found and removed
        """

        filter_node = self.GetFilter(Filter=FilterName)
        if filter_node is None:
            return False

        if filter_node.Locked:
            if filter_node.IsContrastMismatched(MinIntensityCutoff, MaxIntensityCutoff, Gamma):
                self.logger.warning("Locked filter cannot be removed for contrast mismatch. %s " % filter_node.FullPath)
                return False

        if filter_node.RemoveChildrenOnContrastMismatch(MinIntensityCutoff, MaxIntensityCutoff, Gamma):
            filter_node.Clean("Contrast mismatch")
            return True

        return False

    def RemoveFilterOnBppMismatch(self, FilterName, expected_bpp):
        """
        Return: true if filter found and removed
        """

        filter_node = self.GetFilter(Filter=FilterName)
        if filter_node is None:
            return False

        if filter_node.BitsPerPixel != expected_bpp:
            if filter_node.Locked:
                self.logger.warning(
                    "Locked filter cannot be removed for bits-per-pixel mismatch. %s " % filter_node.FullPath)
                return False
            else:
                filter_node.Clean(
                    "Filter's {0} bpp did not match expected {1} bits-per-pixel".format(filter_node.BitsPerPixel,
                                                                                        expected_bpp))
                return True

        return False

    @property
    def Scale(self):
        if hasattr(self, '_scale') is False:
            scaleNode = self.find('Scale')
            self._scale = Scale.Create(scaleNode) if scaleNode is not None else None

        return self._scale

    def GetScale(self):
        return self.Scale

    def SetScale(self, scaleValueInNm):
        """Create a scale node for the channel
        :return: ScaleNode object that was created"""
        # TODO: Scale should be its own object and a property

        [added, scaleNode] = self.UpdateOrAddChild(ScaleNode.Create())

        if isinstance(scaleValueInNm, float):
            scaleNode.UpdateOrAddChild(XElementWrapper('X', {'UnitsOfMeasure': 'nm',
                                                             'UnitsPerPixel': str(scaleValueInNm)}))
            scaleNode.UpdateOrAddChild(XElementWrapper('Y', {'UnitsOfMeasure': 'nm',
                                                             'UnitsPerPixel': str(scaleValueInNm)}))
        elif isinstance(scaleValueInNm, int):
            scaleNode.UpdateOrAddChild(XElementWrapper('X', {'UnitsOfMeasure': 'nm',
                                                             'UnitsPerPixel': str(scaleValueInNm)}))
            scaleNode.UpdateOrAddChild(XElementWrapper('Y', {'UnitsOfMeasure': 'nm',
                                                             'UnitsPerPixel': str(scaleValueInNm)}))
        elif isinstance(scaleValueInNm, ScaleAxis):
            scaleNode.UpdateOrAddChild(XElementWrapper('X', {'UnitsOfMeasure': str(scaleValueInNm.UnitsOfMeasure),
                                                             'UnitsPerPixel': str(scaleValueInNm.UnitsPerPixel)}))
            scaleNode.UpdateOrAddChild(XElementWrapper('Y', {'UnitsOfMeasure': str(scaleValueInNm.UnitsOfMeasure),
                                                             'UnitsPerPixel': str(scaleValueInNm.UnitsPerPixel)}))
        elif isinstance(scaleValueInNm, Scale):
            (added, scaleNode) = self.UpdateOrAddChild(ScaleNode.CreateFromScale(scaleValueInNm))
        else:
            raise NotImplementedError("Unknown type %s" % scaleValueInNm)

        self._scale = Scale.Create(scaleNode)

        return added, scaleNode

    @property
    def NeedsValidation(self):
        return True

    def __str__(self):
        return "Channel: %s Section: %d" % (self.Name, self.Parent.Number)

    @classmethod
    def Create(cls, Name, Path=None, **extra):
        return super(ChannelNode, cls).Create(tag='Channel', Name=Name, Path=Path, **extra)


class ScaleNode(XElementWrapper):

    @property
    def X(self):
        x_elem = ElementTree.Element.find(self,
                                          'X')  # Bypass the extra cruft in XElementTree since scale uses XML with no link loading or special wrapping of elements

        if x_elem is None:
            return None

        return ScaleAxis(x_elem.attrib['UnitsPerPixel'], x_elem.attrib['UnitsOfMeasure'])

    @property
    def Y(self):
        y_elem = ElementTree.Element.find(self,
                                          'Y')  # Bypass the extra cruft in XElementTree since scale uses XML with no link loading or special wrapping of elements

        if y_elem is None:
            return None

        return ScaleAxis(y_elem.attrib['UnitsPerPixel'], y_elem.attrib['UnitsOfMeasure'])

    @property
    def Z(self):
        z_elem = ElementTree.Element.find(self,
                                          'Z')  # Bypass the extra cruft in XElementTree since scale uses XML with no link loading or special wrapping of elements

        if z_elem is None:
            return None

        return ScaleAxis(z_elem.attrib['UnitsPerPixel'], z_elem.attrib['UnitsOfMeasure'])

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Scale'

        super(ScaleNode, self).__init__(tag=tag, attrib=attrib, **extra)

    def __str__(self):
        if self.Z is not None:
            return "X:{0} Y:{1} Z:{2}".format(str(self.X), str(self.Y), str(self.Z))
        else:
            return "X:{0} Y:{1}".format(str(self.X), str(self.Y))

    @classmethod
    def Create(cls, **extra):
        return cls(**extra)

    @classmethod
    def CreateFromScale(cls, scale):
        """Create a ScaleNode from a Scale object"""
        if isinstance(scale, Scale) is False:
            raise NotImplementedError('CreateFromScale got unexpected parameter: %s' % str(scale))

        output = ScaleNode()
        output.UpdateOrAddChild(XElementWrapper('X', {'UnitsOfMeasure': scale.X.UnitsOfMeasure,
                                                      'UnitsPerPixel': str(scale.X.UnitsPerPixel)}))
        output.UpdateOrAddChild(XElementWrapper('Y', {'UnitsOfMeasure': scale.Y.UnitsOfMeasure,
                                                      'UnitsPerPixel': str(scale.Y.UnitsPerPixel)}))

        if output.Z is not None:
            output.UpdateOrAddChild(XElementWrapper('Z', {'UnitsOfMeasure': scale.Z.UnitsOfMeasure,
                                                          'UnitsPerPixel': str(scale.Z.UnitsPerPixel)}))

        return output


class Scale(object):
    """
    A 2/3 dimensional representation of scale.
    """

    @property
    def X(self):
        return self._x

    @X.setter
    def X(self, val):
        if isinstance(val, float):
            self._x = ScaleAxis(val, 'nm')
        elif isinstance(val, ScaleAxis):
            self._x = val
        else:
            raise NotImplementedError('Unknown type passed to Scale setter %s' % val)

    @property
    def Y(self):
        return self._y

    @Y.setter
    def Y(self, val):
        if isinstance(val, float):
            self._y = ScaleAxis(val, 'nm')
        elif isinstance(val, ScaleAxis):
            self._y = val
        else:
            raise NotImplementedError('Unknown type passed to Scale setter %s' % val)

    @property
    def Z(self):
        return self._z

    @Z.setter
    def Z(self, val):
        if isinstance(val, float):
            self._z = ScaleAxis(val, 'nm')
        elif isinstance(val, ScaleAxis):
            self._z = val
        else:
            raise NotImplementedError('Unknown type passed to Scale setter %s' % val)

    @staticmethod
    def Create(ScaleData):
        """Create a Scale object from various input types"""
        if isinstance(ScaleData, ScaleNode):
            obj = Scale(ScaleData.X, ScaleData.Y, ScaleData.Z)
            return obj
        else:
            raise NotImplementedError("Unexpected type passed to Scale.Create %s" % ScaleData)

    def __truediv__(self, scalar):
        if not isinstance(scalar, float):
            raise NotImplementedError("Division for non-floating types is not supported")

        obj = Scale(self.X / scalar,
                    self.Y / scalar,
                    self.Z / scalar if self.Z is not None else None)  # Only pass Z if it is not None
        return obj

    def __mul__(self, scalar):
        if not isinstance(scalar, float):
            raise NotImplementedError("Division for non-floating types is not supported")

        obj = Scale(self.X * scalar,
                    self.Y * scalar,
                    self.Z * scalar if self.Z is not None else None)  # Only pass Z if it is not None
        return obj

    def __init__(self, X, Y=None, Z=None):
        """
        If only X is passed we assume the scale for X&Y are identical and there is no Z
        The only way to specify a Z axis scale is to pass it to the constructor
        """
        self._x = None
        self._y = None
        self._z = None

        self.X = X

        if Y is None:
            self.Y = X
        else:
            self.Y = Y

        if Z is not None:
            self.Z = Z

    def __str__(self):
        if self.Z is not None:
            return "X:{0} Y:{1} Z:{2}".format(str(self.X), str(self.Y), str(self.Z))
        else:
            return "X:{0} Y:{1}".format(str(self.X), str(self.Y))


class ScaleAxis(object):

    def __init__(self, UnitsPerPixel, UnitsOfMeasure):
        self.UnitsPerPixel = float(UnitsPerPixel)
        self.UnitsOfMeasure = str(UnitsOfMeasure)

    def __truediv__(self, scalar):
        if isinstance(scalar, float):
            return ScaleAxis(self.UnitsPerPixel / scalar, self.UnitsOfMeasure)
        elif isinstance(scalar, ScaleAxis):
            if self.UnitsOfMeasure != scalar.UnitsOfMeasure:
                raise NotImplementedError("Cannot divide ScaleAxis objects if UnitsOfMeasure do not match")

            return self.UnitsPerPixel / scalar.UnitsPerPixel  # both inputs have units so the units cancel

        raise NotImplementedError("Division for input type is not supported: %s" % scalar)

    def __eq__(self, other):
        if other is None:
            return False

        return other.UnitsPerPixel == self.UnitsPerPixel and other.UnitsOfMeasure == self.UnitsOfMeasure

    def __mul__(self, scalar):
        if isinstance(scalar, float):
            return ScaleAxis(self.UnitsPerPixel * scalar, self.UnitsOfMeasure)
        elif isinstance(scalar, ScaleAxis):
            if self.UnitsOfMeasure != scalar.UnitsOfMeasure:
                raise NotImplementedError("Cannot multiply ScaleAxis objects if UnitsOfMeasure do not match")

            return self.UnitsPerPixel * scalar.UnitsPerPixel  # both inputs have units so the units cancel

        raise NotImplementedError("Multiplication for input type is not supported: %s" % scalar)

    def __str__(self):
        return "{0}{1}".format(str(self.UnitsPerPixel), self.UnitsOfMeasure)


def BuildFilterImageName(SectionNumber: int, ChannelName: str, FilterName: str, Extension=None) -> str:
    return nornir_buildmanager.templates.Current.SectionTemplate % SectionNumber + \
           f"_{ChannelName}_{FilterName}{Extension}"


class FilterNode(XNamedContainerElementWrapped, VMH.ContrastHandler):
    DefaultMaskName = "Mask"

    def DefaultImageName(self, extension: str) -> str:
        """Default name for an image in this filters imageset"""
        InputChannelNode = self.FindParent('Channel')
        section_node = InputChannelNode.FindParent('Section')  # type: SectionNode
        return BuildFilterImageName(section_node.Number, InputChannelNode.Name, self.Name, extension)

    @property
    def Scale(self) -> float | None:
        """Returns the scale if it is specified in a parent Channel Node"""
        channelNode = self.FindParent('Channel')
        if channelNode is None:
            return None

        return channelNode.Scale

    @property
    def Histogram(self):
        """Get the image set for the filter, create if missing"""

        # imageset = self.GetChildByAttrib('ImageSet', 'Name', ImageSetNode.Name)
        # There should be only one Imageset, so use find
        histogram = self.find('Histogram')
        if histogram is None:
            raise NotImplementedError("Creation of missing histogram node is deprecated")
            # histogram = HistogramNode.Create(InputTransformNode=None)
            # self.append(histogram)

        return histogram

    @property
    def BitsPerPixel(self) -> int | None:
        val = self.attrib.get('BitsPerPixel', None)
        if val is not None:
            val = int(val)

        return val

    @BitsPerPixel.setter
    def BitsPerPixel(self, val):
        if val is None:
            if 'BitsPerPixel' in self.attrib:
                del self.attrib['BitsPerPixel']
        else:
            self.attrib['BitsPerPixel'] = '%d' % val

    def GetOrCreateTilePyramid(self) -> (bool, TilePyramidNode):
        # pyramid = self.GetChildByAttrib('TilePyramid', "Name", TilePyramidNode.Name)
        # There should be only one Imageset, so use find
        pyramid = self.find('TilePyramid')
        if pyramid is None:
            pyramid = TilePyramidNode.Create(NumberOfTiles=0)
            self.append(pyramid)
            return True, pyramid
        else:
            return False, pyramid

    @property
    def TilePyramid(self) -> TilePyramidNode:
        # pyramid = self.GetChildByAttrib('TilePyramid', "Name", TilePyramidNode.Name)
        # There should be only one Imageset, so use find
        pyramid = self.find('TilePyramid')
        if pyramid is None:
            pyramid = TilePyramidNode.Create(NumberOfTiles=0)
            self.append(pyramid)

        return pyramid

    @property
    def HasTilePyramid(self) -> bool:
        return not self.find('TilePyramid') is None

    @property
    def HasImageset(self) -> bool:
        return not self.find('ImageSet') is None

    @property
    def HasTileset(self) -> bool:
        return not self.find('Tileset') is None

    @property
    def Tileset(self) -> TilesetNode | None:
        """Get the tileset for the filter, create if missing"""
        # imageset = self.GetChildByAttrib('ImageSet', 'Name', ImageSetNode.Name)
        # There should be only one Imageset, so use find
        tileset = self.find('Tileset')
        return tileset

    @property
    def Imageset(self) -> ImageSetNode:
        """Get the imageset for the filter, create if missing"""
        # imageset = self.GetChildByAttrib('ImageSet', 'Name', ImageSetNode.Name)
        # There should be only one Imageset, so use find
        imageset = self.find('ImageSet')
        if imageset is None:
            imageset = ImageSetNode.Create()
            self.append(imageset)

        return imageset

    @property
    def MaskImageset(self) -> ImageSetNode | None:
        """Get the imageset for the default mask"""

        maskFilter = self.GetMaskFilter()
        if maskFilter is None:
            return None

        return maskFilter.Imageset

    @property
    def MaskName(self) -> str | None:
        """The default mask to use for this filter"""
        m = self.attrib.get("MaskName", None)
        if m is not None:
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

    def GetOrCreateMaskName(self) -> str:
        """Returns the maskname for the filter, if it does not exist use the default mask name"""
        if self.MaskName is None:
            self.MaskName = FilterNode.DefaultMaskName

        return self.MaskName

    @property
    def HasMask(self) -> bool:
        """
        :return: True if the mask filter exists
        """
        return not self.GetMaskFilter() is None

    def GetMaskFilter(self, MaskName: str = None) -> FilterNode | None:
        if MaskName is None:
            MaskName = self.MaskName

        if MaskName is None:
            return None

        assert (isinstance(MaskName, str))

        return self.Parent.GetFilter(MaskName)

    def GetOrCreateMaskFilter(self, MaskName: str = None) -> FilterNode:
        if MaskName is None:
            MaskName = self.GetOrCreateMaskName()

        assert (isinstance(MaskName, str))

        return self.Parent.GetOrCreateFilter(MaskName)

    def GetImage(self, Downsample) -> ImageNode | None:
        if not self.HasImageset:
            return None

        return self.Imageset.GetImage(Downsample)

    def GetOrCreateImage(self, Downsample) -> ImageNode:
        """As described, raises a nornir_buildmanager.NornirUserException if the image cannot be generated"""
        imageset = self.Imageset
        return imageset.GetOrCreateImage(Downsample)

    def GetMaskImage(self, Downsample) -> ImageNode | None:
        maskFilter = self.GetMaskFilter()
        if maskFilter is None:
            return None

        return maskFilter.GetImage(Downsample)

    def GetOrCreateMaskImage(self, Downsample) -> ImageNode:
        """As described, raises a nornir_buildmanager.NornirUserException if the image cannot be generated"""
        (added_mask_filter, maskFilter) = self.GetOrCreateMaskFilter()
        return maskFilter.GetOrCreateImage(Downsample)

    def GetHistogram(self) -> HistogramNode:
        return self.find('Histogram')

    @property
    def NeedsValidation(self) -> bool:
        return True

    @classmethod
    def Create(cls, Name: str, Path: str = None, **extra):
        return super(FilterNode, cls).Create(tag='Filter', Name=Name, Path=Path, **extra)

    def _LogContrastMismatch(self, MinIntensityCutoff, MaxIntensityCutoff, Gamma):
        XElementWrapper.logger.warning("\tCurrent values (%g,%g,%g), target (%g,%g,%g)" % (
            self.MinIntensityCutoff, self.MaxIntensityCutoff, self.Gamma, MinIntensityCutoff, MaxIntensityCutoff,
            Gamma))


class NotesNode(XResourceElementWrapper):

    @classmethod
    def Create(cls, Text: str = None, SourceFilename: str = None, attrib: dict = None, **extra):
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

    def CleanIfInvalid(self):
        return False


class SectionNode(XNamedContainerElementWrapped):

    @classmethod
    def ClassSortKey(cls, self):
        """Required for objects derived from XContainerElementWrapper"""
        return "Section " + (nornir_buildmanager.templates.Current.SectionTemplate % int(self.Number))

    @property
    def SortKey(self):
        """The default key used for sorting elements"""
        return SectionNode.ClassSortKey(self)

    @property
    def Channels(self) -> [ChannelNode]:
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

    def MatchChannelPattern(self, channelPattern) -> [ChannelNode]:
        return VMH.SearchCollection(self.Channels,
                                    'Name',
                                    channelPattern)

    def MatchChannelFilterPattern(self, channelPattern, filterPattern) -> [ChannelNode]:
        filterNodes = []
        for channelNode in self.MatchChannelPattern(channelPattern):
            result = channelNode.MatchFilterPattern(filterPattern)
            if result is not None:
                filterNodes.extend(result)

        return filterNodes

    @property
    def NeedsValidation(self) -> bool:
        return True

    @classmethod
    def Create(cls, Number, Name=None, Path=None, attrib=None, **extra):

        if Name is None:
            Name = nornir_buildmanager.templates.Current.SectionTemplate % Number

        if Path is None:
            Path = nornir_buildmanager.templates.Current.SectionTemplate % Number

        obj = super(SectionNode, cls).Create(tag='Section', Name=Name, Path=Path, attrib=attrib, **extra)
        obj.Number = Number

        return obj


class StosGroupNode(XNamedContainerElementWrapped):

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'StosGroup'

        super(StosGroupNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, Name, Downsample, **extra):
        Path = Name

        obj = super(StosGroupNode, cls).Create(tag='StosGroup', Name=Name, attrib=None, Path=Path, **extra)
        obj.Downsample = Downsample
        return obj

    @property
    def Downsample(self) -> float:
        return float(self.attrib.get('Downsample', 'NaN'))

    @Downsample.setter
    def Downsample(self, val):
        """The default key used for sorting elements"""
        self.attrib['Downsample'] = '%g' % val

    @property
    def ManualInputDirectory(self) -> str:
        """Directory that manual override stos files are placed in"""
        return os.path.join(self.FullPath, 'Manual')

    def CreateDirectories(self):
        """Ensures the manual input directory exists"""
        os.makedirs(self.FullPath, exist_ok=True)
        os.makedirs(self.ManualInputDirectory, exist_ok=True)

    def PathToManualTransform(self, InputTransformFullPath):
        """Check the manual directory for the existence of a user-supplied file we should use.
           Returns the path to the file if it exists, otherwise None"""

        transform_filename = os.path.basename(InputTransformFullPath)
        # Copy the input stos or converted stos to the input directory
        ManualInputStosFullPath = os.path.join(self.ManualInputDirectory, transform_filename)
        if os.path.exists(ManualInputStosFullPath):
            return ManualInputStosFullPath

        return None

    @property
    def SectionMappings(self) -> [SectionMappingsNode]:
        return list(self.findall('SectionMappings'))

    def GetSectionMapping(self, MappedSectionNumber) -> SectionMappingsNode:
        return self.GetChildByAttrib('SectionMappings', 'MappedSectionNumber', MappedSectionNumber)

    def GetOrCreateSectionMapping(self, MappedSectionNumber) -> (bool, SectionMappingsNode):
        (added, sectionMappings) = self.UpdateOrAddChildByAttrib(
            SectionMappingsNode.Create(MappedSectionNumber=MappedSectionNumber), 'MappedSectionNumber')
        return added, sectionMappings

    def TransformsForMapping(self, MappedSectionNumber:int, ControlSectionNumber:int) -> [TransformNode]:
        sectionMapping = self.GetSectionMapping(MappedSectionNumber)
        if sectionMapping is None:
            return []

        return sectionMapping.TransformsToSection(ControlSectionNumber)

    @property
    def NeedsValidation(self) -> bool:
        return True

    def GetStosTransformNode(self, ControlFilter: FilterNode, MappedFilter: FilterNode) -> TransformNode | None:
        MappedSectionNode = MappedFilter.FindParent("Section")
        MappedChannelNode = MappedFilter.FindParent("Channel")
        ControlSectionNode = ControlFilter.FindParent("Section")
        ControlChannelNode = ControlFilter.FindParent("Channel")

        section_mappings_node = self.GetSectionMapping(MappedSectionNode.Number)
        if section_mappings_node is None:
            return None

        # assert(not SectionMappingsNode is None) #We expect the caller to arrange for a section mappings node in advance

        stosNode = section_mappings_node.FindStosTransform(ControlSectionNode.Number,
                                                         ControlChannelNode.Name,
                                                         ControlFilter.Name,
                                                         MappedSectionNode.Number,
                                                         MappedChannelNode.Name,
                                                         MappedFilter.Name)

        return stosNode

    def GetOrCreateStosTransformNode(self, ControlFilter, MappedFilter, OutputType, OutputPath) -> (
    bool, TransformNode):
        added = False
        stosNode = self.GetStosTransformNode(ControlFilter, MappedFilter)

        if stosNode is None:
            added = True
            stosNode = self.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType, OutputPath)
        else:
            self.__LegacyUpdateStosNode(stosNode, ControlFilter, MappedFilter, OutputPath)

        return added, stosNode

    def AddChecksumsToStos(self, stosNode, ControlFilter, MappedFilter):

        stosNode._AttributesChanged = True
        if MappedFilter.Imageset.HasImage(self.Downsample) or MappedFilter.Imageset.CanGenerate(self.Downsample):
            stosNode.attrib['MappedImageChecksum'] = MappedFilter.Imageset.GetOrCreateImage(self.Downsample).Checksum
        else:
            stosNode.attrib['MappedImageChecksum'] = ""

        if ControlFilter.Imageset.HasImage(self.Downsample) or ControlFilter.Imageset.CanGenerate(self.Downsample):
            stosNode.attrib['ControlImageChecksum'] = ControlFilter.Imageset.GetOrCreateImage(self.Downsample).Checksum
        else:
            stosNode.attrib['ControlImageChecksum'] = ""

        if MappedFilter.HasMask and ControlFilter.HasMask:
            if MappedFilter.MaskImageset.HasImage(self.Downsample) or MappedFilter.MaskImageset.CanGenerate(
                    self.Downsample):
                stosNode.attrib['MappedMaskImageChecksum'] = MappedFilter.MaskImageset.GetOrCreateImage(
                    self.Downsample).Checksum
            else:
                stosNode.attrib['MappedMaskImageChecksum'] = ""

            if ControlFilter.MaskImageset.HasImage(self.Downsample) or ControlFilter.MaskImageset.CanGenerate(
                    self.Downsample):
                stosNode.attrib['ControlMaskImageChecksum'] = ControlFilter.MaskImageset.GetOrCreateImage(
                    self.Downsample).Checksum
            else:
                stosNode.attrib['ControlMaskImageChecksum'] = ""

    def CreateStosTransformNode(self, ControlFilter: FilterNode, MappedFilter: FilterNode, OutputType: str,
                                OutputPath: str):
        """
        :param FilterNode ControlFilter: Filter for control image
        :param FilterNode MappedFilter: Filter for mapped image
        :param str OutputType: Type of stosNode
        :Param str OutputPath: Full path to .stos file
        """

        MappedSectionNode = MappedFilter.FindParent("Section")
        MappedChannelNode = MappedFilter.FindParent("Channel")
        ControlSectionNode = ControlFilter.FindParent("Section")
        ControlChannelNode = ControlFilter.FindParent("Channel")

        section_mappings_node = self.GetSectionMapping(MappedSectionNode.Number)
        assert (
                    section_mappings_node is not None)  # We expect the caller to arrange for a section mappings node in advance

        stosNode = TransformNode.Create(str(ControlSectionNode.Number), OutputType, OutputPath,
                                        {'ControlSectionNumber': str(ControlSectionNode.Number),
                                         'MappedSectionNumber': str(MappedSectionNode.Number),
                                         'MappedChannelName': str(MappedChannelNode.Name),
                                         'MappedFilterName': str(MappedFilter.Name),
                                         'ControlChannelName': str(ControlChannelNode.Name),
                                         'ControlFilterName': str(ControlFilter.Name)})

        self.AddChecksumsToStos(stosNode, ControlFilter, MappedFilter)
        #        WORKAROUND: The etree implementation has a serious shortcoming in that it cannot handle the 'and' operator in XPath queries.
        #        (added, stosNode) = SectionMappingsNode.UpdateOrAddChildByAttrib(stosNode, ['ControlSectionNumber',
        #                                                                                    'ControlChannelName',
        #                                                                                    'ControlFilterName',
        #                                                                                    'MappedSectionNumber',
        #                                                                                    'MappedChannelName',
        #                                                                                    'MappedFilterName'])

        section_mappings_node.append(stosNode)

        return stosNode

    @classmethod
    def GenerateStosFilename(cls, ControlFilter, MappedFilter):

        ControlSectionNode = ControlFilter.FindParent('Section')
        MappedSectionNode = MappedFilter.FindParent('Section')

        OutputFile = str(MappedSectionNode.Number) + '-' + str(ControlSectionNode.Number) + \
                     '_ctrl-' + ControlFilter.Parent.Name + "_" + ControlFilter.Name + \
                     '_map-' + MappedFilter.Parent.Name + "_" + MappedFilter.Name + '.stos'
        return OutputFile

    @classmethod
    def _IsStosInputImageOutdated(cls, stosNode, ChecksumAttribName, imageNode):
        """
        :param TransformNode stosNode: Stos Transform Node to test
        :param str ChecksumAttribName: Name of attribute with checksum value on image node
        :param ImageNode imageNode: Image node to test
        """

        if imageNode is None:
            return True

        IsInvalid = False

        if len(stosNode.attrib.get(ChecksumAttribName, "")) > 0:
            IsInvalid = IsInvalid or not nornir_buildmanager.validation.transforms.IsValueMatched(stosNode,
                                                                                                  ChecksumAttribName,
                                                                                                  imageNode.Checksum)
        else:
            if not os.path.exists(imageNode.FullPath):
                IsInvalid = IsInvalid or True
            else:
                IsInvalid = IsInvalid or nornir_shared.files.IsOutdated(imageNode.FullPath, stosNode.FullPath)

        return IsInvalid

    def AreStosInputImagesOutdated(self,
                                   stosNode: TransformNode,
                                   ControlFilter: FilterNode,
                                   MappedFilter: FilterNode,
                                   MaskRequired: bool) -> bool:
        """
        :param TransformNode stosNode: Stos Transform Node to test
        :param FilterNode ControlFilter: Filter for control image
        :param FilterNode MappedFilter: Filter for mapped image
        :param bool MaskRequired: Require the use of masks
        """

        if stosNode is None or ControlFilter is None or MappedFilter is None:
            return True

        ControlImageNode = None
        MappedImageNode = None
        try:
            ControlImageNode = ControlFilter.GetOrCreateImage(self.Downsample)
            MappedImageNode = MappedFilter.GetOrCreateImage(self.Downsample)
        except nornir_buildmanager.NornirUserException as e:
            logger = logging.getLogger(__name__ + '.' + 'AreStosInputImagesOutdated')
            logger.warning(
                "Reporting .stos file {0} is outdated after exception raised when finding images:\n{0}".format(
                    stosNode.FullPath, str(e)))
            prettyoutput.LogErr(
                "Reporting {0} is outdated after exception raised when finding images:\n{0}".format(stosNode.FullPath,
                                                                                                    str(e)))
            return True

        is_invalid = False

        is_invalid = is_invalid or StosGroupNode._IsStosInputImageOutdated(stosNode,
                                                                         ChecksumAttribName='ControlImageChecksum',
                                                                         imageNode=ControlImageNode)
        is_invalid = is_invalid or StosGroupNode._IsStosInputImageOutdated(stosNode,
                                                                         ChecksumAttribName='MappedImageChecksum',
                                                                         imageNode=MappedImageNode)

        if MaskRequired:
            ControlMaskImageNode = ControlFilter.GetMaskImage(self.Downsample)
            MappedMaskImageNode = MappedFilter.GetMaskImage(self.Downsample)
            is_invalid = is_invalid or StosGroupNode._IsStosInputImageOutdated(stosNode,
                                                                             ChecksumAttribName='ControlMaskImageChecksum',
                                                                             imageNode=ControlMaskImageNode)
            is_invalid = is_invalid or StosGroupNode._IsStosInputImageOutdated(stosNode,
                                                                             ChecksumAttribName='MappedMaskImageChecksum',
                                                                             imageNode=MappedMaskImageNode)

        return is_invalid

    @classmethod
    def __LegacyUpdateStosNode(cls, stosNode, ControlFilter, MappedFilter, OutputPath):

        if stosNode is None:
            return

        if not hasattr(stosNode, "ControlChannelName") or not hasattr(stosNode, "MappedChannelName"):
            MappedChannelNode = MappedFilter.FindParent("Channel")
            ControlChannelNode = ControlFilter.FindParent("Channel")

            renamedPath = os.path.join(os.path.dirname(stosNode.FullPath), stosNode.Path)
            XElementWrapper.logger.warning("Renaming stos transform for backwards compatability")
            XElementWrapper.logger.warning(renamedPath + " -> " + stosNode.FullPath)
            shutil.move(renamedPath, stosNode.FullPath)
            stosNode.Path = OutputPath
            stosNode.MappedChannelName = MappedChannelNode.Name
            stosNode.MappedFilterName = MappedFilter.Name
            stosNode.ControlChannelName = ControlChannelNode.Name
            stosNode.ControlFilterName = ControlFilter.Name
            stosNode.ControlImageChecksum = str(ControlFilter.Imageset.Checksum)
            stosNode.MappedImageChecksum = str(MappedFilter.Imageset.Checksum)

    @property
    def SummaryString(self) -> str:
        """
            :return: Name of the group and the downsample level
            :rtype str:
        """
        return "{0:s} {1:3d}".format(self.Name.ljust(20), int(self.Downsample))

    def CleanIfInvalid(self) -> (bool, str):
        cleaned = super(StosGroupNode, self).CleanIfInvalid()

        # TODO: Deleting stale transforms and section mappinds needs to be enabled, but I identified this shortcoming in a remote and 
        # want to work on it in my own test environment
        # if not cleaned:    
        # for mapping in self.SectionMappings:
        # cleaned or mapping.CleanIfInvalid()

        return cleaned


class StosMapNode(XElementWrapper):

    @property
    def Name(self) -> str:
        return self.get('Name', '')

    @Name.setter
    def Name(self, Value):
        self.attrib['Name'] = Value

    @property
    def Type(self) -> str:
        """Type of Stos Map"""
        return self.attrib.get("Type", None)

    @Type.setter
    def Type(self, val):
        if val is None:
            if 'Type' in self.attrib:
                del self.attrib['Type']
        else:
            self.attrib['Type'] = val

    @property
    def CenterSection(self) -> int | None:
        if 'CenterSection' in self.attrib:
            val = self.attrib.get('CenterSection', None)
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
            assert (isinstance(val, int))
            self.attrib['CenterSection'] = str(val)

    @property
    def Mappings(self) -> [MappingNode]:
        return list(self.findall('Mapping'))

    def MappedToControls(self) -> {int: [int]}:
        """Return dictionary of possible control sections for a given mapped section number"""
        MappedToControlCandidateList = {}
        for mappingNode in self.Mappings:
            for mappedSection in mappingNode.Mapped:
                if mappedSection in MappedToControlCandidateList:
                    MappedToControlCandidateList[mappedSection].append(mappingNode.Control)
                else:
                    MappedToControlCandidateList[mappedSection] = [mappingNode.Control]

        return MappedToControlCandidateList

    def GetMappingsForControl(self, Control) -> [MappingNode]:
        mappings = self.findall("Mapping[@Control='" + str(Control) + "']")
        if mappings is None:
            return []

        return list(mappings)

    def ClearBannedControlMappings(self, NonStosSectionNumbers):
        """Remove any control sections from a mapping which cannot be a control"""

        removed = False
        for InvalidControlSection in NonStosSectionNumbers:
            mapNodes = self.GetMappingsForControl(InvalidControlSection)
            for mapNode in mapNodes:
                removed = True
                self.remove(mapNode)

        return removed

    @property
    def AllowDuplicates(self) -> bool:
        return bool(self.attrib.get('AllowDuplicates', True))

    @classmethod
    def _SectionNumberFromParameter(cls, input_value):
        val = None
        if isinstance(input_value, nornir_imageregistration.transforms.registrationtree.RegistrationTreeNode):
            val = input_value.SectionNumber
        elif isinstance(input_value, int):
            val = input_value
        else:
            raise TypeError("Section Number parameter should be an integer or RegistrationTreeNode")

        return val

    def AddMapping(self, control: int, mapped: int):
        """
        Creates a mapping to a control section by Add/Update a <Mapping> element
        :param int control: Control section number
        :param int mapped: Mapped section number
        """

        val = StosMapNode._SectionNumberFromParameter(mapped)
        control = StosMapNode._SectionNumberFromParameter(control)

        child_mapping = self.GetChildByAttrib('Mapping', 'Control', control)
        if child_mapping is None:
            child_mapping = MappingNode.Create(control, val)
            self.append(child_mapping)
        else:
            if val not in child_mapping.Mapped:
                child_mapping.AddMapping(val)
        return

    def RemoveMapping(self, Control, Mapped):
        """Remove a mapping
        :param int Control: Control section number
        :param int Mapped: Mapped section number

        :return: True if mapped section is found and removed
        """

        Mapped = StosMapNode._SectionNumberFromParameter(Mapped)
        Control = StosMapNode._SectionNumberFromParameter(Control)

        childMapping = self.GetChildByAttrib('Mapping', 'Control', Control)
        if childMapping is not None:
            if Mapped in childMapping.Mapped:
                childMapping.RemoveMapping(Mapped)

                if len(childMapping.Mapped) == 0:
                    self.remove(childMapping)

                return True

        return False

    def FindAllControlsForMapped(self, MappedSection):
        """Given a section to be mapped, return the first control section found"""
        for m in self.findall('Mapping'):

            if MappedSection in m.Mapped:
                yield m.Control

        return

    def RemoveDuplicateControlEntries(self, Control):
        """If there are two entries with the same control number we merge the mapping list and delete the duplicate"""

        mappings = list(self.GetMappingsForControl(Control))
        if len(mappings) < 2:
            return False

        mergeMapping = mappings[0]
        for i in range(1, len(mappings)):
            mappingNode = mappings[i]
            for mappedSection in mappingNode.Mapped:
                mergeMapping.AddMapping(mappedSection)
                XElementWrapper.logger.warning('Moving duplicate mapping ' + str(Control) + ' <- ' + str(mappedSection))

            self.remove(mappingNode)

        return True

    @property
    def NeedsValidation(self):
        return True  # Checking the mapping is easier than checking if volumedata.xml has changed

    def IsValid(self) -> (bool, str):
        """Check for mappings whose control section is in the non-stos section numbers list"""

        if not hasattr(self, 'Parent'):
            return super(StosMapNode, self).IsValid()

        NonStosSectionsNode = self.Parent.find('NonStosSectionNumbers')

        AlreadyMappedSections = []

        if NonStosSectionsNode is None:
            return super(StosMapNode, self).IsValid()

        NonStosSections = misc.ListFromAttribute(NonStosSectionsNode.text)

        MappingNodes = list(self.findall('Mapping'))

        for i in range(len(MappingNodes) - 1, -1, -1):
            mapping_node = MappingNodes[i]
            self.RemoveDuplicateControlEntries(mapping_node.Control)

        MappingNodes = list(self.findall('Mapping'))

        for i in range(len(MappingNodes) - 1, -1, -1):
            mapping_node = MappingNodes[i]

            if mapping_node.Control in NonStosSections:
                mapping_node.Clean()
                XElementWrapper.logger.warning('Mappings for control section ' + str(
                    mapping_node.Control) + ' removed due to existence in NonStosSectionNumbers element')
            else:
                mapped_sections = mapping_node.Mapped
                for isection in range(len(mapped_sections) - 1, -1, -1):
                    mapped_section = mapped_sections[isection]
                    if mapped_section in AlreadyMappedSections and not self.AllowDuplicates:
                        del mapped_sections[isection]
                        XElementWrapper.logger.warning(
                            f'Removing duplicate mapping {mapped_section} -> {mapping_node.Control}')
                    else:
                        AlreadyMappedSections.append(mapped_section)

                if len(mapped_sections) == 0:
                    mapping_node.Clean()
                    XElementWrapper.logger.warning(
                        'No mappings remain for control section ' + str(mapping_node.Control))
                elif len(mapped_sections) != mapping_node.Mapped:
                    mapping_node.Mapped = mapped_sections

        return super(StosMapNode, self).IsValid()

    @classmethod
    def Create(cls, Name, attrib=None, **extra):
        obj = StosMapNode(tag='StosMap', Name=Name, attrib=attrib, **extra)
        return obj


class MappingNode(XElementWrapper):


    @property
    def SortKey(self):
        """The default key used for sorting elements"""
        return self.Control  # self.tag + ' ' + (nornir_buildmanager.templates.Current.SectionTemplate % self.Control)

    @property
    def Control(self) -> int | None:
        if 'Control' in self.attrib:
            return int(self.attrib['Control'])

        return None

    @property
    def Mapped(self) -> int:
        if self._mapped_cache is None:
            mappedList = misc.ListFromAttribute(self.attrib.get('Mapped', []))
            mappedList.sort()
            self._mapped_cache = mappedList

        return self._mapped_cache

    @Mapped.setter
    def Mapped(self, value):
        AdjacentSectionString = ''
        if isinstance(value, list):
            value.sort()
            AdjacentSectionString = ','.join(str(x) for x in value)
        else:
            assert (isinstance(value, int))
            AdjacentSectionString = str(value)

        self.attrib['Mapped'] = AdjacentSectionString
        self._mapped_cache = None

    def AddMapping(self, value: int):
        intval = int(value)
        updated_map = list(self.Mapped)
        if intval in updated_map:
            return
        else:
            updated_map.append(value)
            self.Mapped = updated_map
            # self._AttributeChanged = True #Handled by setattr of Mapped 

    def RemoveMapping(self, value: int):
        intval = int(value)
        updated_map = list(self.Mapped)
        if intval not in updated_map:
            return

        updated_map.remove(intval)
        self.Mappings = updated_map
        # self._AttributeChanged = True #Handled by setattr of Mapped

    def __str__(self):
        self._mapped_cache = None
        return "%d <- %s" % (self.Control, str(self.Mapped))

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Mapping'

        self.Mappings = None
        self._mapped_cache = None
        super(MappingNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, ControlNumber, MappedNumbers, attrib=None, **extra):
        obj = MappingNode(tag='Mapping', attrib=attrib, **extra)

        obj.attrib['Control'] = str(ControlNumber)
        obj._mapped_cache = None

        if MappedNumbers is not None:
            obj.Mapped = MappedNumbers

        return obj


class MosaicBaseNode(XFileElementWrapper):

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
        Path = Name + Type + Ext
        return Path

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


class TransformNode(VMH.InputTransformHandler, MosaicBaseNode):

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Transform'

        self._validity_checked = None

        super(TransformNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @staticmethod
    def get_threshold_format():
        return "%.2f"

    @staticmethod
    def get_threshold_precision():
        return 2  # Number of digits to save in XML file

    @staticmethod
    def round_precision_value(value):
        return float(TransformNode.get_threshold_format() % value)  # Number of digits to save in XML file

    @classmethod
    def Create(cls, Name: str, Type: str, Path: str = None, attrib: dict = None, **extra):

        if Path is None:
            Path = MosaicBaseNode.GetFilename(Name, Type)

        obj = cls(tag='Transform', Path=Path, Name=Name, Type=Type, attrib=attrib, **extra)
        return obj

    @property
    def ControlSectionNumber(self) -> int | None:
        if 'ControlSectionNumber' in self.attrib:
            return int(self.attrib['ControlSectionNumber'])

        return None

    @ControlSectionNumber.setter
    def ControlSectionNumber(self, value):

        if value is None:
            if 'ControlSectionNumber' in self.attrib:
                del self.attrib['ControlSectionNumber']
        else:
            self.attrib['ControlSectionNumber'] = "%d" % value

    @property
    def MappedSectionNumber(self) -> int | None:
        if 'MappedSectionNumber' in self.attrib:
            return int(self.attrib['MappedSectionNumber'])

        return None

    @MappedSectionNumber.setter
    def MappedSectionNumber(self, value):
        if value is None:
            if 'MappedSectionNumber' in self.attrib:
                del self.attrib['MappedSectionNumber']
        else:
            self.attrib['MappedSectionNumber'] = "%d" % value

    @property
    def Compressed(self) -> bool:
        """Indicates if the text representation of this transform has been
           compressed to save space.  Compressing is done by removing
           unnecessary precision in coordinates.  It is done to reduce
           the time required to parse the transform at load time.
           """
        if 'Compressed' in self.attrib:
            return bool(self.attrib['Compressed'])

        return False

    @Compressed.setter
    def Compressed(self, value):
        if value is None:
            if 'Compressed' in self.attrib:
                del self.attrib['Compressed']
        else:
            assert (isinstance(value, bool))
            self.attrib['Compressed'] = "%d" % value

    @property
    def CropBox(self):
        """Returns boundaries of transform output if available, otherwise none
           :rtype tuple:
           :return (Xo, Yo, Width, Height):
        """

        if 'CropBox' in self.attrib:
            return nornir_shared.misc.ListFromAttribute(self.attrib['CropBox'])
        else:
            return None

    def CropBoxDownsampled(self, downsample):
        (Xo, Yo, Width, Height) = self.CropBox
        Xo = Xo // float(downsample)
        Yo = Yo // float(downsample)
        Width = int(math.ceil(Width / float(downsample)))
        Height = int(math.ceil(Height / float(downsample)))

        return Xo, Yo, Width, Height

    @CropBox.setter
    def CropBox(self, bounds):
        """Sets boundaries in fixed space for output from the transform.
        :param tuple bounds :  (Xo, Yo, Width, Height) or (Width, Height)
        """
        if len(bounds) == 4:
            self.attrib['CropBox'] = "%g,%g,%g,%g" % bounds
        elif len(bounds) == 2:
            self.attrib['CropBox'] = "0,0,%g,%g" % bounds
        elif bounds is None:
            if 'CropBox' in self.attrib:
                del self.attrib['CropBox']
        else:
            raise Exception(
                "Invalid argument passed to TransformNode.CropBox %s.  Expected 2 or 4 element tuple." % str(bounds))

    @property
    def NeedsValidation(self) -> (bool, str):
        try:
            if self._validity_checked is True:
                return False, "Validity already checked"
        except AttributeError:
            pass

        if super(TransformNode, self).NeedsValidation:
            return True, ""

        input_needs_validation = VMH.InputTransformHandler.InputTransformNeedsValidation(self)
        return input_needs_validation

    def IsValid(self) -> (bool, str):
        """Check if the transform is valid.  Be careful using this, because it only checks the existing meta-data.
           If you are comparing to a new input transform you should use VMH.IsInputTransformMatched"""

        # We write down the result the first time we check if the transform is valid since it is expensive
        try:
            if self._validity_checked is True:
                return True, "Validity already checked"
        except AttributeError:
            pass

        [valid, reason] = super(TransformNode, self).IsValid()
        prettyoutput.Log('Validate: {0}'.format(self.FullPath))
        if valid:
            [valid, reason] = VMH.InputTransformHandler.InputTransformIsValid(self)

        # We can delete a locked transform if it does not exist on disk
        if not valid and not os.path.exists(self.FullPath):
            self.Locked = False

        if valid:
            self._validity_checked = valid

        return valid, reason

    @property
    def Threshold(self) -> float | None:
        val = self.attrib.get('Threshold', None)
        if isinstance(val, str):
            if len(val) == 0:
                return None

        if val is not None:
            val = float(val)

        return val

    @Threshold.setter
    def Threshold(self, val):
        if val is None and 'Threshold' in self.attrib:
            del self.attrib['Threshold']

        self.attrib['Threshold'] = TransformNode.get_threshold_format() % val


class ImageSetBaseNode(VMH.InputTransformHandler, VMH.PyramidLevelHandler, XContainerElementWrapper, abc.ABC):

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
        node = tile.BuildImagePyramid(self, Levels, Interlace=False)
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

        input_needs_validation = VMH.InputTransformHandler.InputTransformNeedsValidation(self)
        return input_needs_validation[0]

    def IsValid(self) -> (bool, str):
        """Check if the image set is valid.  Be careful using this, because it only checks the existing meta-data.
           If you are comparing to a new input transform you should use VMH.IsInputTransformMatched"""

        [valid, reason] = super(ImageSetBaseNode, self).IsValid()
        prettyoutput.Log('Validate: {0}'.format(self.FullPath))
        if valid:
            (valid, reason) = VMH.InputTransformHandler.InputTransformIsValid(self)
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


class ImageSetNode(ImageSetBaseNode):
    """Represents single image at various downsample levels"""

    @property
    def Checksum(self) -> str:
        raise NotImplementedError(
            "Checksum on ImageSet... not sure why this would be needed.  Try using checksum of highest resolution image instead?")

    DefaultPath = 'Images'

    def FindDownsampleForSize(self, requested_size):
        """Find the smallest existing image of the requested size or greater.  If it does not exist return the maximum resolution level
        :param tuple requested_size: Either a tuple or integer.  A tuple requires both dimensions to be larger than the requested_size.  A integer requires only one of the dimensions to be larger.
        :return: Downsample level
        """

        level = self.MinResLevel
        while level.Downsample > self.MaxResLevel.Downsample:
            levelImg = self.GetImage(level)
            if levelImg is None:
                level = self.MoreDetailedLevel(level.Downsample)

            dim = self.GetImage(level).Dimensions
            if isinstance(requested_size, tuple):
                if dim[0] >= requested_size[0] and dim[1] >= requested_size[1]:
                    return level.Downsample
            elif dim[0] >= requested_size or dim[1] >= requested_size:
                return level.Downsample

            level = self.MoreDetailedLevel(level.Downsample)

        return self.MaxResLevel.Downsample

    def IsLevelValid(self, level_node) -> bool:
        raise NotImplemented("HasImage is being used.  I considered this a dead code path.")

    #         '''
    #         :param str level_full_path: The path to the directories containing the image files
    #         :return: (Bool, String) containing whether all tiles exist and a reason string
    #         '''
    #
    #         level_full_path = level_node.FullPath
    #
    #         globfullpath = os.path.join(level_full_path, '*' + self.ImageFormatExt)
    #
    #         files = glob.glob(globfullpath)
    #
    #         if(len(files) == 0):
    #             return [False, "No files in level"]
    #
    #         FileNumberMatch = len(files) <= self.NumberOfTiles
    #
    #         if not FileNumberMatch:
    #             return [False, "File count mismatch for level"]
    #
    #         return [True, None]

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'ImageSet'

        super(ImageSetNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, Type: str = None, attrib: dict = None, **extra):
        if Type is None:
            Type = ""

        obj = ImageSetNode(Type=Type, Path=ImageSetNode.DefaultPath, attrib=attrib, **extra)

        return obj


class ImageNode(VMH.InputTransformHandler, XFileElementWrapper):
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


class DataNode(XFileElementWrapper):
    """Refers to an external file containing data"""

    @classmethod
    def Create(cls, Path: str, attrib: dict = None, **extra):
        return cls(tag='Data', Path=Path, attrib=attrib, **extra)


class TransformDataNode(VMH.InputTransformHandler, XFileElementWrapper):
    """
    Represents visualization data associated with a specific transform
    """

    @classmethod
    def Create(cls, Path: str, attrib: dict = None, **extra):
        return cls(tag='TransformData', Path=Path, attrib=attrib, **extra)

    def IsValid(self) -> (bool, str):
        if not os.path.exists(self.FullPath):
            return [False, 'File does not exist']

        (valid, reason) = self.InputTransformIsValid()
        if not valid:
            return valid, reason

        return super(ImageNode, self).IsValid()


class SectionMappingsNode(XElementWrapper):

    @property
    def SortKey(self):
        """The default key used for sorting elements"""
        return self.tag + ' ' + (nornir_buildmanager.templates.Current.SectionTemplate % self.MappedSectionNumber)

    @property
    def MappedSectionNumber(self) -> int | None:
        if 'MappedSectionNumber' in self.attrib:
            return int(self.attrib['MappedSectionNumber'])

        return None

    @property
    def Transforms(self) -> [TransformNode]:
        return list(self.findall('Transform'))

    @property
    def Images(self) -> [ImageNode]:
        return list(self.findall('Image'))

    def TransformsToSection(self, sectionNumber:int):
        return self.GetChildrenByAttrib('Transform', 'ControlSectionNumber', sectionNumber)

    def FindStosTransform(self, ControlSectionNumber:int, ControlChannelName: str, ControlFilterName: str, MappedSectionNumber: int,
                          MappedChannelName: str, MappedFilterName: str):
        """
        Find the stos transform matching all of the parameters if it exists
        WORKAROUND: The etree implementation has a serious shortcoming in that it cannot handle the 'and' operator in XPath queries.  This function is a workaround for a multiple criteria find query
        :rtype TransformNode:
        """

        # TODO: 3/10/2017 I believe I can stop checking MappedSectionNumber because it is built into the SectionMapping node.  This is a sanity check before I pull the plug
        assert (MappedSectionNumber == self.MappedSectionNumber)

        for t in self.Transforms:
            if t.ControlSectionNumber != ControlSectionNumber:
                continue

            if t.ControlChannelName != ControlChannelName:
                continue

            if t.ControlFilterName != ControlFilterName:
                continue

            if t.MappedSectionNumber != MappedSectionNumber:
                continue

            if t.MappedChannelName != MappedChannelName:
                continue

            if t.MappedFilterName != MappedFilterName:
                continue

            return t

        return None

    def TryRemoveTransformNode(self, transform_node: TransformNode):
        """Remove the transform if it exists
        :rtype bool:
        :return: True if transform removed
        """
        return self.TryRemoveTransform(transform_node.ControlSectionNumber,
                                       transform_node.ControlChannelName,
                                       transform_node.ControlFilterName,
                                       transform_node.MappedChannelName,
                                       transform_node.MappedFilterName)

    def TryRemoveTransform(self,
                           ControlSectionNumber: int,
                           ControlChannelName: str,
                           ControlFilterName: str,
                           MappedChannelName: str,
                           MappedFilterName: str):
        """Remove the transform if it exists
        :rtype bool:
        :return: True if transform removed
        """

        existing_transform = self.FindStosTransform(ControlSectionNumber, ControlChannelName, ControlFilterName,
                                                    self.MappedSectionNumber, MappedChannelName, MappedFilterName)
        if existing_transform is not None:
            existing_transform.Clean()
            return True

        return False

    def AddOrUpdateTransform(self, transform_node: TransformNode):
        """
        Add or update a transform to the section mappings.
        :rtype bool:
        :return: True if the transform node was added.  False if updated.
        """
        existing_transform = self.TryRemoveTransformNode(transform_node)
        self.AddChild(transform_node)
        return not existing_transform

    @classmethod
    def _CheckForFilterExistence(cls,
                                 block: BlockNode,
                                 section_number: int,
                                 channel_name: str,
                                 filter_name: str) -> (bool, str):

        section_node = block.GetSection(section_number)
        if section_node is None:
            return False, "Transform section not found %d.%s.%s" % (section_number, channel_name, filter_name)

        channel_node = section_node.GetChannel(channel_name)
        if channel_node is None:
            return False, "Transform channel not found %d.%s.%s" % (section_number, channel_name, filter_name)

        filter_node = channel_node.GetFilter(filter_name)
        if filter_node is None:
            return False, "Transform filter not found %d.%s.%s" % (section_number, channel_name, filter_name)

        return True, None

    def CleanIfInvalid(self):
        cleaned = XElementWrapper.CleanIfInvalid(self)
        if not cleaned:
            return self.CleanTransformsIfInvalid()

        return cleaned

    def CleanTransformsIfInvalid(self):
        block = self.FindParent('Block')

        transformCleaned = False

        # Check the transforms and make sure the input data still exists
        for t in self.Transforms:
            transformValid = t.IsValid()
            if not transformValid[0]:
                prettyoutput.Log("Cleaning invalid transform " + t.Path + " " + transformValid[1])
                t.Clean()
                transformCleaned = True
                continue

            ControlResult = SectionMappingsNode._CheckForFilterExistence(block, t.ControlSectionNumber,
                                                                         t.ControlChannelName, t.ControlFilterName)
            if ControlResult[0] is False:
                prettyoutput.Log("Cleaning transform " + t.Path + " control input did not exist: " + ControlResult[1])
                t.Clean()
                transformCleaned = True
                continue

            MappedResult = SectionMappingsNode._CheckForFilterExistence(block, t.MappedSectionNumber,
                                                                        t.MappedChannelName, t.MappedFilterName)
            if MappedResult[0] is False:
                prettyoutput.Log("Cleaning transform " + t.Path + " mapped input did not exist: " + MappedResult[1])
                t.Clean()
                transformCleaned = True
                continue

        return transformCleaned

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'SectionMappings'

        super(SectionMappingsNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, Path=None, MappedSectionNumber=None, attrib=None, **extra):
        obj = SectionMappingsNode(attrib=attrib, **extra)

        if MappedSectionNumber is not None:
            obj.attrib['MappedSectionNumber'] = str(MappedSectionNumber)

        return obj


class TilePyramidNode(XContainerElementWrapper, VMH.PyramidLevelHandler):
    """A collection of images, all downsampled for each levels"""

    DefaultName = 'TilePyramid'
    DefaultPath = 'TilePyramid'

    @property
    def LevelFormat(self) -> str:
        return self.attrib.get('LevelFormat', None)

    @LevelFormat.setter
    def LevelFormat(self, val):
        assert (isinstance(val, str))
        self.attrib['LevelFormat'] = val

    @property
    def NumberOfTiles(self) -> int:
        return int(self.attrib.get('NumberOfTiles', 0))

    @NumberOfTiles.setter
    def NumberOfTiles(self, val):
        self.attrib['NumberOfTiles'] = '%d' % val

    @property
    def ImageFormatExt(self) -> str:
        return self.attrib.get('ImageFormatExt', None)

    @ImageFormatExt.setter
    def ImageFormatExt(self, val):
        assert (isinstance(val, str))
        self.attrib['ImageFormatExt'] = val

    @property
    def Type(self) -> str:
        """The default mask to use for this filter"""
        m = self.attrib.get("Type", None)
        if m is not None:
            if len(m) == 0:
                m = None

        return m

    @Type.setter
    def Type(self, val):
        if val is None:
            if 'Type' in self.attrib:
                del self.attrib['Type']
        else:
            self.attrib['Type'] = val

    def ImagesInLevel(self, level_node):
        """
        :return: A list of all images contained in the level directory
        :rtype: list
        """

        level_full_path = level_node.FullPath
        expectedExtension = self.ImageFormatExt

        try:
            images = []
            with os.scandir(level_full_path) as pathscan:
                for item in pathscan:
                    if item.is_file() is False:
                        continue

                    if item.name[0] == '.':  # Avoid the .desktop_ini files of the world
                        continue

                    (root, ext) = os.path.splitext(item.name)
                    if ext != expectedExtension:
                        continue

                    images.add(item.path)

            return True, images

        except FileNotFoundError:
            return []

    @property
    def NeedsValidation(self) -> bool:
        return True

    def IsValid(self) -> (bool, str):
        """Remove level directories without files, or with more files than they should have"""

        (valid, reason) = super(TilePyramidNode, self).IsValid()
        if not valid:
            return valid, reason

        return valid, reason

        # Starting with the highest resolution level, we need to check that all 
        # of the levels are valid

    def CheckIfLevelTilesExistViaMetaData(self, level_node):
        """
        Using the meta-data, returns whether there is a reasonable belief that
        the passed level has all of the tiles and that they are valid
        :return: True if the level should have its contents validated
        """

        level_full_path = level_node.FullPath

        if self.Parent is None:  # Don't check for validity if our node has not been added to the tree yet
            if not os.path.isdir(level_full_path):
                return False, '{0} directory does not exist'.format(level_full_path)
            else:
                return True, 'Element has not been added to the tree'

        level_has_changes = level_node.ChangesSinceLastValidation

        if level_has_changes is None:
            return [False, '{0} directory does not exist'.format(level_full_path)]

        if level_has_changes:
            prettyoutput.Log('Validating tiles in {0}, directory was modified since last check'.format(level_full_path))
            nornir_buildmanager.operations.tile.VerifyTiles(level_node)

        # The "No modifications since last validation case"
        if self.NumberOfTiles == level_node.TilesValidated:
            return (
                True, "Tiles validated previously, directory has not been modified, and # validated == # in Pyramid")
        elif self.NumberOfTiles < level_node.TilesValidated:
            return True, "More tiles validated than expected in level"
        else:
            return False, "Fewer tiles validated than expected in level"

    def TryToMakeLevelValid(self, level_node):
        """
        :param str level_full_path: The path to the directories containing the image files
        :return: (Bool, String) containing whether all tiles exist and a reason string
        """

        (ProbablyGood, Reason) = self.CheckIfLevelTilesExistViaMetaData(level_node)

        if ProbablyGood:
            return True, Reason

        # Attempt to regenerate the level, then we'll check again for validity
        self.GenerateLevels(level_node.Downsample)
        # if output is not None:
        #    output.Save()

        (ProbablyGood, Reason) = self.CheckIfLevelTilesExistViaMetaData(level_node)

        if ProbablyGood:
            return True, Reason

        return ProbablyGood, Reason

    #
    #         globfullpath = os.path.join(level_full_path, '*' + self.ImageFormatExt)
    #
    #         files = glob.glob(globfullpath)
    #
    #         if(len(files) == 0):
    #             return [False, "No files in level"]
    #
    #         FileNumberMatch = len(files) <= self.NumberOfTiles
    #
    #         if not FileNumberMatch:
    #             return [False, "File count mismatch for level"]
    #
    #         return [True, None]

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'TilePyramid'

        super(TilePyramidNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, NumberOfTiles=0, LevelFormat=None, ImageFormatExt=None, attrib=None, **extra):
        if LevelFormat is None:
            LevelFormat = nornir_buildmanager.templates.Current.LevelFormat

        if ImageFormatExt is None:
            ImageFormatExt = '.png'

        obj = cls(tag='TilePyramid',
                  Path=TilePyramidNode.DefaultPath,
                  attrib=attrib,
                  NumberOfTiles=str(NumberOfTiles),
                  ImageFormatExt=ImageFormatExt,
                  LevelFormat=LevelFormat,
                  **extra)

        return obj

    def GenerateLevels(self, Levels):
        node = tile.BuildTilePyramids(self, Levels)
        if node is not None:
            node.Save()


class TilesetNode(XContainerElementWrapper, VMH.PyramidLevelHandler, VMH.InputTransformHandler):
    DefaultPath = 'Tileset'

    @property
    def CoordFormat(self) -> str:
        return self.attrib.get('CoordFormat', None)

    @CoordFormat.setter
    def CoordFormat(self, val):
        self.attrib['CoordFormat'] = val

    @property
    def FilePrefix(self) -> str:
        return self.attrib.get('FilePrefix', None)

    @FilePrefix.setter
    def FilePrefix(self, val):
        self.attrib['FilePrefix'] = val

    @property
    def FilePostfix(self) -> str:
        return self.attrib.get('FilePostfix', None)

    @FilePostfix.setter
    def FilePostfix(self, val):
        self.attrib['FilePostfix'] = val

    @property
    def TileXDim(self) -> int:
        val = self.attrib.get('TileXDim', None)
        if val is not None:
            val = int(val)

        return val

    @TileXDim.setter
    def TileXDim(self, val):
        self.attrib['TileXDim'] = '%d' % int(val)

    @property
    def TileYDim(self) -> int:
        val = self.attrib.get('TileYDim', None)
        if val is not None:
            val = int(val)

        return val

    @TileYDim.setter
    def TileYDim(self, val):
        self.attrib['TileYDim'] = '%d' % int(val)

    @classmethod
    def Create(cls):
        return TilesetNode()

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Tileset'

        super(TilesetNode, self).__init__(tag=tag, Path=TilesetNode.DefaultPath, attrib=attrib, **extra)

        if 'Path' not in self.attrib:
            self.attrib['Path'] = TilesetNode.DefaultPath

    def GenerateLevels(self, Levels):
        node = tile.BuildTilesetPyramid(self)
        if node is not None:
            node.Save()

    @property
    def NeedsValidation(self) -> bool:
        # We don't check with the base class' last directory modification because 
        # we cannot save the metadata without changing the timestamp, so we 
        # only look at the input transform (which will not exist for volumes built
        # before June 8th 2020.)  If there is no input transform then no validation
        # is done and tilesets must be deleted manually to refresh them.
        # if super(TilesetNode, self).NeedsValidation:
        #    return True

        input_needs_validation = VMH.InputTransformHandler.InputTransformNeedsValidation(self)
        return input_needs_validation[0]

    def IsValid(self) -> (bool, str):
        """Check if the TileSet is valid.  Be careful using this, because it only checks the existing meta-data.
           If you are comparing to a new input transform you should use VMH.IsInputTransformMatched"""

        [valid, reason] = super(TilesetNode, self).IsValid()
        prettyoutput.Log('Validate: {0}'.format(self.FullPath))
        if valid:
            (valid, reason) = VMH.InputTransformHandler.InputTransformIsValid(self)
            # if valid:
            # [valid, reason] = super(TransformNode, self).IsValid()

        # We can delete a locked transform if it does not exist on disk
        if not valid and not os.path.exists(self.FullPath):
            self.Locked = False

        return valid, reason

    def IsLevelValid(self, level_node, GridDimX: int, GridDimY: int):
        """
        :param str level_full_path: The path to the directories containing the image files
        :return: (Bool, String) containing whether all tiles exist and a reason string
        """

        if GridDimX is None or GridDimY is None:
            return False, "No grid dimensions found in tileset"

        level_full_path = level_node.FullPath

        GridXDim = GridDimX - 1  # int(GridDimX) - 1
        GridYDim = GridDimY - 1  # int(GridDimY) - 1

        FilePrefix = self.FilePrefix
        FilePostfix = self.FilePostfix

        GridXString = nornir_buildmanager.templates.Current.GridTileCoordTemplate % GridXDim
        # MatchString = os.path.join(OutputDir, FilePrefix + 'X%' + nornir_buildmanager.templates.GridTileCoordFormat % GridXDim + '_Y*' + FilePostfix)
        MatchString = os.path.join(level_full_path,
                                   nornir_buildmanager.templates.Current.GridTileMatchStringTemplate % {
                                       'prefix': FilePrefix,
                                       'X': GridXString,
                                       'Y': '*',
                                       'postfix': FilePostfix})

        # Start with the middle because it is more likely to have a match earlier
        TestIndicies = list(range(GridYDim // 2, GridYDim))
        TestIndicies.extend(list(range((GridYDim // 2) - 1, -1, -1)))
        for iY in TestIndicies:
            # MatchString = os.path.join(OutputDir, FilePrefix +
            #                           'X' + nornir_buildmanager.templates.GridTileCoordFormat % GridXDim +
            #                           '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % iY +
            #                           FilePostfix)
            MatchString = os.path.join(level_full_path,
                                       nornir_buildmanager.templates.Current.GridTileMatchStringTemplate % {
                                           'prefix': FilePrefix,
                                           'X': GridXString,
                                           'Y': nornir_buildmanager.templates.Current.GridTileCoordTemplate % iY,
                                           'postfix': FilePostfix})
            if os.path.exists(MatchString):
                [YSize, XSize] = nornir_imageregistration.GetImageSize(MatchString)
                if YSize != self.TileYDim or XSize != self.TileXDim:
                    return [False, "Image size does not match meta-data"]

                level_node.UpdateValidationTime()
                return [True, "Last column of tileset found"]

            MatchString = os.path.join(level_full_path, nornir_buildmanager.templates.Current.GridTileNameTemplate % {
                'prefix': FilePrefix,
                'X': GridXDim,
                'Y': iY,
                'postfix': FilePostfix})
            if os.path.exists(MatchString):
                [YSize, XSize] = nornir_imageregistration.GetImageSize(MatchString)
                if YSize != self.TileYDim or XSize != self.TileXDim:
                    return [False, "Image size does not match meta-data"]

                level_node.UpdateValidationTime()
                return [True, "Last column of tileset found"]

        return [False, "Last column of tileset not found"]


class LevelNode(XContainerElementWrapper):

    @property
    def SaveAsLinkedElement(self):
        """
        See base class for full description.  This is set to false to
        prevent saving the LevelNode's VolumeData.xml from changing the
        directories last modified time.  This allows us to know if a
        directory has changed and we need to re-verify any images in the
        level.
        """
        return False

    @classmethod
    def PredictPath(cls, level):
        return nornir_buildmanager.templates.Current.LevelFormat % int(level)

    @classmethod
    def ClassSortKey(cls, self):
        """Required for objects derived from XContainerElementWrapper"""
        return "Level" + ' ' + nornir_buildmanager.templates.Current.DownsampleFormat % float(self.Downsample)

    @property
    def SortKey(self):
        """The default key used for sorting elements"""
        return LevelNode.ClassSortKey(self)

    @property
    def Name(self):
        return '%g' % self.Downsample

    @Name.setter
    def Name(self, Value):
        assert False, "Attempting to set name on LevelNode"

    @property
    def Downsample(self):
        assert ('Downsample' in self.attrib)
        return float(self.attrib.get('Downsample', ''))

    @Downsample.setter
    def Downsample(self, Value):
        self.attrib['Downsample'] = '%g' % Value

    @property
    def GridDimX(self):
        val = self.attrib.get('GridDimX', None)
        if val is not None:
            val = int(val)

        return val

    @GridDimX.setter
    def GridDimX(self, val):
        if val is None:
            if 'GridDimX' in self.attrib:
                del self.attrib['GridDimX']
        else:
            self.attrib['GridDimX'] = '%d' % int(val)

    @property
    def GridDimY(self):
        val = self.attrib.get('GridDimY', None)
        if val is not None:
            val = int(val)

        return val

    @GridDimY.setter
    def GridDimY(self, val):
        if val is None:
            if 'GridDimY' in self.attrib:
                del self.attrib['GridDimY']
        else:
            self.attrib['GridDimY'] = '%d' % int(val)

    @property
    def TilesValidated(self):
        """
        :return: Returns None if the attribute has not been set, otherwise an integer
        """
        val = self.attrib.get('TilesValidated', None)
        if val is not None:
            val = int(val)

        return val

    @TilesValidated.setter
    def TilesValidated(self, val):
        if val is None:
            if 'TilesValidated' in self.attrib:
                del self.attrib['TilesValidated']
        else:
            self.attrib['TilesValidated'] = '%d' % int(val)

    def IsValid(self):
        """Remove level directories without files, or with more files than they should have"""

        if not os.path.isdir(self.FullPath):
            return [False, 'Directory does not exist']

        # We need to be certain to avoid the pathscan that occurs in our parent class,
        # So we check that our directory exists and call it good

        PyramidNode = self.Parent
        if isinstance(PyramidNode, TilePyramidNode):
            return PyramidNode.TryToMakeLevelValid(self)
        elif isinstance(PyramidNode, TilesetNode):
            return PyramidNode.IsLevelValid(self, self.GridDimX, self.GridDimY)
        elif isinstance(PyramidNode, ImageSetNode):
            if not PyramidNode.HasImage(self.Downsample):
                return False, "No image node found"
            # Make sure each level has at least one tile from the last column on the disk.

        return True, None

    @classmethod
    def Create(cls, Level, attrib=None, **extra):

        obj = LevelNode(tag='Level', Path=LevelNode.PredictPath(Level))

        if isinstance(Level, str):
            obj.attrib['Downsample'] = Level
        else:
            obj.attrib['Downsample'] = '%g' % Level

        return obj

    def __init__(self, tag=None, attrib=None, **extra):

        if tag is None:
            tag = 'Level'

        if attrib is None:
            attrib = {}

        super(LevelNode, self).__init__(tag='Level', attrib=attrib, **extra)

        # Temporary remap for TileValidationTime
        if 'TileValidationTime' in self.attrib:
            val = self.attrib.get('TileValidationTime', datetime.datetime.min)
            if val is not None and isinstance(val, str):
                val = datetime.datetime.fromisoformat(val)

            self.ValidationTime = val
            del self.attrib['TileValidationTime']


class HistogramBase(VMH.InputTransformHandler, XElementWrapper):

    @property
    def DataNode(self) -> DataNode:
        return self.find('Data')  # data: DataNode

    @property
    def ImageNode(self) -> ImageNode:
        return self.find('Image')  # data: ImageNode

    @property
    def DataFullPath(self) -> str:
        if self.DataNode is None:
            return ""

        return self.DataNode.FullPath

    @property
    def ImageFullPath(self) -> str:
        if self.ImageNode is None:
            return ""

        return self.ImageNode.FullPath

    @property
    def Checksum(self) -> str:
        if self.DataNode is None:
            return ""
        else:
            return self.DataNode.Checksum

    @property
    def NeedsValidation(self) -> bool:

        if self.InputTransformNeedsValidation():
            return True

        if self.DataNode is None:
            return True

        return self.DataNode.NeedsValidation

    def IsValid(self) -> (bool, str):
        """Remove this node if our output does not exist"""
        if self.DataNode is None:
            return False, "No data node found"
        else:
            if not os.path.exists(self.DataNode.FullPath):
                return False, "No file to match data node"

        '''Check for the transform node and ensure the checksums match'''
        # TransformNode = self.Parent.find('Transform')

        return super(HistogramBase, self).IsValid()


class AutoLevelHintNode(XElementWrapper):

    @property
    def UserRequestedMinIntensityCutoff(self) -> float | None:
        """Returns None or a float"""
        val = self.attrib.get('UserRequestedMinIntensityCutoff', None)
        if val is None or len(val) == 0:
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
    def UserRequestedMaxIntensityCutoff(self) -> float | None:
        """Returns None or a float"""
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
    def UserRequestedGamma(self) -> float | None:
        """Returns None or a float"""
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

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'AutoLevelHint'

        super(AutoLevelHintNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, MinIntensityCutoff=None, MaxIntensityCutoff=None, Gamma=None):
        attrib = {'UserRequestedMinIntensityCutoff': "",
                  'UserRequestedMaxIntensityCutoff': "",
                  'UserRequestedGamma': ""}

        obj = AutoLevelHintNode(attrib=attrib)

        obj.UserRequestedMinIntensityCutoff = MinIntensityCutoff
        obj.UserRequestedMaxIntensityCutoff = MaxIntensityCutoff
        obj.UserRequestedGamma = Gamma

        return obj


class HistogramNode(HistogramBase):

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Histogram'

        super(HistogramNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, InputTransformNode, Type, attrib=None, **extra):
        obj = HistogramNode(attrib=attrib, **extra)
        obj.SetTransform(InputTransformNode)
        obj.attrib['Type'] = Type
        return obj

    def GetAutoLevelHint(self):
        return self.find('AutoLevelHint')

    def GetOrCreateAutoLevelHint(self):
        existing_hint = self.GetAutoLevelHint()
        if existing_hint is not None:
            return existing_hint
        else:
            # Create a new AutoLevelData node using the calculated values as overrides so users can find and edit it later
            self.UpdateOrAddChild(AutoLevelHintNode.Create())
            return self.GetAutoLevelHint()


class PruneNode(HistogramBase):

    @property
    def Overlap(self) -> float | None:
        if 'Overlap' in self.attrib:
            return float(self.attrib['Overlap'])

        return None

    @property
    def NumImages(self) -> int:
        if 'NumImages' in self.attrib:
            return int(self.attrib['NumImages'])

        return 0

    @NumImages.setter
    def NumImages(self, value):

        if value is None:
            if 'NumImages' in self.attrib:
                del self.attrib['NumImages']
                return

        self.attrib['NumImages'] = str(value)
        return

    @property
    def UserRequestedCutoff(self) -> float | None:
        val = self.attrib.get('UserRequestedCutoff', None)
        if isinstance(val, str):
            if len(val) == 0:
                return None

        if val is not None:
            val = float(val)

        return val

    @UserRequestedCutoff.setter
    def UserRequestedCutoff(self, val: float | None):
        if val is None:
            val = ""

        self.attrib['UserRequestedCutoff'] = str(val)

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Prune'

        super(PruneNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, Type: str, Overlap: float, attrib=None, **extra):

        obj = cls(attrib=attrib, **extra)
        obj.attrib['Type'] = Type
        obj.attrib['Overlap'] = str(Overlap)

        if 'UserRequestedCutoff' not in obj.attrib:
            obj.attrib['UserRequestedCutoff'] = ""

        return obj


if __name__ == '__main__':
    VolumeManager.Load(r"C:\Temp")

    # tagdict = XPropertiesElementWrapper.wrap(ElementTree.Element("Tag"))
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

    del tagdict.Properties.Value
    del tagdict.Properties.Path

    print(ElementTree.tostring(tagdict))
