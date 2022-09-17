from __future__ import annotations

import datetime
import logging
import os
import nornir_buildmanager
import nornir_shared.files

from . import lockable
from . import xelementwrapper


class XResourceElementWrapper(lockable.Lockable,
                              xelementwrapper.XElementWrapper):
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

        if isinstance(self, nornir_buildmanager.volumemanager.XContainerElementWrapper):
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
