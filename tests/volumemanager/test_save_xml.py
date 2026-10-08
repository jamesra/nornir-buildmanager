"""Tests for resilient VolumeData.xml save behavior and the container storage seam."""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
import unittest
from unittest import mock
from xml.etree import ElementTree

from hypothesis import given, settings
from hypothesis import strategies as st

import nornir_buildmanager.volumemanager as vm
from nornir_buildmanager.volumemanager import container_storage
from nornir_buildmanager.volumemanager.container_storage import XmlContainerStorage
from nornir_buildmanager.volumemanager.xcontainerelementwrapper import (
    XContainerElementWrapper,
)
from nornir_buildmanager.volumemanager.xelementwrapper import XElementWrapper


class _TestContainer(XContainerElementWrapper):
    """Minimal concrete container for exercising save paths."""

    def __init__(self, full_path: str, tag: str = 'TestContainer') -> None:
        path_name = os.path.basename(full_path.rstrip(os.sep))
        super().__init__(tag, attrib={'Path': path_name})
        self._full_path = full_path
        os.makedirs(full_path, exist_ok=True)

    @property
    def FullPath(self) -> str:
        return self._full_path


class _MemoryStorage:
    """Container storage held in a dict, so any direct file I/O by the wrapper shows up on disk."""

    def __init__(self) -> None:
        self.saved: dict[str, bytes] = {}
        self.loads: list[str] = []

    def load_container(self, container_dir: str) -> ElementTree.Element:
        self.loads.append(container_dir)
        try:
            return ElementTree.fromstring(self.saved[container_dir])
        except KeyError:
            raise FileNotFoundError(container_dir) from None

    def save_container(self, container_dir: str, element: ElementTree.Element) -> None:
        self.saved[container_dir] = ElementTree.tostring(element, encoding='utf-8')


class _RecordingStorage(XmlContainerStorage):
    """XML storage that records which directories were loaded and saved."""

    def __init__(self) -> None:
        super().__init__()
        self.loads: list[str] = []
        self.saves: list[str] = []

    def load_container(self, container_dir: str) -> ElementTree.Element:
        self.loads.append(container_dir)
        return super().load_container(container_dir)

    def save_container(self, container_dir: str, element: ElementTree.Element) -> None:
        self.saves.append(container_dir)
        super().save_container(container_dir, element)


def _make_nonempty_dir(path: str) -> None:
    """Linked containers are cleaned on load unless their directory exists and holds a file."""
    os.makedirs(path, exist_ok=True)
    open(os.path.join(path, 'placeholder.png'), 'wb').close()


def _volume_data_files(root: str) -> list[str]:
    return sorted(os.path.relpath(dirpath, root) for dirpath, _, files in os.walk(root) if 'VolumeData.xml' in files)


def _canonical(element: ElementTree.Element) -> tuple:
    """Tag, ordered attributes, non-whitespace text, children; ignores indentation whitespace."""
    text = element.text if element.text and element.text.strip() else None
    return element.tag, tuple(element.attrib.items()), text, tuple(_canonical(child) for child in element)


