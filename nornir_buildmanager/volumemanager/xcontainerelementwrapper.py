from __future__ import annotations

import concurrent.futures
import os
import shutil
import sys
import threading
from xml.etree import ElementTree as ElementTree

import nornir_buildmanager
from nornir_buildmanager.volumemanager import ValidateAttributesAreStrings, WrapElement, XElementWrapper, \
    XResourceElementWrapper
from nornir_shared import prettyoutput as prettyoutput


class XContainerElementWrapper(XResourceElementWrapper):
    """XML meta-data for a container whose sub-elements are contained within a directory on the file system.  The directories container will always be the same, such as TilePyramid"""
    _save_lock : threading.Lock
    
    @property
    def SaveAsLinkedElement(self) -> bool:
        """
        When set to true, the element will be saved as a link element in the subdirectory
        It may be set to false to prevent saving meta-data from updating the modification
        time of the directory.  When set to false the element remains under the
        parent element wherever that XML file may be.
        """
        return True

    @property
    def SortKey(self) -> str:
        """The default key used for sorting elements"""

        tag = self.tag
        if tag.endswith("_Link"):
            tag = tag[:-len("_Link")]
            tag += "Node"

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
    def Path(self, val: str):

        super(XContainerElementWrapper, self.__class__).Path.fset(self, val)

        try:
            os.makedirs(self.FullPath)
        except (OSError, FileExistsError):
            if not os.path.isdir(self.FullPath):
                raise ValueError(
                    "{0}.Path property was set to an existing file or non-directory file system object {1}".format(
                        self.__class__, self.FullPath))

        return

    def IsValid(self) -> tuple[bool, str]:
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

    def RepairMissingLinkElements(self, recurse: bool = True):
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
        (wrapped, NewElement) = WrapElement(XMLElement)
        # SubContainer = XContainerElementWrapper.wrap(XMLElement)

        return wrapped, NewElement

    def _load_wrap_setparent_link_element(self, fullpath: str):
        """Loads an xml file containing a subset of our meta-data referred to by a LINK element.  Wraps the loaded XML in the correct meta-data class"""

        XMLElement = XContainerElementWrapper._load_link_element(fullpath)
        (wrapped, NewElement) = WrapElement(XMLElement)
        # SubContainer = XContainerElementWrapper.wrap(XMLElement)

        if wrapped:
            nornir_buildmanager.volumemanager.SetElementParent(NewElement, self)

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
            cleaned, reason = loaded_element.CleanIfInvalid()
            if cleaned:
                return None

        return loaded_element

    def _replace_links(self, link_nodes: list[XElementWrapper], fullpath: str = None):
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
                    nornir_buildmanager.volumemanager.SetElementParent(wrapped_loaded_element, self)

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
        
        self._save_lock = threading.Lock()
        # else:
        # self.attrib['Path'] = Path

    def Save(self, tabLevel: int | None = None, recurse: bool = True):
        """
        Public version of Save, if this element is not flagged SaveAsLinkedElement
        then we need to save the parent to ensure our data is retained
        """

        if self.SaveAsLinkedElement:
            return self._Save()

        elif self.Parent is not None:
            return self.Parent.Save()

        raise NotImplemented("Cannot save a container node that is not linked without a parent node to save it under")

    def _Save(self, tabLevel: int | None = None, recurse: bool = True):
        """
        Called by another Save function.  This function is either called by a
        parent element or by ourselves if SaveAsLinkedElement is True.

        If recurse = False we only save this element, no child elements are saved
        """
        try:
            #We need to take a lock for certain containers where the meta data of child folders is not saved in the child directory.
            #For example, if we are validating each level of a tile pyramid concurrently each level may try to save any updates at the same time.
            self._save_lock.acquire(blocking=True)

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
        finally:
            self._save_lock.release()

    def __SaveXML(self, xmlfilename: str, SaveElement: bool):
        """Intended to be called on a thread from the save function"""
        try:
            OutputXML = ElementTree.tostring(SaveElement, encoding="utf-8")
        except Exception as e:
            prettyoutput.Log(f"Cannot encode output XML:\n{e}")
            raise

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
                except PermissionError:
                    prettyoutput.LogErr(f"Permission error removing backup of {XMLFilename} before write")
                    raise

                # Move the current file to the backup location, write the new data
                try:
                    shutil.move(XMLFilename, BackupXMLFullPath)
                except PermissionError:
                    prettyoutput.LogErr(f"Permission error backing up {XMLFilename} before write")
                    raise
                    
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
