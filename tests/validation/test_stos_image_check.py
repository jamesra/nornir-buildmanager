"""Tests for shared STOS input-image freshness checks."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from nornir_buildmanager.validation.stos_image_check import (
    ImageCheckSnapshot,
    is_stos_input_image_outdated,
)
from nornir_buildmanager.volumemanager.stosgroupnode import StosGroupNode


def _write(path: Path, content: str = 'img') -> str:
    path.write_text(content, encoding='utf-8')
    return str(path.stat().st_size)


def test_outdated_when_image_missing(tmp_path: Path) -> None:
    output = tmp_path / 'out.stos'
    output.write_text('stos', encoding='utf-8')
    check = ImageCheckSnapshot('3', '3', str(tmp_path / 'missing.png'))
    assert is_stos_input_image_outdated(check, str(output))


def test_outdated_when_no_image_facts() -> None:
    assert is_stos_input_image_outdated(ImageCheckSnapshot('', None, None), '/tmp/out.stos')


def test_fresh_when_size_matches_and_image_older(tmp_path: Path) -> None:
    image = tmp_path / 'ctrl.png'
    output = tmp_path / 'out.stos'
    size = _write(image)
    output.write_text('stos', encoding='utf-8')
    os.utime(image, (1, 1))
    os.utime(output, (2, 2))
    check = ImageCheckSnapshot(size, size, str(image))
    assert not is_stos_input_image_outdated(check, str(output))


def test_outdated_when_live_size_differs_from_stored(tmp_path: Path) -> None:
    image = tmp_path / 'ctrl.png'
    output = tmp_path / 'out.stos'
    size = _write(image)
    output.write_text('stos', encoding='utf-8')
    os.utime(image, (1, 1))
    os.utime(output, (2, 2))
    check = ImageCheckSnapshot('999', size, str(image))
    assert is_stos_input_image_outdated(check, str(output))


def test_outdated_when_live_size_differs_from_image_checksum(tmp_path: Path) -> None:
    image = tmp_path / 'ctrl.png'
    output = tmp_path / 'out.stos'
    size = _write(image)
    output.write_text('stos', encoding='utf-8')
    os.utime(image, (1, 1))
    os.utime(output, (2, 2))
    check = ImageCheckSnapshot(size, '1', str(image))
    assert is_stos_input_image_outdated(check, str(output))


def test_outdated_when_image_newer_than_output(tmp_path: Path) -> None:
    image = tmp_path / 'ctrl.png'
    output = tmp_path / 'out.stos'
    size = _write(image)
    output.write_text('stos', encoding='utf-8')
    os.utime(output, (1, 1))
    os.utime(image, (2, 2))
    check = ImageCheckSnapshot(size, size, str(image))
    assert is_stos_input_image_outdated(check, str(output))


def test_stos_group_node_delegates_to_shared_check(tmp_path: Path) -> None:
    """Brute path helper uses the same always-stat implementation."""
    image_path = tmp_path / 'ctrl.png'
    stos_path = tmp_path / 'pair.stos'
    size = _write(image_path)
    stos_path.write_text('stos', encoding='utf-8')
    os.utime(image_path, (1, 1))
    os.utime(stos_path, (2, 2))

    stos_node = SimpleNamespace(
        FullPath=str(stos_path),
        attrib={'ControlImageChecksum': size},
    )
    image_node = SimpleNamespace(Checksum=size, FullPath=str(image_path))
    assert not StosGroupNode._IsStosInputImageOutdated(
        stos_node, 'ControlImageChecksum', image_node)

    os.utime(image_path, (3, 3))
    assert StosGroupNode._IsStosInputImageOutdated(
        stos_node, 'ControlImageChecksum', image_node)

    assert StosGroupNode._IsStosInputImageOutdated(
        stos_node, 'ControlImageChecksum', None)