_names = st.text(alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_', min_size=1, max_size=8)
# Cc and Cn cover the characters XML 1.0 cannot carry (controls, U+FFFE/U+FFFF).
_values = st.text(st.characters(codec='utf-8', exclude_categories=('Cs', 'Cc', 'Cn')), max_size=12)
# Indentation only replaces whitespace-only text, so leaf text excludes whitespace to stay comparable.
_texts = st.text(st.characters(codec='utf-8', exclude_categories=('Cs', 'Cc', 'Cn', 'Zs', 'Zl', 'Zp')),
                 min_size=1, max_size=12)


@st.composite
def _elements(draw: st.DrawFn, depth: int = 3) -> ElementTree.Element:
    element = ElementTree.Element(draw(_names), attrib=draw(st.dictionaries(_names, _values, max_size=4)))
    children = draw(st.lists(_elements(depth=depth - 1), max_size=3)) if depth > 0 else []
    element.extend(children)
    if not children:
        element.text = draw(st.none() | _texts)
    return element


class TestSaveXml(unittest.TestCase):
    """VolumeData.xml backup and atomic write behavior."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.mkdtemp(prefix='nornir-save-xml-')
        self.addCleanup(lambda: shutil.rmtree(self._temp_dir, ignore_errors=True))
        self._storage = XmlContainerStorage()
        self._element = ElementTree.Element('Volume', attrib={'Name': 'test'})

    def test_atomic_write_creates_volume_data(self) -> None:
        """New saves write valid XML via temp + replace."""
        self._storage.save_container(self._temp_dir, self._element)

        xml_path = os.path.join(self._temp_dir, 'VolumeData.xml')
        self.assertTrue(os.path.isfile(xml_path))
        self.assertGreater(os.path.getsize(xml_path), 0)
        self.assertFalse(os.path.exists(xml_path + '.tmp'))

    def test_backup_failure_does_not_block_write(self) -> None:
        """CIFS-style backup rename failure still writes new metadata."""
        xml_path = os.path.join(self._temp_dir, 'VolumeData.xml')
        with open(xml_path, 'wb') as handle:
            handle.write(b'<Volume Name="old"/>')

        original_replace = os.replace
        replace_calls = {'count': 0}

        def fake_replace(src: str, dst: str) -> None:
            replace_calls['count'] += 1
            if replace_calls['count'] == 1:
                raise FileNotFoundError('stale CIFS stat')
            return original_replace(src, dst)

        with mock.patch.object(container_storage.os, 'replace', side_effect=fake_replace):
            self._storage.save_container(self._temp_dir, self._element)

        self.assertEqual(replace_calls['count'], 2)
        with open(xml_path, 'rb') as handle:
            saved = handle.read()
        self.assertIn(b'Name="test"', saved)
        self.assertNotIn(b'Name="old"', saved)
        self.assertFalse(os.path.exists(os.path.join(self._temp_dir, 'VolumeData.xml.backup.xml')))

    def test_backup_oserror_does_not_block_write(self) -> None:
        """Any other OSError while moving the old file aside still writes the new metadata."""
        xml_path = os.path.join(self._temp_dir, 'VolumeData.xml')
        with open(xml_path, 'wb') as handle:
            handle.write(b'<Volume Name="old"/>')

        original_replace = os.replace

        def fake_replace(src: str, dst: str) -> None:
            if dst.endswith('.backup.xml'):
                raise OSError(errno.EBUSY, 'busy')
            return original_replace(src, dst)

        with mock.patch.object(container_storage.os, 'replace', side_effect=fake_replace):
            self._storage.save_container(self._temp_dir, self._element)

        with open(xml_path, 'rb') as handle:
            self.assertEqual(handle.read(), b'<Volume Name="test" />')

    def test_replace_retries_after_directory_vanishes(self) -> None:
        """A FileNotFoundError on the final replace recreates the directory, rewrites the temp file, and retries."""
        original_replace = os.replace
        calls = {'count': 0}

        def vanish_once(src: str, dst: str) -> None:
            calls['count'] += 1
            if calls['count'] == 1:
                shutil.rmtree(self._temp_dir)
                raise FileNotFoundError('directory vanished')
            return original_replace(src, dst)

        with (mock.patch.object(container_storage.os, 'replace', side_effect=vanish_once),
              mock.patch.object(container_storage.time, 'sleep')):
            self._storage.save_container(self._temp_dir, self._element)

        self.assertEqual(calls['count'], 2)
        with open(os.path.join(self._temp_dir, 'VolumeData.xml'), 'rb') as handle:
            self.assertEqual(handle.read(), b'<Volume Name="test" />')

    def test_replace_gives_up_after_eight_attempts_with_last_error(self) -> None:
        with (mock.patch.object(container_storage.os, 'replace', side_effect=PermissionError('in use')) as replace,
              mock.patch.object(container_storage.time, 'sleep'),
              self.assertRaises(PermissionError)):
            self._storage.save_container(self._temp_dir, self._element)
        self.assertEqual(replace.call_count, 8)

    def test_temp_write_gives_up_after_five_attempts(self) -> None:
        with (mock.patch.object(container_storage, 'open', create=True, side_effect=FileNotFoundError('gone')) as opener,
              mock.patch.object(container_storage.time, 'sleep'),
              self.assertRaises(FileNotFoundError)):
            self._storage.save_container(self._temp_dir, self._element)
        self.assertEqual(opener.call_count, 5)

    def test_encode_failure_removes_temp_file(self) -> None:
        """A tree ElementTree cannot serialize leaves neither a temp file nor a VolumeData.xml."""
        element = ElementTree.Element('Volume', attrib={'Count': 1})  # type: ignore[dict-item]
        with self.assertRaises(TypeError):
            self._storage.save_container(self._temp_dir, element)
        self.assertEqual(os.listdir(self._temp_dir), [])

    def test_non_ascii_is_written_as_utf8(self) -> None:
        self._storage.save_container(self._temp_dir, ElementTree.Element('Notes', attrib={'Text': '\u00b5m'}))
        with open(os.path.join(self._temp_dir, 'VolumeData.xml'), 'rb') as handle:
            self.assertEqual(handle.read(), '<Notes Text="\u00b5m" />'.encode('utf-8'))

    def test_previous_bytes_move_to_backup(self) -> None:
        """A save over a non-empty file keeps the old bytes as VolumeData.xml.backup.xml."""
        self._storage.save_container(self._temp_dir, self._element)
        xml_path = os.path.join(self._temp_dir, 'VolumeData.xml')
        with open(xml_path, 'rb') as handle:
            first = handle.read()

        self._storage.save_container(self._temp_dir, ElementTree.Element('Volume', attrib={'Name': 'second'}))

        with open(os.path.join(self._temp_dir, 'VolumeData.xml.backup.xml'), 'rb') as handle:
            self.assertEqual(handle.read(), first)
        with open(xml_path, 'rb') as handle:
            self.assertEqual(handle.read(), b'<Volume Name="second" />')

    def test_empty_file_is_not_backed_up(self) -> None:
        """A zero-byte VolumeData.xml must not replace a good backup."""
        backup_path = os.path.join(self._temp_dir, 'VolumeData.xml.backup.xml')
        with open(backup_path, 'wb') as handle:
            handle.write(b'<Volume Name="good" />')
        open(os.path.join(self._temp_dir, 'VolumeData.xml'), 'wb').close()

        self._storage.save_container(self._temp_dir, self._element)

        with open(backup_path, 'rb') as handle:
            self.assertEqual(handle.read(), b'<Volume Name="good" />')

    def test_load_missing_file_raises_oserror(self) -> None:
        """Link resolution relies on OSError for a missing linked VolumeData.xml."""
        with self.assertRaises(OSError):
            self._storage.load_container(self._temp_dir)

    def test_load_malformed_file_raises_parse_error(self) -> None:
        with open(os.path.join(self._temp_dir, 'VolumeData.xml'), 'wb') as handle:
            handle.write(b'<Volume')
        with self.assertRaises(ElementTree.ParseError):
            self._storage.load_container(self._temp_dir)


@settings(max_examples=60, deadline=None)
@given(element=_elements())
def test_round_trip_preserves_tree_and_resave_is_byte_identical(element: ElementTree.Element) -> None:
    """save -> load returns the same tree; saving the loaded tree again writes the same bytes."""
    storage = XmlContainerStorage()
    with tempfile.TemporaryDirectory(prefix='nornir-save-xml-') as container_dir:
        expected = _canonical(element)
        storage.save_container(container_dir, element)
        xml_path = os.path.join(container_dir, 'VolumeData.xml')
        with open(xml_path, 'rb') as handle:
            first = handle.read()

        loaded = storage.load_container(container_dir)
        assert _canonical(loaded) == expected

        storage.save_container(container_dir, loaded)
        with open(xml_path, 'rb') as handle:
            assert handle.read() == first


class TestContainerStorageSeam(unittest.TestCase):
    """Containers and VolumeManager.Load read and write meta-data only through XContainerElementWrapper.storage."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.mkdtemp(prefix='nornir-storage-seam-')
        self.addCleanup(lambda: shutil.rmtree(self._temp_dir, ignore_errors=True))

    def test_wrapper_saves_and_resolves_links_only_through_storage(self) -> None:
        storage = _MemoryStorage()
        child_dir = os.path.join(self._temp_dir, 'TEM')
        with mock.patch.object(XContainerElementWrapper, 'storage', storage):
            parent = _TestContainer(self._temp_dir)
            parent.append(vm.BlockNode.Create('TEM'))
            parent.ChildrenChanged = True
            parent._Save(recurse=True)

            self.assertEqual(_volume_data_files(self._temp_dir), [])
            self.assertEqual(set(storage.saved), {self._temp_dir, child_dir})
            saved_parent = ElementTree.fromstring(storage.saved[self._temp_dir])
            self.assertEqual([link.tag for link in saved_parent], ['Block_Link'])

            _make_nonempty_dir(child_dir)
            reloaded = _TestContainer(self._temp_dir)
            stub = XElementWrapper('Block_Link', attrib=dict(saved_parent[0].attrib))
            reloaded.append(stub)
            resolved = reloaded._replace_link(stub)

        self.assertEqual(storage.loads, [child_dir])
        self.assertIsInstance(resolved, vm.BlockNode)
        self.assertEqual([child.tag for child in reloaded], ['Block'])

    def test_replace_links_loads_every_link_through_storage(self) -> None:
        storage = _MemoryStorage()
        names = ['A', 'B', 'C']
        with mock.patch.object(XContainerElementWrapper, 'storage', storage):
            for name in names:
                _make_nonempty_dir(os.path.join(self._temp_dir, name))
                storage.save_container(os.path.join(self._temp_dir, name), vm.BlockNode.Create(name))
            parent = _TestContainer(self._temp_dir)
            stubs: list[ElementTree.Element] = [
                XElementWrapper('Block_Link', attrib={'Path': name, 'Name': name}) for name in names]
            parent.extend(stubs)
            parent._replace_links(stubs)

        self.assertEqual(sorted(storage.loads), [os.path.join(self._temp_dir, name) for name in names])
        self.assertEqual(sorted(child.attrib['Path'] for child in parent if isinstance(child, vm.BlockNode)), names)
        self.assertEqual(len(parent), len(names))

    def test_volume_load_and_save_use_storage(self) -> None:
        storage = _RecordingStorage()
        root = os.path.join(self._temp_dir, 'vol')
        with mock.patch.object(XContainerElementWrapper, 'storage', storage):
            volume = vm.VolumeManager.Load(root, Create=True)
            assert volume is not None
            volume.UpdateOrAddChildByAttrib(vm.BlockNode.Create('TEM'), 'Name')
            vm.VolumeManager.Save(volume)
            saved = {os.path.relpath(path, root) for path in storage.saves}

            reloaded = vm.VolumeManager.Load(root)
            assert reloaded is not None
            reloaded.LoadAllLinkedNodes()

        self.assertEqual(saved, set(_volume_data_files(root)))
        self.assertEqual(sorted(os.path.relpath(path, root) for path in storage.loads), ['.', 'TEM'])


class TestDuplicateLinkCleanup(unittest.TestCase):
    """Saving must drop same-Path link duplicates from the in-memory tree."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.mkdtemp(prefix='nornir-dup-link-')
        self.addCleanup(lambda: shutil.rmtree(self._temp_dir, ignore_errors=True))
        self._storage = _RecordingStorage()
        patcher = mock.patch.object(XContainerElementWrapper, 'storage', self._storage)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._parent = _TestContainer(self._temp_dir)
        self._child_dir = os.path.join(self._temp_dir, 'TEM')

    def _same_path_children(self) -> list[ElementTree.Element]:
        return [
            child for child in list(self._parent)
            if child.attrib.get('Path') == 'TEM'
            and (child.tag == 'TestContainer' or child.tag == 'TestContainer_Link')
        ]

    def _saved_links(self) -> list[ElementTree.Element]:
        """Same-Path links in the parent VolumeData.xml, read back through the storage the save used."""
        self.assertIn(self._temp_dir, self._storage.saves)
        saved = self._storage.load_container(self._temp_dir)
        return saved.findall("TestContainer_Link[@Path='TEM']")

    def test_two_loaded_containers_same_path_keeps_one(self) -> None:
        first = _TestContainer(self._child_dir)
        second = _TestContainer(self._child_dir)
        self._parent.append(first)
        self._parent.append(second)
        self._parent.ChildrenChanged = True

        self._parent._Save(recurse=True)

        remaining = self._same_path_children()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].tag, 'TestContainer')
        self.assertEqual(len(self._saved_links()), 1)

    def test_stub_then_loaded_prefers_loaded(self) -> None:
        stub = XElementWrapper('TestContainer_Link', attrib={'Path': 'TEM'})
        loaded = _TestContainer(self._child_dir)
        self._parent.append(stub)
        self._parent.append(loaded)
        self._parent.ChildrenChanged = True

        self._parent._Save(recurse=True)

        remaining = self._same_path_children()
        self.assertEqual(len(remaining), 1)
        self.assertIs(remaining[0], loaded)
        self.assertEqual(len(self._saved_links()), 1)

    def test_loaded_then_stub_prefers_loaded(self) -> None:
        loaded = _TestContainer(self._child_dir)
        stub = XElementWrapper('TestContainer_Link', attrib={'Path': 'TEM'})
        self._parent.append(loaded)
        self._parent.append(stub)
        self._parent.ChildrenChanged = True

        self._parent._Save(recurse=True)

        remaining = self._same_path_children()
        self.assertEqual(len(remaining), 1)
        self.assertIs(remaining[0], loaded)
        self.assertEqual(len(self._saved_links()), 1)


if __name__ == '__main__':
    unittest.main()
