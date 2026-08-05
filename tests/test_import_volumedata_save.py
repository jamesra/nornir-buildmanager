"""Regression: ImportIDoc VolumeData saves via dirty-flag contract."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock
from xml.etree import ElementTree

from nornir_buildmanager.pipelinemanager import PipelineManager
from nornir_buildmanager.volumemanager import (
    BlockNode,
    ChannelNode,
    SectionNode,
    VolumeManager,
)


class TestImportVolumeDataSave(unittest.TestCase):
    """Per-idoc Import yield must persist dirty linked containers."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.mkdtemp(prefix="nornir-import-vd-")
        self.addCleanup(lambda: shutil.rmtree(self._temp_dir, ignore_errors=True))
        self.volume = VolumeManager.Load(self._temp_dir, Create=True)

    def _block_xml_path(self) -> str:
        return os.path.join(self._temp_dir, "TEM", "VolumeData.xml")

    def _volume_xml_path(self) -> str:
        return os.path.join(self._temp_dir, "VolumeData.xml")

    def _section_xml_path(self, section: str = "1001") -> str:
        return os.path.join(self._temp_dir, "TEM", section, "VolumeData.xml")

    def _channel_xml_path(self, section: str = "1001") -> str:
        return os.path.join(self._temp_dir, "TEM", section, "TEM", "VolumeData.xml")

    def _section_link_count(self) -> int:
        xml_path = self._block_xml_path()
        if not os.path.isfile(xml_path):
            return 0
        root = ElementTree.parse(xml_path).getroot()
        return len(root.findall("Section_Link"))

    def _append_section_and_return_save_root(self, volume_obj, section_number: int):
        """Mimic ToMosaic tree mutation and return policy (Volume then Block)."""
        block = BlockNode.Create("TEM")
        save_block, block = volume_obj.UpdateOrAddChild(block)
        section = SectionNode.Create(section_number, str(section_number), str(section_number))
        block.UpdateOrAddChildByAttrib(section, "Number")
        if save_block:
            return volume_obj
        return block

    def test_save_nodes_skips_none_yield(self) -> None:
        with mock.patch.object(VolumeManager, "Save") as save:
            PipelineManager._SaveNodes(iter([None, None]))
            save.assert_not_called()

    def test_append_dirties_parent_save_writes_and_clears_flags(self) -> None:
        """Mutation sets ChildrenChanged; Save writes VolumeData.xml; Reset clears flags."""
        block = BlockNode.Create("TEM")
        added, block = self.volume.UpdateOrAddChild(block)
        self.assertTrue(added)
        self.assertTrue(self.volume.ChildrenChanged)

        VolumeManager.Save(self.volume)
        self.assertTrue(os.path.isfile(self._volume_xml_path()))
        self.assertTrue(os.path.isfile(self._block_xml_path()))
        self.assertFalse(self.volume.ChildrenChanged)
        self.assertFalse(self.volume.AttributesChanged)
        self.assertFalse(block.ChildrenChanged)

    def test_channel_only_mutation_writes_channel_not_block(self) -> None:
        """Linked channel dirty does not rewrite Block XML (dirtiness does not bubble)."""
        block = BlockNode.Create("TEM")
        _, block = self.volume.UpdateOrAddChild(block)
        section = SectionNode.Create(1001, "1001", "1001")
        _, section = block.UpdateOrAddChildByAttrib(section, "Number")
        channel = ChannelNode.Create("TEM")
        _, channel = section.UpdateOrAddChildByAttrib(channel, "Name")
        VolumeManager.Save(self.volume)

        block_mtime = os.path.getmtime(self._block_xml_path())
        channel_path = self._channel_xml_path("1001")
        self.assertTrue(os.path.isfile(channel_path))
        channel_mtime = os.path.getmtime(channel_path)

        block.ResetElementChangeFlags()
        section.ResetElementChangeFlags()
        channel.ResetElementChangeFlags()
        self.assertFalse(block.ChildrenChanged)
        self.assertFalse(block.ElementHasChangesToSave)

        channel.Name = "TEM_renamed"
        self.assertTrue(channel.AttributesChanged)
        self.assertFalse(block.ChildrenChanged)
        self.assertFalse(block.ElementHasChangesToSave)

        time.sleep(0.05)
        VolumeManager.Save(block)

        self.assertGreater(os.path.getmtime(channel_path), channel_mtime)
        self.assertEqual(os.path.getmtime(self._block_xml_path()), block_mtime)

    def test_import_yield_updates_tem_and_volume_xml_per_idoc(self) -> None:
        from nornir_buildmanager.importers import idoc as idoc_mod

        section_a = mock.Mock()
        section_b = mock.Mock()
        found = [
            (section_a, ["a.idoc"]),
            (section_b, ["b.idoc"]),
        ]
        call_state = {"n": 0}

        def fake_to_mosaic(volume_obj, idoc_path, **kwargs):
            call_state["n"] += 1
            yield self._append_section_and_return_save_root(volume_obj, 1000 + call_state["n"])

        import_root = os.path.join(self._temp_dir, "import_src")
        os.makedirs(import_root, exist_ok=True)

        with mock.patch.object(idoc_mod, "find_sections", return_value=iter(found)):
            with mock.patch.object(idoc_mod.find, "find_section_candidates", return_value={}):
                with mock.patch.object(idoc_mod.nornir_buildmanager.importers, "GetFlipList", return_value=[]):
                    with mock.patch.object(
                            idoc_mod.nornir_buildmanager.importers,
                            "LoadHistogramCutoffs",
                            return_value={}):
                        with mock.patch.object(
                                idoc_mod.serialem_utils,
                                "get_import_cache_path",
                                return_value=import_root):
                            with mock.patch.object(
                                    idoc_mod.SerialEMIDocImport,
                                    "ToMosaic",
                                    side_effect=fake_to_mosaic):
                                with mock.patch.object(idoc_mod, "report_iterate"):
                                    with mock.patch.object(idoc_mod.nornir_pools, "ReleaseStagePools"):
                                        PipelineManager._SaveNodes(
                                            idoc_mod.Import(
                                                self.volume,
                                                ImportPath=import_root,
                                                Min=0.1,
                                                Max=0.9,
                                            )
                                        )

        self.assertTrue(os.path.isfile(self._volume_xml_path()), "volume root VolumeData.xml after first block")
        volume_root = ElementTree.parse(self._volume_xml_path()).getroot()
        self.assertGreaterEqual(len(volume_root.findall("Block_Link")), 1)

        self.assertTrue(os.path.isfile(self._block_xml_path()), "TEM/VolumeData.xml after sections")
        self.assertEqual(self._section_link_count(), 2)

    def test_to_mosaic_empty_tiles_returns_block_not_none(self) -> None:
        """Early exit after Block/Section create must still yield a save root."""
        from nornir_buildmanager.importers import idoc as idoc_mod

        section_dir = os.path.join(self._temp_dir, "src", "0001")
        os.makedirs(section_dir, exist_ok=True)
        idoc_path = os.path.join(section_dir, "0001.idoc")
        with open(idoc_path, "w", encoding="utf-8") as handle:
            handle.write("PixelSpacing = 1.0\nDataMode = 1\nImageSize = 64 64\n")

        fake_idoc = mock.Mock()
        fake_idoc.NumTiles = 0
        fake_idoc.PixelSpacing = 1.0
        fake_idoc.DataMode = 1
        fake_idoc.ImageSize = (64, 64)

        with mock.patch.object(idoc_mod.serialem_utils, "GetPathWithoutSpaces", return_value=idoc_path):
            with mock.patch.object(
                    idoc_mod.shared,
                    "GetSectionInfo",
                    return_value=mock.Mock(number=1, name="0001")):
                with mock.patch.object(idoc_mod.IDoc, "Load", return_value=fake_idoc):
                    results = list(idoc_mod.SerialEMIDocImport.ToMosaic(
                        self.volume,
                        idoc_path,
                        ContrastCutoffs=(0.1, 0.9),
                    ))

        self.assertGreaterEqual(len(results), 1)
        self.assertTrue(
            any(r is self.volume or getattr(r, "tag", None) == "Block" for r in results),
            f"expected Volume or Block among yields, got {results!r}",
        )
        for node in results:
            PipelineManager._SaveNodes(node)
        self.assertTrue(os.path.isfile(self._volume_xml_path()))
        self.assertTrue(os.path.isfile(self._block_xml_path()))
        self.assertEqual(self._section_link_count(), 1)


if __name__ == "__main__":
    unittest.main()
