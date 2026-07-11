"""Tests for the SyncValidationTimes maintenance operation.

These verify that ValidationTime metadata is realigned to current filesystem
timestamps after a volume copy so pyramid levels do not look modified-since-last
-validation (which would trigger spurious tile-pyramid rebuilds).
"""
import os
import tempfile
import unittest

import nornir_buildmanager.operations.migration as migration
import nornir_buildmanager.volumemanager as volumemanager
from nornir_buildmanager.volumemanager.volumemanager import VolumeManager


STALE_VALIDATION_TIME = "2000-01-01 00:00:00"


def _write_linked_volume(root_dir: str) -> None:
    """Create a minimal on-disk volume that uses linked containers, like real volumes.

    The TilePyramid is stored as a linked element (its own VolumeData.xml), so the
    Level metadata is persisted by rewriting the TilePyramid's file rather than a single
    monolithic volume file. This mirrors how nornir stores Block/Channel/Filter/TilePyramid
    containers and is what makes a deep Level change persist on save.
    """
    pyramid_dir = os.path.join(root_dir, "TilePyramid")
    level_dir = os.path.join(pyramid_dir, "001")
    os.makedirs(level_dir, exist_ok=True)
    with open(os.path.join(level_dir, "000.png"), "wb") as tile_file:
        tile_file.write(b"not-a-real-png")

    with open(os.path.join(root_dir, "VolumeData.xml"), "w", encoding="utf-8") as volume_file:
        volume_file.write(
            '<Volume Name="Test" Path="">'
            '<TilePyramid_Link Path="TilePyramid" />'
            '</Volume>'
        )

    with open(os.path.join(pyramid_dir, "VolumeData.xml"), "w", encoding="utf-8") as pyramid_file:
        pyramid_file.write(
            '<TilePyramid Path="TilePyramid" NumberOfTiles="1" ImageFormatExt=".png" LevelFormat="%03d">'
            f'<Level Path="001" Downsample="1" ValidationTime="{STALE_VALIDATION_TIME}" TilesValidated="1" />'
            '</TilePyramid>'
        )


def _get_pyramid_node(volume_node):
    volume_node.LoadAllLinkedNodes()
    return volume_node.find("TilePyramid")


def _get_level_node(volume_node):
    pyramid = _get_pyramid_node(volume_node)
    return None if pyramid is None else pyramid.find("Level")


class TestResourceTargeting(unittest.TestCase):
    """Verify which node types are targeted for validation-time sync."""

    def test_level_node_is_targeted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_linked_volume(temp_dir)
            volume = VolumeManager.Load(temp_dir)
            level = _get_level_node(volume)
            self.assertIsNotNone(level)
            self.assertTrue(migration._ResourceUsesFilesystemValidation(level))

    def test_linked_container_is_excluded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_linked_volume(temp_dir)
            volume = VolumeManager.Load(temp_dir)
            pyramid_node = _get_pyramid_node(volume)
            self.assertIsNotNone(pyramid_node)
            self.assertTrue(pyramid_node.SaveAsLinkedElement)
            self.assertFalse(migration._ResourceUsesFilesystemValidation(pyramid_node))


class TestSyncValidationTimes(unittest.TestCase):
    """Verify the end-to-end behavior of the SyncValidationTimes stage."""

    def test_stale_level_is_resynced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_linked_volume(temp_dir)
            volume = VolumeManager.Load(temp_dir)

            level = _get_level_node(volume)
            self.assertTrue(level.ChangesSinceLastValidation,
                            "Level should look modified before sync (stale ValidationTime)")

            result = migration.SyncValidationTimes(VolumeElement=volume)

            self.assertIs(result, volume, "Should return the volume node to save when updates occurred")
            self.assertFalse(level.ChangesSinceLastValidation,
                             "Level should not look modified after sync")
            self.assertNotEqual(level.attrib.get("ValidationTime"), STALE_VALIDATION_TIME)

    def test_dryrun_does_not_modify_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_linked_volume(temp_dir)
            volume = VolumeManager.Load(temp_dir)
            level = _get_level_node(volume)

            result = migration.SyncValidationTimes(VolumeElement=volume, dryrun=True)

            self.assertIsNone(result, "Dry run should not return a node to save")
            self.assertEqual(level.attrib.get("ValidationTime"), STALE_VALIDATION_TIME,
                             "Dry run must not alter ValidationTime")

    def test_updated_validation_times_persist_to_disk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_linked_volume(temp_dir)
            volume = VolumeManager.Load(temp_dir)

            result = migration.SyncValidationTimes(VolumeElement=volume)
            self.assertIs(result, volume)
            VolumeManager.Save(result)

            reloaded = VolumeManager.Load(temp_dir)
            reloaded.LoadAllLinkedNodes()
            level = _get_level_node(reloaded)
            self.assertIsNotNone(level)
            self.assertNotEqual(level.attrib.get("ValidationTime"), STALE_VALIDATION_TIME,
                                "ValidationTime must be persisted to disk after save")
            self.assertFalse(level.ChangesSinceLastValidation,
                             "Reloaded level should not look modified after a persisted sync")

    def test_second_run_is_noop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_linked_volume(temp_dir)
            volume = VolumeManager.Load(temp_dir)

            first = migration.SyncValidationTimes(VolumeElement=volume)
            self.assertIs(first, volume)

            second = migration.SyncValidationTimes(VolumeElement=volume)
            self.assertIsNone(second, "A volume already in sync should report no updates")

    def test_missing_volume_node_returns_none(self):
        self.assertIsNone(migration.SyncValidationTimes())


if __name__ == "__main__":
    unittest.main()
