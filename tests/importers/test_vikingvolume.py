"""Offline fixture tests for AdoptVikingVolume."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree

from PIL import Image

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.importers import vikingvolume
from nornir_buildmanager.volumemanager import VolumeManager
from nornir_imageregistration.files.mosaicfile import MosaicFile
from nornir_imageregistration.files.stosfile import StosFile
from nornir_imageregistration.transforms.factory import CreateRigidTransform


def _md5(path: str) -> str:
    digest = hashlib.md5()
    with open(path, 'rb') as handle:
        digest.update(handle.read())
    return digest.hexdigest()


def _write_png(path: str, size: tuple[int, int] = (8, 8), color: int = 128) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new('L', size, color).save(path)


def _write_mosaic(path: str, tiles: list[str]) -> None:
    entries = {name: (index * 10, 0) for index, name in enumerate(tiles)}
    MosaicFile.Write(path, entries, ImageSize=(8, 8))


def _write_stos(path: str, control_png: str, mapped_png: str,
                control_mask: str, mapped_mask: str) -> None:
    transform = CreateRigidTransform((0, 0), 0.0, (8, 8), (8, 8))
    stos = StosFile.Create(control_png, mapped_png, transform, control_mask, mapped_mask)
    stos.Save(path)


def _write_tileset_level(level_dir: str, downsample: int, prefix: str) -> None:
    os.makedirs(level_dir, exist_ok=True)
    xml_path = os.path.join(level_dir, 'tileset.xml')
    ElementTree.ElementTree(ElementTree.Element('Level', {
        'GridDimX': '1',
        'GridDimY': '1',
        'TileXDim': '8',
        'TileYDim': '8',
        'Downsample': str(downsample),
        'FilePrefix': prefix,
        'FilePostfix': '.png',
    })).write(xml_path)
    tile_name = f'{prefix}X000_Y000.png' if prefix else 'X000_Y000.png'
    _write_png(os.path.join(level_dir, tile_name), color=40)


def _write_vikingxml(path: str, num_sections: int) -> None:
    root = ElementTree.Element('Volume', {
        'Name': 'RC1',
        'num_sections': str(num_sections),
        'DefaultSection': '1',
    })
    ElementTree.SubElement(root, 'Scale', {'UnitsOfMeasure': 'nm', 'UnitsPerPixel': '2.176'})
    ElementTree.ElementTree(root).write(path)


def build_fixture(root: str, *, missing_tile: bool = False) -> dict[str, str]:
    """Create a two-section Viking-layout tree. Returns paths used by assertions."""
    os.makedirs(root, exist_ok=True)
    _write_vikingxml(os.path.join(root, 'volume.vikingxml'), 2)
    with open(os.path.join(root, 'About.xml'), 'w', encoding='utf-8') as handle:
        handle.write('<Volume Name="RC1"/>\n')
    with open(os.path.join(root, 'FlipList.txt'), 'w', encoding='utf-8') as handle:
        handle.write('0001\n')
    with open(os.path.join(root, 'StosMap.txt'), 'w', encoding='utf-8') as handle:
        handle.write('Mapped\tControl\tArgs\tNote\n2\t1\t\t\n')
    with open(os.path.join(root, 'Stos.zip'), 'wb') as handle:
        handle.write(b'stos-zip')
    with open(os.path.join(root, 'RC1.zip'), 'wb') as handle:
        handle.write(b'rc1-zip')
    with open(os.path.join(root, 'leftover.zip'), 'wb') as handle:
        handle.write(b'leave-me')

    hashes: dict[str, str] = {}
    for number in (1, 2):
        section = os.path.join(root, f'{number:04d}')
        os.makedirs(section, exist_ok=True)
        tiles = [f'{number:04d}.000.png']
        if not missing_tile or number != 1:
            tiles.append(f'{number:04d}.001.png')
        level_one = os.path.join(section, '8-bit', '001')
        os.makedirs(level_one, exist_ok=True)
        for index, name in enumerate(tiles):
            png_path = os.path.join(level_one, name)
            _write_png(png_path, color=10 + index)
            hashes[name] = _md5(png_path)
        if missing_tile and number == 1:
            tiles.append('0001.002.png')
        _write_mosaic(os.path.join(section, f'{number:04d}_Supertile.mosaic'), tiles)
        _write_mosaic(os.path.join(section, 'translate.mosaic'), tiles)
        _write_mosaic(os.path.join(section, 'grid.mosaic'), tiles)
        _write_tileset_level(os.path.join(section, 'TEM', '001'), 1, f'{number:04d}_')
        hashes[f'{number:04d}_X000_Y000.png'] = _md5(
            os.path.join(section, 'TEM', '001', f'{number:04d}_X000_Y000.png'))
        if number == 1:
            agb_tile = os.path.join(section, 'AGB', '001', f'{number:04d}_AGB_X000_Y000.png')
            _write_tileset_level(os.path.join(section, 'AGB', '001'), 1, f'{number:04d}_AGB_')
            hashes['0001_AGB_X000_Y000.png'] = _md5(agb_tile)
            agb_png = os.path.join(section, f'{number:04d}_AGB_32.png')
            _write_png(agb_png, color=90)
            hashes['0001_AGB_32.png'] = _md5(agb_png)
        with open(os.path.join(section, 'histogram.txt'), 'w', encoding='utf-8') as handle:
            handle.write(f'section {number} histogram\n')

        for kind, ds, color in (('mosaic', 8, 20), ('blob', 16, 30), ('mask', 16, 1)):
            png_path = os.path.join(root, f'{number:04d}_{kind}_{ds}.png')
            _write_png(png_path, color=color)
            hashes[os.path.basename(png_path)] = _md5(png_path)

    thumb = os.path.join(root, '0001_thumbnail_8.png')
    _write_png(thumb, size=(16, 16), color=5)
    hashes['0001_thumbnail_8.png'] = _md5(thumb)

    _write_stos(
        os.path.join(root, '0002-0001_grid_16.stos'),
        os.path.join(root, '0001_blob_16.png'),
        os.path.join(root, '0002_blob_16.png'),
        os.path.join(root, '0001_mask_16.png'),
        os.path.join(root, '0002_mask_16.png'),
    )
    hashes['_fixture'] = root
    return hashes


def _run_import(dest: str, source: str, sections=None, dry_run: bool = False):
    volume = VolumeManager.Load(dest, Create=True)
    list(vikingvolume.Import(volume, source, Sections=sections, DryRun=dry_run))
    return VolumeManager.Load(dest, Create=False)


class VikingVolumeAdoptTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp = tempfile.mkdtemp(prefix='vikingvolume_')
        self.source = os.path.join(self.temp, 'RC1_Original')
        self.dest = os.path.join(self.temp, 'RC1')
        self.hashes = build_fixture(self.source)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_dry_run_does_not_move_files(self) -> None:
        os.makedirs(self.dest, exist_ok=True)
        volume = VolumeManager.Load(self.dest, Create=True)
        list(vikingvolume.Import(volume, self.source, DryRun=True))
        self.assertTrue(os.path.isfile(os.path.join(self.source, '0001', 'grid.mosaic')))
        self.assertTrue(os.path.isfile(os.path.join(self.source, '0002-0001_grid_16.stos')))
        self.assertTrue(os.path.isfile(os.path.join(self.source, 'Stos.zip')))
        self.assertFalse(os.path.isdir(os.path.join(self.dest, 'TEM', '0001')))

    def test_refuses_rabbit_and_unrenamed_rc1(self) -> None:
        rabbit = os.path.join(self.temp, 'Rabbit')
        os.makedirs(rabbit, exist_ok=True)
        volume = VolumeManager.Load(self.dest, Create=True)
        with self.assertRaises(NornirUserException):
            list(vikingvolume.Import(volume, rabbit))

        unrenamed = os.path.join(self.temp, 'RC1')
        os.makedirs(unrenamed, exist_ok=True)
        other_dest = os.path.join(self.temp, 'RC1_new')
        other_volume = VolumeManager.Load(other_dest, Create=True)
        with self.assertRaises(NornirUserException):
            list(vikingvolume.Import(other_volume, unrenamed))

    def test_adopt_moves_locks_and_leaves_leftovers(self) -> None:
        volume = _run_import(self.dest, self.source)
        block = volume.GetBlock('TEM')
        self.assertIsNotNone(block)

        section = block.GetSection(1)
        channel = section.GetChannel('TEM')
        self.assertTrue(os.path.isfile(os.path.join(channel.FullPath, 'Stage.mosaic')))
        self.assertTrue(os.path.isfile(os.path.join(channel.FullPath, 'Translated_Stage_Max0.5.mosaic')))
        self.assertTrue(os.path.isfile(os.path.join(channel.FullPath, 'Grid.mosaic')))

        for name in ('Stage', 'Translated_Stage', 'Grid'):
            transform = channel.GetTransform(name)
            self.assertIsNotNone(transform, name)
            self.assertTrue(transform.Locked, name)

        for filter_name in ('Raw8', 'Leveled', 'Blob', 'Mask'):
            filter_node = channel.GetFilter(filter_name)
            self.assertIsNotNone(filter_node, filter_name)
            self.assertTrue(filter_node.Locked, filter_name)
        self.assertIsNone(channel.GetFilter('VikingTEM'))

        raw8 = channel.GetFilter('Raw8')
        self.assertTrue(raw8.HasTilePyramid)
        self.assertTrue(os.path.isfile(os.path.join(raw8.TilePyramid.FullPath, '001', '0001.000.png')))
        self.assertEqual(_md5(os.path.join(raw8.TilePyramid.FullPath, '001', '0001.000.png')),
                         self.hashes['0001.000.png'])

        leveled = channel.GetFilter('Leveled')
        mosaic_png = os.path.join(leveled.Imageset.GetImage(8).FullPath)
        self.assertTrue(os.path.isfile(mosaic_png))
        self.assertEqual(_md5(mosaic_png), self.hashes['0001_mosaic_8.png'])
        self.assertTrue(leveled.Imageset.HasImage(8))
        self.assertTrue(leveled.HasTileset)
        self.assertTrue(os.path.isfile(os.path.join(leveled.Tileset.FullPath, '001', '0001_X000_Y000.png')))
        self.assertEqual(_md5(os.path.join(leveled.Tileset.FullPath, '001', '0001_X000_Y000.png')),
                         self.hashes['0001_X000_Y000.png'])

        blob = channel.GetFilter('Blob')
        self.assertEqual(blob.MaskName, 'Mask')
        self.assertEqual(_md5(blob.Imageset.GetImage(16).FullPath), self.hashes['0001_blob_16.png'])

        agb = section.GetChannel('AGB')
        self.assertIsNotNone(agb)
        viking = agb.GetFilter('VikingTEM')
        self.assertIsNotNone(viking)
        self.assertTrue(viking.Locked)
        self.assertTrue(viking.HasTileset)
        self.assertTrue(os.path.isfile(os.path.join(viking.Tileset.FullPath, '001', '0001_AGB_X000_Y000.png')))
        self.assertEqual(_md5(os.path.join(viking.Tileset.FullPath, '001', '0001_AGB_X000_Y000.png')),
                         self.hashes['0001_AGB_X000_Y000.png'])
        agb_leveled = agb.GetFilter('Leveled')
        self.assertIsNotNone(agb_leveled)
        self.assertTrue(agb_leveled.Locked)
        self.assertFalse(agb_leveled.HasTileset)
        self.assertEqual(_md5(agb_leveled.Imageset.GetImage(32).FullPath), self.hashes['0001_AGB_32.png'])
        self.assertFalse(os.stat(mosaic_png).st_mode & stat.S_IWRITE)
        self.assertFalse(os.stat(agb_leveled.Imageset.GetImage(32).FullPath).st_mode & stat.S_IWRITE)

        stos_group = block.GetStosGroup('Grid16', 16)
        self.assertIsNotNone(stos_group)
        mapping = stos_group.GetSectionMapping(2)
        self.assertIsNotNone(mapping)
        transforms = list(mapping.Transforms)
        self.assertEqual(len(transforms), 1)
        stos_node = transforms[0]
        self.assertTrue(stos_node.Locked)
        self.assertTrue(stos_node.Path.endswith('_ctrl-TEM_Raw8_map-TEM_Raw8.stos'))
        self.assertTrue(os.path.isfile(stos_node.FullPath))

        loaded = StosFile.Load(stos_node.FullPath, resolve_paths=True)
        self.assertTrue(os.path.isfile(loaded.ControlImageFullPath))
        self.assertTrue(os.path.isfile(loaded.MappedImageFullPath))
        self.assertIn('Blob', loaded.ControlImageFullPath)
        self.assertIn('Mask', loaded.ControlMaskFullPath)

        self.assertTrue(os.path.isfile(os.path.join(self.dest, 'Stos.zip')))
        self.assertTrue(os.path.isfile(os.path.join(self.dest, 'RC1.zip')))
        self.assertTrue(os.path.isfile(os.path.join(self.source, 'leftover.zip')))
        self.assertFalse(os.path.isfile(os.path.join(self.source, 'Stos.zip')))
        self.assertFalse(os.path.isfile(os.path.join(self.source, '0001', 'grid.mosaic')))
        self.assertTrue(os.path.isfile(os.path.join(self.source, 'volume.vikingxml')))
        self.assertTrue(os.path.isfile(os.path.join(self.dest, 'volume.vikingxml')))

        stos_map = block.GetStosMap('StosMap')
        self.assertIsNotNone(stos_map)
        self.assertEqual(stos_map.CenterSection, 1)

    def test_missing_tiles_are_reported_not_invented(self) -> None:
        shutil.rmtree(self.source)
        self.hashes = build_fixture(self.source, missing_tile=True)
        mosaic = os.path.join(self.source, '0001', 'grid.mosaic')
        tiles = os.path.join(self.source, '0001', '8-bit', '001')
        missing = vikingvolume.missing_mosaic_tiles(mosaic, tiles)
        self.assertEqual(missing, ['0001.002.png'])
        self.assertIn('0001.000.png', os.listdir(tiles))
        names = vikingvolume.mosaic_image_names(mosaic)
        self.assertIn('0001.002.png', names)

        _run_import(self.dest, self.source)
        dest_mosaic = os.path.join(self.dest, 'TEM', '0001', 'TEM', 'Grid.mosaic')
        dest_tiles = os.path.join(self.dest, 'TEM', '0001', 'TEM', 'Raw8', 'TilePyramid', '001')
        still_missing = vikingvolume.missing_mosaic_tiles(dest_mosaic, dest_tiles)
        self.assertEqual(still_missing, ['0001.002.png'])
        dest_names = vikingvolume.mosaic_image_names(dest_mosaic)
        self.assertIn('0001.002.png', dest_names)

    def test_resume_rebuilds_xml_from_moved_files(self) -> None:
        _run_import(self.dest, self.source)
        section_xml = os.path.join(self.dest, 'TEM', '0001', 'VolumeData.xml')
        channel_xml = os.path.join(self.dest, 'TEM', '0001', 'TEM', 'VolumeData.xml')
        self.assertTrue(os.path.isfile(section_xml))
        os.remove(section_xml)
        if os.path.isfile(channel_xml):
            os.remove(channel_xml)
        volume = _run_import(self.dest, self.source)
        section = volume.GetBlock('TEM').GetSection(1)
        channel = section.GetChannel('TEM')
        self.assertTrue(os.path.isfile(os.path.join(section.FullPath, 'VolumeData.xml')))
        for name in ('Stage', 'Translated_Stage', 'Grid'):
            self.assertTrue(channel.GetTransform(name).Locked, name)
        for filter_name in ('Raw8', 'Leveled', 'Blob', 'Mask'):
            self.assertTrue(channel.GetFilter(filter_name).Locked, filter_name)
        self.assertIsNone(channel.GetFilter('VikingTEM'))
        self.assertTrue(channel.GetFilter('Leveled').HasTileset)

    def test_relocate_vikingtem_tileset_onto_leveled(self) -> None:
        _run_import(self.dest, self.source)
        channel_path = os.path.join(self.dest, 'TEM', '0001', 'TEM')
        leveled_tileset = os.path.join(channel_path, 'Leveled', 'Tileset')
        viking_tileset = os.path.join(channel_path, 'VikingTEM', 'Tileset')
        tile = os.path.join(leveled_tileset, '001', '0001_X000_Y000.png')
        digest = _md5(tile)
        os.makedirs(os.path.join(channel_path, 'VikingTEM'), exist_ok=True)
        shutil.move(leveled_tileset, viking_tileset)
        self.assertTrue(os.path.isfile(os.path.join(viking_tileset, '001', '0001_X000_Y000.png')))
        self.assertFalse(os.path.isdir(leveled_tileset))

        volume = _run_import(self.dest, self.source)
        channel = volume.GetBlock('TEM').GetSection(1).GetChannel('TEM')
        self.assertIsNone(channel.GetFilter('VikingTEM'))
        leveled = channel.GetFilter('Leveled')
        self.assertTrue(leveled.HasTileset)
        self.assertTrue(leveled.Locked)
        dest_tile = os.path.join(leveled.Tileset.FullPath, '001', '0001_X000_Y000.png')
        self.assertTrue(os.path.isfile(dest_tile))
        self.assertEqual(_md5(dest_tile), digest)
        self.assertFalse(os.path.isdir(os.path.join(channel_path, 'VikingTEM')))

    def test_relocate_immuno_tileset_off_leveled_onto_channel(self) -> None:
        _run_import(self.dest, self.source)
        agb_path = os.path.join(self.dest, 'TEM', '0001', 'AGB')
        viking_tileset = os.path.join(agb_path, 'VikingTEM', 'Tileset')
        leveled_tileset = os.path.join(agb_path, 'Leveled', 'Tileset')
        tile = os.path.join(viking_tileset, '001', '0001_AGB_X000_Y000.png')
        digest = _md5(tile)
        os.makedirs(os.path.join(agb_path, 'Leveled'), exist_ok=True)
        shutil.move(viking_tileset, leveled_tileset)

        volume = VolumeManager.Load(self.dest, Create=False)
        agb = volume.GetBlock('TEM').GetSection(1).GetChannel('AGB')
        leveled = agb.GetFilter('Leveled')
        vikingvolume._attach_tileset(leveled, None, False)
        viking = agb.GetFilter('VikingTEM')
        if viking is not None:
            agb.remove(viking)
        leftover_viking = os.path.join(agb_path, 'VikingTEM')
        if os.path.isdir(leftover_viking):
            shutil.rmtree(leftover_viking)
        agb.Save()

        self.assertTrue(os.path.isfile(os.path.join(leveled_tileset, '001', '0001_AGB_X000_Y000.png')))
        self.assertFalse(os.path.isdir(viking_tileset))

        volume = _run_import(self.dest, self.source)
        agb = volume.GetBlock('TEM').GetSection(1).GetChannel('AGB')
        viking = agb.GetFilter('VikingTEM')
        self.assertIsNotNone(viking)
        self.assertTrue(viking.HasTileset)
        dest_tile = os.path.join(viking.Tileset.FullPath, '001', '0001_AGB_X000_Y000.png')
        self.assertTrue(os.path.isfile(dest_tile))
        self.assertEqual(_md5(dest_tile), digest)
        leveled = agb.GetFilter('Leveled')
        self.assertFalse(leveled.HasTileset)
        self.assertIsNone(leveled.find('Tileset_Link'))
        self.assertFalse(os.path.isdir(leveled_tileset))

    def test_rerun_restores_missing_imageset_png_and_marks_readonly(self) -> None:
        _run_import(self.dest, self.source)
        dest_png = os.path.join(
            self.dest, 'TEM', '0001', 'TEM', 'Leveled', 'Images', '008', '0001_TEM_Leveled.png')
        with open(dest_png, 'rb') as handle:
            payload = handle.read()
        os.chmod(dest_png, stat.S_IWRITE | stat.S_IREAD)
        os.remove(dest_png)
        replacement = os.path.join(self.source, '0001_mosaic_8.png')
        with open(replacement, 'wb') as handle:
            handle.write(payload)

        _run_import(self.dest, self.source)
        self.assertTrue(os.path.isfile(dest_png))
        self.assertEqual(_md5(dest_png), hashlib.md5(payload).hexdigest())
        self.assertFalse(os.stat(dest_png).st_mode & stat.S_IWRITE)
        self.assertFalse(os.path.isfile(replacement))

    def test_incomplete_source_is_an_error(self) -> None:
        _write_vikingxml(os.path.join(self.source, 'volume.vikingxml'), 9)
        volume = VolumeManager.Load(self.dest, Create=True)
        with self.assertRaises(NornirUserException):
            list(vikingvolume.Import(volume, self.source))


if __name__ == '__main__':
    unittest.main()
