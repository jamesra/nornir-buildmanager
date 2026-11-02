"""Repeated saves of one container check its directory once per process.

``ensure_directory`` lists every ancestor directory and writes a probe file, which on
CIFS is several round trips. Grid refine saved the same StosGroup container after
every pair, so it paid that hundreds of times per stage (srv, 2026-11-02).
"""
from __future__ import annotations

import os
import shutil
from xml.etree import ElementTree

import pytest
from nornir_buildmanager.volumemanager import container_storage


@pytest.fixture
def counted(monkeypatch):
    calls: list[str] = []
    real = container_storage.ensure_directory

    def counting(path, *args, **kwargs):
        calls.append(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(container_storage, 'ensure_directory', counting)
    monkeypatch.setattr(container_storage, '_verified_container_dirs', set())
    return calls


def _save(directory: str, value: str) -> None:
    container_storage.XmlContainerStorage('Test.xml').save_container(
        directory, ElementTree.Element('Container', attrib={'Value': value}))


def test_repeated_saves_check_the_directory_once(tmp_path, counted):
    directory = os.path.join(str(tmp_path), 'group')
    for i in range(5):
        _save(directory, str(i))

    assert len(counted) == 1
    root = ElementTree.parse(os.path.join(directory, 'Test.xml')).getroot()
    assert root.attrib['Value'] == '4'


def test_each_directory_is_checked(tmp_path, counted):
    for name in ('a', 'b', 'a', 'b'):
        _save(os.path.join(str(tmp_path), name), name)

    assert len(counted) == 2


def test_a_removed_directory_is_recreated(tmp_path, counted):
    """The write retry still calls ensure_directory when the directory has gone."""
    directory = os.path.join(str(tmp_path), 'group')
    _save(directory, 'first')
    shutil.rmtree(directory)

    _save(directory, 'second')

    assert len(counted) >= 2
    root = ElementTree.parse(os.path.join(directory, 'Test.xml')).getroot()
    assert root.attrib['Value'] == 'second'
