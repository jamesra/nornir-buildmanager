from __future__ import annotations

import datetime
import logging
import operator
import threading
from typing import *
from xml.etree import ElementTree as ElementTree

import nornir_buildmanager
from nornir_shared import prettyoutput as prettyoutput

# Used for debugging with conditional break's, each node gets a temporary unique ID
nid = 0


class XElementWrapper(ElementTree.Element):
    _save_lock : threading.RLock
    
    logger = logging.getLogger('XElementWrapper')

    def sort(self):
        """Order child elements"""

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

    def _GetAttribFromParent(self, attribName: str):
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
    def Checksum(self, value: str):
        if not isinstance(value, str):
            XElementWrapper.logger.warning(
                'Setting non string value on XElement.Checksum, automatically corrected: ' + str(value))
        self.attrib['Checksum'] = value
        return

    @property
    def Version(self) -> float:
        return float(self.attrib.get('Version', 1.0))

    @Version.setter
    def Version(self, value):
        self.attrib['Version'] = str(value)

    @property
    def AttributesChanged(self) -> bool:
        """
        :return: Boolean indicating if an attribute has changed.  Used to indicate
        the element needs to be saved to disk.
        :rtype: bool
        """
        return self._AttributesChanged

    @AttributesChanged.setter
    def AttributesChanged(self, value: bool):
        self._AttributesChanged = value

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
    def ChildrenChanged(self, value: bool):
        self._ChildrenChanged = value

    @property
    def ElementHasChangesToSave(self) -> bool:
        """Check this and child elements (which are not linked containers that will save themselves) for changes to save.  We need to note any nested elements that would save with this element"""

        if self.AttributesChanged or self.ChildrenChanged:
            return True

        ReturnValue = False
        for child in self:
            if child.tag.endswith('_Link'):
                continue

            if isinstance(child, nornir_buildmanager.volumemanager.XContainerElementWrapper):
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

            if isinstance(child, nornir_buildmanager.volumemanager.XContainerElementWrapper):
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
    #         if not isinstance(parent, nornir_buildmanager.volumemanager.XContainerElementWrapper):
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
    def Parent(self, value: XElementWrapper | None):
        """
        Setting the parent with this method will set the Attribute Changed flag
        """
        self.__dict__['_Parent'] = value
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
        nid += 1

        self._AttributesChanged = False
        self._ChildrenChanged = False
        self._save_lock = threading.RLock()

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

            if 'Version' not in self.attrib:
                self.Version = nornir_buildmanager.volumemanager.GetLatestVersionForNodeType(tag)

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
            if nornir_buildmanager.volumemanager.GetLatestVersionForNodeType(self.tag) > 1.0:
                return False, "Node version outdated"

        if not nornir_buildmanager.volumemanager.IsNodeVersionCompatible(self.tag, self.Version):
            return False, "Node version outdated"

        return True, ""

    def CleanIfInvalid(self) -> (bool, str):
        """Remove the contents of this node if it is out of date
        :returns: true, reason (bool,str) if node was cleaned"""
        Valid = self.IsValid()

        if isinstance(Valid, bool):
            Valid = (Valid, "")

        if not Valid[0]:
            self.Clean(Valid[1])

        return Valid[0] == False, Valid[1]  # The return value convention is reversed from IsValid.

    def Clean(self, reason: str | None = None):
        """Remove node from element tree and remove any external resources such as files"""

        '''Remove the contents referred to by this node from the disk'''
        prettyoutput.Log(f' --- Cleaning {self.ToElementString()}. ')
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

    def Copy(self) -> Self:
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
    def wrap(cls, dictElement: XElementWrapper | ElementTree.Element) -> XElementWrapper:
        """Change the class of an ElementTree.Element(PropertyElementName) to add our wrapper functions"""
        if isinstance(dictElement, cls):  # Check if it is already wrapped
            return dictElement

        newElement = cls.__CreateFromElement(dictElement)
        # dictElement.__class__ = cls
        assert (newElement is not None)
        assert (isinstance(newElement, cls))

        if 'CreationDate' not in newElement.attrib:
            cls.logger.info("Populating missing CreationDate attribute " + newElement.ToElementString())
            newElement.attrib['CreationDate'] = XElementWrapper.__GetCreationTimeString__()

        if isinstance(newElement, nornir_buildmanager.volumemanager.XContainerElementWrapper):
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
                    try:
                        self._save_lock.acquire(blocking=True)
                        # Mark the _AttributesChanged flag if the value has been updated
                        if attribute.fget is not None:
                            self._AttributesChanged = self._AttributesChanged or attribute.fget(self) != value
                        else:
                            self._AttributesChanged = True
                        attribute.fset(self, value)
                    finally:
                        self._save_lock.release()
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
            try:
                self._save_lock.acquire(blocking=True)
                originalValue = None
                if name in self.attrib:
                    originalValue = self.attrib[name]
    
                if value is None:
                    raise ValueError(f"Setting None on XML Element attribute: {name}")
                elif not isinstance(value, str):
                    XElementWrapper.logger.info('Setting non string value on <' + str(
                        self.tag) + '>, automatically corrected: ' + name + ' -> ' + str(value))
    
                    strVal = '%g' % value if isinstance(value, float) else str(value)
                    self.attrib[name] = strVal
                    self._AttributesChanged = self._AttributesChanged or (strVal != originalValue)
                else:
                    self.attrib[name] = value
                    self._AttributesChanged = self._AttributesChanged or (value != originalValue)
            finally:
                self._save_lock.release()

    def __delattr__(self, name):

        """Like __setattr__() but for attribute deletion instead of assignment. This should only be implemented if del obj.name is meaningful for the object."""
        
        if name in self.__dict__:
            self.__dict__.pop(name)
        elif name in self.attrib:
            try:
                self._save_lock.acquire(blocking=True)
                self._AttributesChanged = True
                self.attrib.pop(name)
            finally:
                self._save_lock.release()

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

        Children = list(Children)
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

    def GetChildrenByAttrib(self, ElementName: str, AttribName: str, AttribValue) -> Generator[XElementWrapper]:
        XPathStr = "%(ElementName)s[@%(AttribName)s='%(AttribValue)s']" % {'ElementName': ElementName,
                                                                           'AttribName': AttribName,
                                                                           'AttribValue': AttribValue}
        return self.findall(XPathStr)

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

    def UpdateOrAddChildByAttrib(self, element: XElementWrapper, AttribNames=None) -> (bool, XElementWrapper):
        if AttribNames is None:
            AttribNames = ['Name']
        elif isinstance(AttribNames, str):
            AttribNames = [AttribNames]
        elif not isinstance(AttribNames, list):
            raise Exception("Unexpected attribute names for UpdateOrAddChildByAttrib")

        attribXPathTemplate = "@%(AttribName)s='%(AttribValue)s'"
        attribXPaths = []

        for AttribName in AttribNames:
            val = element.attrib[AttribName]
            attribXPaths.append(attribXPathTemplate % {'AttribName': AttribName,
                                                       'AttribValue': val})

        XPathStr = "%(ElementName)s[%(QueryString)s]" % {'ElementName': element.tag,
                                                         'QueryString': ' and '.join(attribXPaths)}
        return self.UpdateOrAddChild(element, XPathStr)

    def UpdateOrAddChild(self, element: XElementWrapper, XPath: str = None) -> (bool, XElementWrapper):
        """Adds an element using the specified XPath.  If the XPath is unspecified the element name is used
           Returns a tuple with (True/False, Element).
           True indicates the element did not exist and was added.
           False indicates the element existed and the existing value is returned.
           """

        if XPath is None:
            XPath = element.tag

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
            if element is not None:
                self.append(element)
                assert (element in self)
                child = element
                NewNodeCreated = True
            else:
                # No data provided to create the child element
                return False, None

        # Make sure the parent is set correctly
        (wrapped, child) = nornir_buildmanager.volumemanager.WrapElement(child)

        if wrapped:
            nornir_buildmanager.volumemanager.SetElementParent(child, self)
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

    def FindAllFromParent(self, xpath: str) -> Generator[XElementWrapper] | None:
        """Run findall on xpath on each parent, return results only first nearest parent with resuls"""
        #        assert (not ParentTag is None)
        p = self.Parent
        while p is not None:
            results = p.findall(xpath)
            if next(results, None) is not None:
                yield from p.findall(xpath)  # Cannot restart a generator, so have to start it again and return
                return

            p = p.Parent

        return

    def _ReplaceChildElementInPlace(self, old, new):

        # print("Removing {0}".format(str(old)))
        i = self.indexofchild(old)

        self[i] = new
        # self.remove(old)
        # self.insert(i, new)

        nornir_buildmanager.volumemanager.SetElementParent(new, self)

    def ReplaceChildWithLink(self, child):
        if isinstance(child, nornir_buildmanager.volumemanager.XContainerElementWrapper):
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

        (wrapped, wrappedElement) = nornir_buildmanager.volumemanager.WrapElement(child)

        if wrapped:
            self._ReplaceChildElementInPlace(child, wrappedElement)
            wrappedElement._AttributesChanged = False  # Setting the parent will set this flag, but if we loaded it there was no change

        return wrappedElement

    # replacement for find function that loads subdirectory xml files
    def find(self, path, namespaces=None) -> XElementWrapper | None:

        (UnlinkedElementsXPath, LinkedElementsXPath, RemainingXPath, UsedWildcard) = self.__ElementLinkNameFromXPath(
            path)

        if isinstance(self,
                      nornir_buildmanager.volumemanager.XContainerElementWrapper):  # Only containers have linked elements
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

    def findall(self, path, namespaces=None) -> Generator[XElementWrapper]:
        match = path
        (UnlinkedElementsXPath, LinkedElementsXPath, RemainingXPath, UsedWildcard) = self.__ElementLinkNameFromXPath(
            match)

        # TODO: Need to modify to only search one level at a time
        # OK, check for linked elements that also meet the criteria

        link_matches = list(super(XElementWrapper, self).findall(LinkedElementsXPath))
        if link_matches is None:
            return  # matches

        if UsedWildcard:
            link_matches = list(filter(lambda e: e.tag.endswith('_Link'), link_matches))

        if link_matches:
            # if num_matches > 1:
            #    prettyoutput.Log("Need to load {0} links".format(num_matches))
            self._replace_links(link_matches)

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
