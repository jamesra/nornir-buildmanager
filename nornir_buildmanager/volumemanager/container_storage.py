"""Storage seam for container meta-data (metadata port stage 1).

Containers read and write their meta-data through a :class:`ContainerStorage`.
:class:`XmlContainerStorage`, which reads and writes ``VolumeData.xml`` in the
container directory, is the only implementation and the source of truth.
"""

from __future__ import annotations

import errno
import os
import time
from typing import Protocol
from xml.etree import ElementTree

from nornir_shared import prettyoutput
from nornir_shared.files import ensure_directory

VOLUME_DATA_FILENAME = 'VolumeData.xml'


class ContainerStorage(Protocol):
    """Reads and writes the meta-data element owned by one container directory."""

    def load_container(self, container_dir: str) -> ElementTree.Element:
        """Return the root element stored for *container_dir*."""
        ...

    def save_container(self, container_dir: str, element: ElementTree.Element) -> None:
        """Replace the meta-data stored for *container_dir* with *element*."""
        ...


class XmlContainerStorage:
    """``VolumeData.xml`` in the container directory, written via temp file, backup, and replace."""

    filename: str

    def __init__(self, filename: str = VOLUME_DATA_FILENAME) -> None:
        self.filename = filename

    def load_container(self, container_dir: str) -> ElementTree.Element:
        """Parse ``<container_dir>/<filename>`` and return its root element.

        Raises ``OSError`` when the file cannot be read and ``ElementTree.ParseError``
        when it is not well-formed XML.
        """
        # I could use ElementTree.parse here.  However, there was a rare
        # bug where saving the file would encounter a permissions error
        # loading the file and closing it myself seems to have solved
        # the problem
        with open(os.path.join(container_dir, self.filename), 'rb') as hFile:
            RawXML = hFile.read()
        return ElementTree.fromstring(RawXML)

    def save_container(self, container_dir: str, element: ElementTree.Element) -> None:
        """Indent *element* in place and atomically replace ``<container_dir>/<filename>``.

        A non-empty existing file is moved to ``<filename>.backup.xml`` first. Writes and
        replaces retry on the transient errors seen on CIFS/NFS shares.
        """
        try:
            ElementTree.indent(element, space='  ')
        except Exception as e:
            prettyoutput.Log(f"Cannot encode output XML:\n{e}")
            raise

        container_dir = ensure_directory(container_dir)

        BackupXMLFilename = f"{os.path.basename(self.filename)}.backup.xml"
        BackupXMLFullPath = os.path.join(container_dir, BackupXMLFilename)
        XMLFilename = os.path.join(container_dir, self.filename)
        TmpFilename = XMLFilename + ".tmp"

        def write_temp_file() -> None:
            try:
                with open(TmpFilename, 'wb') as hFile:
                    ElementTree.ElementTree(element).write(
                        hFile,
                        encoding='utf-8',
                        xml_declaration=False,
                        short_empty_elements=True,
                    )
            except Exception as e:
                try:
                    os.remove(TmpFilename)
                except FileNotFoundError:
                    pass
                if isinstance(e, OSError):
                    raise
                prettyoutput.Log(f"Cannot encode output XML:\n{e}")
                raise

            if os.path.getsize(TmpFilename) == 0:
                raise Exception(
                    f"No meta data produced for XML element {element} writing to {self.filename}")

        last_open_error: OSError | None = None
        for attempt in range(5):
            try:
                write_temp_file()
                break
            except FileNotFoundError as e:
                last_open_error = e
                ensure_directory(container_dir)
                time.sleep(0.05 * (attempt + 1))
            except OSError as e:
                if e.errno == errno.EMFILE:
                    raise OSError(
                        errno.EMFILE,
                        "Too many open files; raise ulimit -n or reduce tile I/O concurrency",
                        XMLFilename,
                    ) from e
                raise
        else:
            raise FileNotFoundError(
                f"Unable to write {TmpFilename} after retries; last error: {last_open_error}"
            ) from last_open_error

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
                backup_ok = False
                backup_blocked = False
                for backup_attempt in range(8):
                    try:
                        os.replace(XMLFilename, BackupXMLFullPath)
                        backup_ok = True
                        break
                    except FileNotFoundError as e:
                        prettyoutput.LogErr(
                            f"Could not backup {XMLFilename} to {BackupXMLFullPath} ({e}); continuing without backup")
                        break
                    except PermissionError:
                        backup_blocked = True
                        prettyoutput.Log(
                            f"XML backup retry {backup_attempt + 1}/8, file in use: {XMLFilename}")
                        time.sleep(min(8.0, 0.5 * (2 ** backup_attempt)))
                    except OSError as e:
                        if e.errno == errno.EMFILE:
                            prettyoutput.LogErr(
                                f"Too many open files backing up {XMLFilename}; continuing without backup. "
                                "Raise ulimit -n or reduce tile I/O concurrency.")
                        else:
                            prettyoutput.LogErr(
                                f"Could not backup {XMLFilename} to {BackupXMLFullPath} ({e}); continuing without backup")
                        break
                if not backup_ok and backup_blocked:
                    prettyoutput.LogErr(
                        f"Permission error backing up {XMLFilename} before write; continuing without backup")

            else:
                # This is a rare issue where I'd write a file but have zero bytes on disk.
                # If this error occurs check into replacing the zero byte file with the backup if it exists
                prettyoutput.LogErr(f"{XMLFilename} had zero size, did not backup on write")
        except FileNotFoundError:
            pass

        for attempt in range(8):
            try:
                os.replace(TmpFilename, XMLFilename)
                return
            except FileNotFoundError as e:
                # Parent dir vanished or not yet visible (Clean race / CIFS cache).
                last_open_error = e
                ensure_directory(container_dir)
                write_temp_file()
                time.sleep(0.05 * (attempt + 1))
            except PermissionError as e:
                # SMB/CIFS often holds a directory handle after a large folder move
                # (WinError 32). Back off and retry the replace.
                last_open_error = e
                prettyoutput.Log(
                    f"XML replace retry {attempt + 1}/8, file in use: {XMLFilename}")
                time.sleep(min(8.0, 0.5 * (2 ** attempt)))
            except OSError as e:
                if e.errno == errno.EMFILE:
                    raise OSError(
                        errno.EMFILE,
                        "Too many open files; raise ulimit -n or reduce tile I/O concurrency",
                        XMLFilename,
                    ) from e
                raise

        if last_open_error is not None:
            raise last_open_error
        raise FileNotFoundError(f"Unable to write {XMLFilename} after retries")


XML_CONTAINER_STORAGE = XmlContainerStorage()
