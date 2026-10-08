"""Unit tests for nornir_buildmanager.importers.find section discovery helpers."""

from __future__ import annotations

import re
from pathlib import Path

import nornir_shared.files as shared_files

from nornir_buildmanager.importers import find as find_mod
from nornir_buildmanager.importers import shared


def _mkdir_section(parent: Path, dirname: str) -> Path:
    section_dir = parent / dirname
    section_dir.mkdir()
    return section_dir


class TestFindSectionCandidates:
    def test_import_path_is_section_directory(self, tmp_path: Path) -> None:
        root = _mkdir_section(tmp_path, "7 NameHere.idoc")
        found = find_mod.find_section_candidates(str(root), None)
        assert list(found.keys()) == [7]
        assert found[7][0].number == 7
        assert found[7][0].fullpath == str(root)

    def test_collects_child_directories(self, tmp_path: Path) -> None:
        parent = tmp_path / "volume"
        parent.mkdir()
        _mkdir_section(parent, "1_NameHere")
        _mkdir_section(parent, "2_NameHere")
        found = find_mod.find_section_candidates(str(parent), None)
        assert set(found.keys()) == {1, 2}

    def test_same_section_versions_sorted_descending(self, tmp_path: Path) -> None:
        parent = tmp_path / "volume"
        parent.mkdir()
        _mkdir_section(parent, "5A_NameHere")
        _mkdir_section(parent, "5B_NameHere")
        found = find_mod.find_section_candidates(str(parent), None)
        assert len(found[5]) == 2
        assert [m.version for m in found[5]] == ["B", "A"]

    def test_desired_section_list_filters(self, tmp_path: Path) -> None:
        parent = tmp_path / "volume"
        parent.mkdir()
        _mkdir_section(parent, "3_NameHere")
        _mkdir_section(parent, "4_NameHere")
        found = find_mod.find_section_candidates(str(parent), [4])
        assert 3 not in found
        assert list(found.keys()) == [4]

    def test_skips_files_and_unparseable_directories(self, tmp_path: Path) -> None:
        parent = tmp_path / "volume"
        parent.mkdir()
        _mkdir_section(parent, "6_NameHere")
        (parent / "readme.txt").write_text("x", encoding="utf-8")
        _mkdir_section(parent, "not_a_section")
        found = find_mod.find_section_candidates(str(parent), None)
        assert list(found.keys()) == [6]


class TestFindSectionDirectoryMetadata:
    def test_frozenset_extension_match(self, tmp_path: Path) -> None:
        section_path = _mkdir_section(tmp_path, "8_NameHere")
        (section_path / "tile.idoc").write_text("", encoding="utf-8")
        (section_path / "tile.mrc").write_text("", encoding="utf-8")
        (section_path / "notes.txt").write_text("", encoding="utf-8")
        meta = shared.GetSectionInfo(str(section_path))
        idoc_pattern = shared_files.ensure_regex_or_set("*.idoc")
        assert isinstance(idoc_pattern, re.Pattern)
        returned_meta, matched = find_mod.find_section_directory_metadata(meta, idoc_pattern)
        assert returned_meta is meta
        assert len(matched) == 1
        assert matched[0].endswith("tile.idoc")

    def test_frozenset_filename_match(self, tmp_path: Path) -> None:
        section_path = _mkdir_section(tmp_path, "8B_NameHere")
        (section_path / "only.idoc").write_text("", encoding="utf-8")
        (section_path / "other.idoc").write_text("", encoding="utf-8")
        meta = shared.GetSectionInfo(str(section_path))
        _, matched = find_mod.find_section_directory_metadata(meta, frozenset({"only.idoc"}))
        assert len(matched) == 1
        assert matched[0].endswith("only.idoc")

    def test_regex_pattern_match(self, tmp_path: Path) -> None:
        section_path = _mkdir_section(tmp_path, "9_NameHere")
        (section_path / "a.idoc").write_text("", encoding="utf-8")
        (section_path / "b.idoc").write_text("", encoding="utf-8")
        meta = shared.GetSectionInfo(str(section_path))
        _, matched = find_mod.find_section_directory_metadata(meta, re.compile(r"^a\.idoc$"))
        assert len(matched) == 1
        assert matched[0].endswith("a.idoc")


class TestSectionDirectoryMetadataGenerator:
    def test_preserves_input_order(self, tmp_path: Path) -> None:
        metas: list[shared.FilenameMetadata] = []
        for number in (10, 11, 12):
            section_path = _mkdir_section(tmp_path, f"{number}_NameHere")
            (section_path / "capture.idoc").write_text("", encoding="utf-8")
            metas.append(shared.GetSectionInfo(str(section_path)))
        idoc_pattern = shared_files.ensure_regex_or_set("*.idoc")
        assert isinstance(idoc_pattern, re.Pattern)
        rows = list(find_mod.section_directory_metadata_generator(metas, idoc_pattern))
        assert [row[0].number for row in rows] == [10, 11, 12]
        assert all(len(row[1]) == 1 for row in rows)
