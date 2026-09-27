"""Adopt a Viking-layout volume clone into a modern Nornir VolumeData.xml tree.

Moves and renames files from the source clone. Image bytes are not rewritten.
``.mosaic`` / ``.stos`` text may be edited. Every adopted mosaic, stos, and
filter is created with ``Locked="1"``. Pipeline lock-honor behavior is not
changed.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from typing import Generator, Iterable, cast

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.progress import report_iterate, report_iterate_complete
from nornir_buildmanager.templates import Current as templates
from nornir_buildmanager.volumemanager import (
    BlockNode,
    ChannelNode,
    FilterNode,
    ImageNode,
    ImageSetNode,
    LevelNode,
    NotesNode,
    SectionNode,
    StosGroupNode,
    TilePyramidNode,
    TilesetNode,
    TransformNode,
    VolumeNode,
    XContainerElementWrapper,
    XElementWrapper,
)
from nornir_buildmanager.volumemanager.filternode import BuildFilterImageName
from nornir_imageregistration.files.mosaicfile import MosaicFile
from nornir_imageregistration.files.stosfile import StosFile
from nornir_shared import prettyoutput
from nornir_shared.checksum import FilesizeChecksum

logger = logging.getLogger(__name__)

BLOCK_NAME = 'TEM'
TEM_CHANNEL = 'TEM'
DEFAULT_SCALE_NM = 2.176
VikingTemFilter = 'VikingTEM'
LEVELED_FILTER = 'Leveled'
LOCKED = True

_RESERVED_SECTION_DIRS = frozenset({'8-bit', '16-bit', 'TEM'})
_SIDECAR_FILES = ('About.xml', 'StosMap.txt', 'FlipList.txt', 'volume.vikingxml')
_ROOT_ZIPS = ('Stos.zip', 'RC1.zip')

_ROOT_PNG = re.compile(
    r'^(\d+)_((?:8-bit_)?(?:mosaic|blob|mask)|thumbnail)_(\d+)\.png$',
    re.IGNORECASE)
_IMMUNO_PNG = re.compile(r'^(\d+)_([A-Za-z]+)_(8|16|32|64|128)\.png$', re.IGNORECASE)
_STOS_NAME = re.compile(r'^(\d+)-(\d+)_(grid|brute)_(\d+)\.stos$', re.IGNORECASE)
_LEVEL_DIR = re.compile(r'^\d+$')

_PNG_TYPE_TO_FILTER = {
    'mosaic': 'Leveled',
    'blob': 'Blob',
    'mask': 'Mask',
    'thumbnail': 'Leveled',
    '8-bit_mosaic': 'Raw8',
}

_MOSAICS = (
    ('supertile', 'Stage', 'Stage', 'Stage.mosaic'),
    ('translate', 'Translated_Stage', '_Max0.5', 'Translated_Stage_Max0.5.mosaic'),
    ('grid', 'Grid', 'Grid', 'Grid.mosaic'),
)

_STOS_FAMILIES: dict[tuple[str, int], tuple[str, int, str]] = {
    ('grid', 8): ('Grid8', 8, 'Grid'),
    ('grid', 16): ('Grid16', 16, 'Grid'),
    ('grid', 32): ('Grid32', 32, 'Grid'),
    ('brute', 32): ('Brute32', 32, 'Brute'),
}


@dataclass
class VolumeMeta:
    """Scale and section count from volume.vikingxml."""

    scale_nm: float = DEFAULT_SCALE_NM
    num_sections: int | None = None
    default_section: int = 1


@dataclass
class StosRecord:
    """One root ``.stos`` file to adopt."""

    mapped: int
    control: int
    family: str
    downsample: int
    group_name: str
    group_downsample: int
    transform_type: str
    source_path: str


@dataclass
class RootPng:
    """An assemble PNG at the volume or section root."""

    section: int
    png_type: str
    downsample: int
    source_path: str
    filter_name: str


@dataclass
class SectionInventory:
    """Files belonging to one numeric section folder."""

    number: int
    path: str
    mosaics: dict[str, str] = field(default_factory=dict)
    capture_pyramids: dict[str, str] = field(default_factory=dict)
    viking_tileset: str | None = None
    immuno_channels: dict[str, str] = field(default_factory=dict)
    local_pngs: list[RootPng] = field(default_factory=list)
    histogram_path: str | None = None
    local_thumbnail: str | None = None


@dataclass
class VikingInventory:
    """Inventory of a Viking-layout source tree."""

    source_root: str
    meta: VolumeMeta
    sections: dict[int, SectionInventory] = field(default_factory=dict)
    stos: list[StosRecord] = field(default_factory=list)
    root_pngs: list[RootPng] = field(default_factory=list)
    flip_list: list[int] = field(default_factory=list)
    stos_map_path: str | None = None
    missing_required: list[str] = field(default_factory=list)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _section_template(number: int) -> str:
    return templates.SectionTemplate % number


def parse_volume_meta(source_root: str) -> VolumeMeta:
    """Read scale and section count from ``volume.vikingxml``."""
    viking_path = os.path.join(source_root, 'volume.vikingxml')
    if not os.path.isfile(viking_path):
        raise NornirUserException(f"Missing volume.vikingxml in {source_root}")

    root = ElementTree.parse(viking_path).getroot()
    meta = VolumeMeta()
    num_sections = root.attrib.get('num_sections')
    if num_sections is not None:
        meta.num_sections = int(num_sections)
    default_section = root.attrib.get('DefaultSection')
    if default_section is not None:
        meta.default_section = int(default_section)

    scale_node = root.find('Scale')
    if scale_node is not None:
        units = scale_node.attrib.get('UnitsPerPixel')
        if units is not None:
            meta.scale_nm = float(units)
    return meta


def parse_stos_basename(filename: str) -> tuple[int, int, str, int] | None:
    """Return (mapped, control, family, downsample) from a Viking stos name."""
    match = _STOS_NAME.match(os.path.basename(filename))
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), match.group(3).lower(), int(match.group(4))


def mosaic_image_names(mosaic_path: str) -> list[str]:
    """Basenames referenced by a mosaic, or empty if the file cannot be loaded."""
    mosaic = MosaicFile.Load(mosaic_path)
    if mosaic is None or mosaic.ImageToTransformString is None:
        return []
    return list(mosaic.ImageToTransformString.keys())


def png_names(directory: str) -> set[str]:
    """PNG basenames in a directory, ignoring dotfiles."""
    names: set[str] = set()
    if not os.path.isdir(directory):
        return names
    with os.scandir(directory) as entries:
        for entry in entries:
            if not entry.is_file():
                continue
            if entry.name.startswith('.'):
                continue
            if entry.name.lower().endswith('.png'):
                names.add(entry.name)
    return names


def missing_mosaic_tiles(mosaic_path: str, tile_dir: str) -> list[str]:
    """Mosaic image names that are not present in ``tile_dir``."""
    present = png_names(tile_dir)
    missing = [name for name in mosaic_image_names(mosaic_path) if name not in present]
    missing.sort()
    return missing


def _numeric_section_dirs(source_root: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    with os.scandir(source_root) as entries:
        for entry in entries:
            if not entry.is_dir():
                continue
            if not re.fullmatch(r'\d+', entry.name):
                continue
            found.append((int(entry.name), entry.path))
    found.sort(key=lambda item: item[0])
    return found


def _find_supertile(section_path: str, section_number: int) -> str | None:
    expected = os.path.join(section_path, f'{_section_template(section_number)}_Supertile.mosaic')
    if os.path.isfile(expected):
        return expected
    with os.scandir(section_path) as entries:
        for entry in entries:
            if entry.is_file() and entry.name.lower().endswith('_supertile.mosaic'):
                return entry.path
    return None


def _parse_root_png(path: str) -> RootPng | None:
    match = _ROOT_PNG.match(os.path.basename(path))
    if match is None:
        return None
    png_type = match.group(2).lower()
    filter_name = _PNG_TYPE_TO_FILTER.get(png_type)
    if filter_name is None:
        return None
    return RootPng(
        section=int(match.group(1)),
        png_type=png_type,
        downsample=int(match.group(3)),
        source_path=path,
        filter_name=filter_name,
    )


def _parse_immuno_png(path: str) -> tuple[str, RootPng] | None:
    match = _IMMUNO_PNG.match(os.path.basename(path))
    if match is None:
        return None
    channel = match.group(2)
    if channel.lower() in {'mosaic', 'blob', 'mask', 'thumbnail'}:
        return None
    png = RootPng(
        section=int(match.group(1)),
        png_type='immuno',
        downsample=int(match.group(3)),
        source_path=path,
        filter_name='Leveled',
    )
    return channel, png


def inventory_source(source_root: str) -> VikingInventory:
    """Walk a Viking-layout tree and record mosaics, tiles, stos, and assemble PNGs."""
    source_root = os.path.abspath(source_root)
    inventory = VikingInventory(source_root=source_root, meta=parse_volume_meta(source_root))

    flip_path = os.path.join(source_root, 'FlipList.txt')
    if os.path.isfile(flip_path):
        with open(flip_path, encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                inventory.flip_list.append(int(line))

    stos_map_path = os.path.join(source_root, 'StosMap.txt')
    if os.path.isfile(stos_map_path):
        inventory.stos_map_path = stos_map_path

    for number, section_path in _numeric_section_dirs(source_root):
        section = SectionInventory(number=number, path=section_path)
        grid = os.path.join(section_path, 'grid.mosaic')
        translate = os.path.join(section_path, 'translate.mosaic')
        if os.path.isfile(grid):
            section.mosaics['grid'] = grid
        if os.path.isfile(translate):
            section.mosaics['translate'] = translate
        supertile = _find_supertile(section_path, number)
        if supertile is not None:
            section.mosaics['supertile'] = supertile

        for folder, filter_name in (('8-bit', 'Raw8'), ('16-bit', '16-bit')):
            capture = os.path.join(section_path, folder)
            if os.path.isdir(capture):
                section.capture_pyramids[filter_name] = capture

        viking_tem = os.path.join(section_path, 'TEM')
        if os.path.isdir(viking_tem):
            section.viking_tileset = viking_tem

        histogram = os.path.join(section_path, 'histogram.txt')
        if os.path.isfile(histogram):
            section.histogram_path = histogram

        with os.scandir(section_path) as entries:
            for entry in entries:
                if entry.is_dir() and entry.name not in _RESERVED_SECTION_DIRS:
                    section.immuno_channels[entry.name] = entry.path
                elif entry.is_file() and entry.name.lower().startswith('thumbnail_') and entry.name.lower().endswith('.png'):
                    section.local_thumbnail = entry.path
                elif entry.is_file() and entry.name.lower().endswith('.png'):
                    parsed = _parse_root_png(entry.path)
                    if parsed is not None:
                        section.local_pngs.append(parsed)
                    else:
                        immuno = _parse_immuno_png(entry.path)
                        if immuno is not None:
                            section.local_pngs.append(immuno[1])

        inventory.sections[number] = section

    with os.scandir(source_root) as entries:
        for entry in entries:
            if not entry.is_file():
                continue
            if entry.name.lower().endswith('.stos'):
                parsed = parse_stos_basename(entry.name)
                if parsed is None:
                    logger.warning("Skipping stos with unrecognized name: %s", entry.name)
                    continue
                mapped, control, family, downsample = parsed
                family_key = (family, downsample)
                if family_key not in _STOS_FAMILIES:
                    inventory.missing_required.append(
                        f"Unsupported stos family {entry.name}")
                    continue
                group_name, group_ds, transform_type = _STOS_FAMILIES[family_key]
                inventory.stos.append(StosRecord(
                    mapped=mapped,
                    control=control,
                    family=family,
                    downsample=downsample,
                    group_name=group_name,
                    group_downsample=group_ds,
                    transform_type=transform_type,
                    source_path=entry.path,
                ))
                continue
            parsed_png = _parse_root_png(entry.path)
            if parsed_png is not None:
                inventory.root_pngs.append(parsed_png)

    return inventory


def _refuse_if_unsafe(dest_root: str, source_root: str) -> None:
    dest = _norm(dest_root)
    source = _norm(source_root)
    if dest == source:
        raise NornirUserException("Destination and source are the same path")

    source_name = os.path.basename(os.path.normpath(source_root)).lower()
    dest_name = os.path.basename(os.path.normpath(dest_root)).lower()
    if source_name == 'rabbit' or dest_name == 'rabbit':
        raise NornirUserException("Refusing to read or write the live Rabbit volume")
    if source_name == 'rc1':
        raise NornirUserException(
            "Source is still named RC1; rename the clone to RC1_Original first")


def _dest_has_adopted_sections(dest_root: str) -> bool:
    dest_tem = os.path.join(dest_root, BLOCK_NAME)
    if not os.path.isdir(dest_tem):
        return False
    with os.scandir(dest_tem) as entries:
        return any(entry.is_dir() and entry.name.isdigit() for entry in entries)


def _unresolved_missing(inventory: VikingInventory, dest_root: str) -> list[str]:
    """Missing mosaics that are still absent from both source and dest."""
    still = list(inventory.missing_required)
    for number, section in inventory.sections.items():
        dest_channel = os.path.join(dest_root, BLOCK_NAME, _section_template(number), TEM_CHANNEL)
        has_source_tiles = 'Raw8' in section.capture_pyramids or section.viking_tileset is not None
        dest_has_tiles = (
            os.path.isdir(os.path.join(dest_channel, 'Raw8'))
            or os.path.isdir(os.path.join(dest_channel, LEVELED_FILTER, TilesetNode.DefaultPath))
            or os.path.isdir(os.path.join(dest_channel, VikingTemFilter)))
        if not has_source_tiles and not dest_has_tiles:
            continue
        for key, _name, _type, dest_name in _MOSAICS:
            if key in section.mosaics:
                continue
            if os.path.isfile(os.path.join(dest_channel, dest_name)):
                continue
            still.append(f"Section {number:04d} is missing {key} mosaic")
    return still


def _refuse_if_incomplete(inventory: VikingInventory, dest_root: str) -> None:
    expected = inventory.meta.num_sections
    actual = len(inventory.sections)
    if expected is not None and actual < expected and not _dest_has_adopted_sections(dest_root):
        raise NornirUserException(
            f"Source has {actual} section folders, volume.vikingxml expects {expected}. "
            "The clone may still be copying.")
    still_missing = _unresolved_missing(inventory, dest_root)
    if still_missing:
        raise NornirUserException(
            "Missing inventoried files:\n" + "\n".join(still_missing))


_needs_dir_settle = False


def _save_xml(node: XContainerElementWrapper, *, recurse: bool) -> None:
    """Persist VolumeData.xml, retrying SMB file-in-use after large directory moves."""
    global _needs_dir_settle
    if _needs_dir_settle:
        prettyoutput.Log("Waiting for filesystem to settle after directory moves")
        time.sleep(2.0)
        _needs_dir_settle = False
    last_error: BaseException | None = None
    for attempt in range(8):
        try:
            if recurse:
                node.Save()
            else:
                node._Save(recurse=False)
            return
        except PermissionError as error:
            last_error = error
            prettyoutput.Log(f"Save retry {attempt + 1}/8: {error}")
            time.sleep(min(8.0, 0.5 * (2 ** attempt)))
    if last_error is not None:
        raise last_error


def _move(source: str, dest: str, dry_run: bool) -> None:
    prettyoutput.Log(f"MOVE {source} -> {dest}")
    if dry_run:
        return
    if not os.path.exists(source):
        raise NornirUserException(f"Missing source file: {source}")
    if os.path.exists(dest):
        raise NornirUserException(f"Destination already exists: {dest}")
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    shutil.move(source, dest)
    if os.path.isdir(dest):
        global _needs_dir_settle
        _needs_dir_settle = True
        time.sleep(0.25)


def _copy_if_needed(source: str, dest: str, dry_run: bool) -> None:
    if not os.path.isfile(source):
        return
    prettyoutput.Log(f"COPY {source} -> {dest}")
    if dry_run:
        return
    if os.path.isfile(dest):
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(source, dest)


def _mark_file_readonly(path: str) -> None:
    """Clear the write bit so pipelines cannot delete or overwrite the file."""
    if not os.path.isfile(path):
        return
    mode = os.stat(path).st_mode
    if mode & stat.S_IWRITE:
        os.chmod(path, mode & ~stat.S_IWRITE)


def _mark_imageset_pngs_readonly(filter_node: FilterNode, dry_run: bool) -> None:
    """Make ImageSet assemble PNGs read-only. Tile pyramid / tileset files are left alone."""
    if dry_run:
        return
    imageset_root = os.path.join(filter_node.FullPath, ImageSetNode.DefaultPath)
    if not os.path.isdir(imageset_root):
        return
    for dirpath, _dirnames, filenames in os.walk(imageset_root):
        for name in filenames:
            if name.lower().endswith('.png'):
                _mark_file_readonly(os.path.join(dirpath, name))


def _lock_filter(filter_node: FilterNode) -> None:
    filter_node.Locked = True


def _lock_transform(transform: TransformNode) -> None:
    transform.Locked = True


def _get_or_create_block(volume: VolumeNode) -> BlockNode:
    added, block = volume.UpdateOrAddChildByAttrib(BlockNode.Create(BLOCK_NAME), 'Name')
    return block


def _get_or_create_section(block: BlockNode, number: int) -> SectionNode:
    added, section = block.GetOrCreateSection(number)
    return section


def _get_or_create_channel(section: SectionNode, name: str, scale_nm: float) -> ChannelNode:
    added, channel = section.GetOrCreateChannel(name)
    channel = cast(ChannelNode, channel)
    if channel.Scale is None:
        channel.SetScale(scale_nm)
    return channel


def _get_or_create_filter(channel: ChannelNode, name: str, bits: int | None = None,
                          mask_name: str | None = None) -> FilterNode:
    added, filter_node = channel.GetOrCreateFilter(name)
    if bits is not None and filter_node.BitsPerPixel is None:
        filter_node.BitsPerPixel = bits
    if mask_name is not None:
        filter_node.MaskName = mask_name
    return filter_node


def _add_notes(parent, text: str, source_filename: str | None = None) -> None:
    parent.append(NotesNode.Create(Text=text, SourceFilename=source_filename))


def _level_dirs(root: str) -> list[tuple[int, str]]:
    levels: list[tuple[int, str]] = []
    if not os.path.isdir(root):
        return levels
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_dir() and _LEVEL_DIR.match(entry.name):
                levels.append((int(entry.name), entry.path))
    levels.sort(key=lambda item: item[0])
    return levels


def _parse_tileset_level_xml(level_dir: str) -> dict[str, str]:
    attribs: dict[str, str] = {}
    if not os.path.isdir(level_dir):
        return attribs
    with os.scandir(level_dir) as entries:
        for entry in entries:
            if entry.is_file() and entry.name.lower().endswith('.xml'):
                root = ElementTree.parse(entry.path).getroot()
                attribs.update(root.attrib)
                return attribs
    return attribs


def _attach_tile_pyramid(filter_node: FilterNode, source_dir: str | None, dry_run: bool) -> None:
    added, pyramid = filter_node.GetOrCreateTilePyramid()
    dest = pyramid.FullPath
    if os.path.isdir(dest):
        prettyoutput.Log(f"SKIP pyramid already present: {dest}")
    elif source_dir and os.path.isdir(source_dir):
        _move(source_dir, dest, dry_run)
    else:
        prettyoutput.Log(f"SKIP pyramid, neither source nor dest present for {filter_node.FullPath}")
        return

    if dry_run:
        return

    number_of_tiles = 0
    for downsample, level_dir in _level_dirs(dest):
        pyramid.GetOrCreateLevel(downsample, GenerateData=False)
        if downsample == 1 or number_of_tiles == 0:
            number_of_tiles = len(png_names(level_dir))
    pyramid.NumberOfTiles = number_of_tiles
    pyramid.ImageFormatExt = '.png'


def _attach_tileset(filter_node: FilterNode, source_dir: str | None, dry_run: bool) -> None:
    dest = os.path.join(filter_node.FullPath, TilesetNode.DefaultPath)
    if os.path.isdir(dest):
        prettyoutput.Log(f"SKIP tileset already present: {dest}")
    elif source_dir and os.path.isdir(source_dir):
        _move(source_dir, dest, dry_run)
    else:
        prettyoutput.Log(f"SKIP tileset, neither source nor dest present for {filter_node.FullPath}")
        return

    if dry_run:
        return

    existing_xml = os.path.join(dest, 'VolumeData.xml')
    if os.path.isfile(existing_xml):
        # Keep moved/already-written tileset XML. Rewriting it races SMB after shutil.move.
        if not _filter_has_tileset_child(filter_node):
            filter_node.append(XElementWrapper('Tileset_Link', attrib={'Path': TilesetNode.DefaultPath}))
        return

    tileset = filter_node.Tileset
    if tileset is None:
        tileset = TilesetNode.Create()
        filter_node.UpdateOrAddChildByAttrib(tileset, 'Path')
    tileset.CoordFormat = templates.GridTileCoordFormat
    tileset.FilePostfix = '.png'
    first_xml: dict[str, str] | None = None
    for downsample, level_dir in _level_dirs(dest):
        added, level = tileset.GetOrCreateLevel(downsample, GenerateData=False)
        if level is None:
            continue
        xml_attribs = _parse_tileset_level_xml(level_dir)
        if first_xml is None and xml_attribs:
            first_xml = xml_attribs
        if 'GridDimX' in xml_attribs:
            level.GridDimX = int(xml_attribs['GridDimX'])
        if 'GridDimY' in xml_attribs:
            level.GridDimY = int(xml_attribs['GridDimY'])
        if 'FilePrefix' in xml_attribs:
            tileset.FilePrefix = xml_attribs['FilePrefix']
        if 'FilePostfix' in xml_attribs:
            tileset.FilePostfix = xml_attribs['FilePostfix']
        if 'TileXDim' in xml_attribs:
            tileset.TileXDim = int(xml_attribs['TileXDim'])
        if 'TileYDim' in xml_attribs:
            tileset.TileYDim = int(xml_attribs['TileYDim'])
    if first_xml is None:
        tileset.FilePrefix = tileset.FilePrefix or ''
        tileset.TileXDim = tileset.TileXDim or 256
        tileset.TileYDim = tileset.TileYDim or 256


def _tileset_source_from_path(path: str | None) -> str | None:
    """Return a directory that actually holds tileset level folders, if any."""
    if not path or not os.path.isdir(path):
        return None
    nested_legacy = os.path.join(path, VikingTemFilter, TilesetNode.DefaultPath)
    if os.path.isdir(nested_legacy):
        return nested_legacy
    nested_tileset = os.path.join(path, TilesetNode.DefaultPath)
    if os.path.isdir(nested_tileset):
        return nested_tileset
    if _level_dirs(path):
        return path
    return None


def _remove_empty_vikingtem(channel: ChannelNode, dry_run: bool) -> None:
    """Drop the invented VikingTEM filter after its tileset has moved to Leveled."""
    viking_dir = os.path.join(channel.FullPath, VikingTemFilter)
    leftover_tileset = os.path.join(viking_dir, TilesetNode.DefaultPath)
    if os.path.isdir(leftover_tileset):
        prettyoutput.Log(f"Leaving {leftover_tileset}; relocate did not empty VikingTEM")
        return
    viking = channel.GetFilter(VikingTemFilter)
    if viking is not None:
        prettyoutput.Log(f"REMOVE filter {viking.FullPath}")
        if not dry_run:
            channel.remove(viking)
    if os.path.isdir(viking_dir):
        prettyoutput.Log(f"REMOVE {viking_dir}")
        if not dry_run:
            shutil.rmtree(viking_dir)


def _filter_has_tileset_child(filter_node: FilterNode | None) -> bool:
    """True when the filter has a Tileset node or an unloaded Tileset_Link stub."""
    if filter_node is None:
        return False
    return filter_node.find('Tileset') is not None or filter_node.find('Tileset_Link') is not None


def _remove_leveled_tileset_node(channel: ChannelNode, dry_run: bool) -> bool:
    """Drop a Tileset child from Leveled without touching ImageSet assemble PNGs."""
    leveled = channel.GetFilter(LEVELED_FILTER)
    if leveled is None:
        return False
    removed = False
    for tag in ('Tileset', 'Tileset_Link'):
        child = leveled.find(tag)
        while child is not None:
            prettyoutput.Log(f"REMOVE {tag} from {leveled.FullPath}")
            if not dry_run:
                leveled.remove(child)
            removed = True
            child = None if dry_run else leveled.find(tag)
    return removed


def _relocate_tileset_to_leveled(channel: ChannelNode, source_dir: str | None, dry_run: bool) -> bool:
    """Move a TEM Viking tileset onto Leveled and attach Tileset metadata.

    Returns True when dest XML should be saved.
    """
    leveled_path = os.path.join(channel.FullPath, LEVELED_FILTER, TilesetNode.DefaultPath)
    legacy_path = os.path.join(channel.FullPath, VikingTemFilter, TilesetNode.DefaultPath)
    source = legacy_path if os.path.isdir(legacy_path) else _tileset_source_from_path(source_dir)
    viking_dir = os.path.join(channel.FullPath, VikingTemFilter)
    needs_remove = channel.GetFilter(VikingTemFilter) is not None or os.path.isdir(viking_dir)

    if os.path.isdir(leveled_path):
        leveled = channel.GetFilter(LEVELED_FILTER)
        has_meta = _filter_has_tileset_child(leveled)
        if has_meta and not needs_remove:
            return False
        leveled = _get_or_create_filter(channel, LEVELED_FILTER, bits=8)
        _attach_tileset(leveled, None, dry_run)
        _lock_filter(leveled)
        _remove_empty_vikingtem(channel, dry_run)
        return True

    if source is None:
        if needs_remove:
            _remove_empty_vikingtem(channel, dry_run)
            return True
        return False

    leveled = _get_or_create_filter(channel, LEVELED_FILTER, bits=8)
    _attach_tileset(leveled, source, dry_run)
    _lock_filter(leveled)
    _remove_empty_vikingtem(channel, dry_run)
    return True


def _relocate_immuno_tileset_to_vikingtem(channel: ChannelNode, source_dir: str | None,
                                          dry_run: bool) -> bool:
    """Keep stain tilesets on the immuno channel's VikingTEM filter, not Leveled."""
    viking_path = os.path.join(channel.FullPath, VikingTemFilter, TilesetNode.DefaultPath)
    leveled_tileset = os.path.join(channel.FullPath, LEVELED_FILTER, TilesetNode.DefaultPath)
    viking = channel.GetFilter(VikingTemFilter)
    leveled = channel.GetFilter(LEVELED_FILTER)
    already_ok = (
        os.path.isdir(viking_path)
        and _filter_has_tileset_child(viking)
        and not os.path.isdir(leveled_tileset)
        and not _filter_has_tileset_child(leveled)
    )
    if already_ok:
        return False

    source = None
    if os.path.isdir(leveled_tileset) and not os.path.isdir(viking_path):
        source = leveled_tileset
    elif not os.path.isdir(viking_path):
        source = _tileset_source_from_path(source_dir)

    # Detach Leveled tileset metadata before moving files so Save does not follow a
    # stale Tileset_Link to Leveled/Tileset/VolumeData.xml.
    stripped = _remove_leveled_tileset_node(channel, dry_run)

    if os.path.isdir(viking_path):
        viking = _get_or_create_filter(channel, VikingTemFilter, bits=8)
        _attach_tileset(viking, None, dry_run)
        _lock_filter(viking)
        if os.path.isdir(leveled_tileset):
            prettyoutput.Log(f"REMOVE leftover immuno tileset {leveled_tileset}")
            if not dry_run:
                shutil.rmtree(leveled_tileset)
        return True

    if source is None:
        return stripped

    viking = _get_or_create_filter(channel, VikingTemFilter, bits=8)
    _attach_tileset(viking, source, dry_run)
    _lock_filter(viking)
    return True


def _relocate_channel_tileset(channel: ChannelNode, source_dir: str | None, dry_run: bool) -> bool:
    """TEM tilesets go on Leveled; immuno stain tilesets stay on that channel's VikingTEM."""
    if channel.Name == TEM_CHANNEL:
        return _relocate_tileset_to_leveled(channel, source_dir, dry_run)
    return _relocate_immuno_tileset_to_vikingtem(channel, source_dir, dry_run)


def _imageset_image_name(section_number: int, channel_name: str, filter_name: str) -> str:
    return BuildFilterImageName(section_number, channel_name, filter_name, '.png')


def _attach_imageset_png(filter_node: FilterNode, png: RootPng, channel_name: str, dry_run: bool) -> bool:
    """Move an assemble PNG onto the filter ImageSet. Returns True when a file was moved."""
    added, imageset = filter_node.GetOrCreateImageset()
    added_level, level = imageset.GetOrCreateLevel(png.downsample, GenerateData=False)
    if level is None:
        raise NornirUserException(f"Could not create ImageSet level {png.downsample} on {filter_node.FullPath}")
    dest_name = _imageset_image_name(png.section, channel_name, filter_node.Name)
    image = ImageNode.Create(dest_name)
    added_image, image = level.UpdateOrAddChildByAttrib(image, 'Path')
    dest = image.FullPath
    if os.path.isfile(dest):
        prettyoutput.Log(f"SKIP image already present: {dest}")
        if not dry_run:
            _mark_file_readonly(dest)
        return False
    if not os.path.isfile(png.source_path):
        prettyoutput.Log(f"SKIP missing assemble image: {png.source_path}")
        return False
    _move(png.source_path, dest, dry_run)
    if not dry_run:
        _mark_file_readonly(dest)
    return True


def _adopt_mosaics(channel: ChannelNode, section: SectionInventory, dry_run: bool) -> None:
    for key, name, transform_type, dest_name in _MOSAICS:
        source = section.mosaics.get(key)
        dest_guess = os.path.join(channel.FullPath, dest_name)
        if source is None and not os.path.isfile(dest_guess):
            continue
        transform = TransformNode.Create(Name=name, Type=transform_type, Path=dest_name)
        added, transform = channel.UpdateOrAddChildByAttrib(transform, 'Name')
        dest = transform.FullPath
        if os.path.isfile(dest):
            prettyoutput.Log(f"SKIP mosaic already present: {dest}")
        elif source is not None:
            _move(source, dest, dry_run)
        else:
            continue
        if not dry_run:
            transform.ResetChecksum()
        _lock_transform(transform)


def _report_missing_tiles(section: SectionInventory) -> None:
    mosaic = section.mosaics.get('grid') or section.mosaics.get('supertile')
    pyramid = section.capture_pyramids.get('Raw8')
    if mosaic is None or pyramid is None:
        return
    level_one = os.path.join(pyramid, templates.LevelFormat % 1)
    if not os.path.isdir(level_one):
        levels = _level_dirs(pyramid)
        level_one = levels[0][1] if levels else pyramid
    missing = missing_mosaic_tiles(mosaic, level_one)
    if missing:
        prettyoutput.Log(
            f"Section {section.number:04d}: {len(missing)} mosaic tiles missing from capture pyramid "
            f"(not inventing transforms). First: {missing[:5]}")


def _attach_existing_imagesets(channel: ChannelNode, section_number: int) -> bool:
    """Create ImageSet nodes for assemble PNGs already sitting in dest filter folders."""
    if not os.path.isdir(channel.FullPath):
        return False
    changed = False
    with os.scandir(channel.FullPath) as entries:
        filter_dirs = [entry for entry in entries if entry.is_dir()]
    for entry in filter_dirs:
        imageset_root = os.path.join(entry.path, ImageSetNode.DefaultPath)
        if not os.path.isdir(imageset_root):
            continue
        bits = 16 if entry.name == '16-bit' else 8
        mask_name = 'Mask' if entry.name == 'Blob' else None
        filter_node = _get_or_create_filter(channel, entry.name, bits=bits, mask_name=mask_name)
        for downsample, level_dir in _level_dirs(imageset_root):
            dest_name = _imageset_image_name(section_number, channel.Name, filter_node.Name)
            dest = os.path.join(level_dir, dest_name)
            if not os.path.isfile(dest):
                continue
            added, imageset = filter_node.GetOrCreateImageset()
            added_level, level = imageset.GetOrCreateLevel(downsample, GenerateData=False)
            if level is None:
                continue
            image = ImageNode.Create(dest_name)
            added_image, image = level.UpdateOrAddChildByAttrib(image, 'Path')
            changed = changed or added or added_level or added_image
        _lock_filter(filter_node)
    return changed


def _adopt_tem_assemble_pngs(channel: ChannelNode, section: SectionInventory,
                             root_pngs: Iterable[RootPng], dry_run: bool) -> bool:
    """Move leftover TEM assemble PNGs (mosaic/blob/mask/thumbnail) onto dest ImageSets."""
    moved = False
    pngs = [png for png in root_pngs if png.section == section.number]
    pngs.extend(png for png in section.local_pngs if png.png_type != 'immuno')
    attached_thumbnail = False
    for png in pngs:
        if png.png_type == 'immuno':
            continue
        if png.png_type == '8-bit_mask':
            prettyoutput.Log(f"Leaving 8-bit mask in source (Raw8 ImageSet collision): {png.source_path}")
            continue
        if png.png_type == 'thumbnail':
            attached_thumbnail = True
        bits = 8 if png.filter_name in {'Raw8', 'Leveled', 'Blob', 'Mask'} else None
        mask_name = 'Mask' if png.filter_name == 'Blob' else None
        filter_node = _get_or_create_filter(channel, png.filter_name, bits=bits, mask_name=mask_name)
        moved = _attach_imageset_png(filter_node, png, TEM_CHANNEL, dry_run) or moved
        _lock_filter(filter_node)

    if section.local_thumbnail is not None and not attached_thumbnail:
        match = re.search(r'(\d+)\.png$', section.local_thumbnail, re.IGNORECASE)
        downsample = int(match.group(1)) if match else 1
        png = RootPng(section.number, 'thumbnail', downsample, section.local_thumbnail, LEVELED_FILTER)
        filter_node = _get_or_create_filter(channel, LEVELED_FILTER, bits=8)
        moved = _attach_imageset_png(filter_node, png, TEM_CHANNEL, dry_run) or moved
        _lock_filter(filter_node)
    return moved


def _adopt_immuno_assemble_pngs(immuno_channel: ChannelNode, immuno_name: str,
                                section: SectionInventory, dry_run: bool) -> bool:
    moved = False
    for png in section.local_pngs:
        if png.png_type != 'immuno':
            continue
        if immuno_name.lower() not in os.path.basename(png.source_path).lower():
            continue
        leveled = _get_or_create_filter(immuno_channel, LEVELED_FILTER, bits=8)
        moved = _attach_imageset_png(leveled, png, immuno_name, dry_run) or moved
        _lock_filter(leveled)
    return moved


def _refresh_adopted_section(section_node: SectionNode, section: SectionInventory, meta: VolumeMeta,
                             root_pngs: Iterable[RootPng], dry_run: bool) -> list[ChannelNode]:
    """Restore leftover Original files onto an already-adopted section and lock ImageSet PNGs."""
    dest_section = section_node.FullPath
    dest_channel_path = os.path.join(dest_section, TEM_CHANNEL)
    dirty: list[ChannelNode] = []

    channel = section_node.GetChannel(TEM_CHANNEL)
    has_tem = (
        bool(section.mosaics or section.capture_pyramids or section.viking_tileset)
        or os.path.isdir(dest_channel_path)
        or channel is not None)
    if has_tem:
        if channel is None:
            channel = _get_or_create_channel(section_node, TEM_CHANNEL, meta.scale_nm)
        _adopt_mosaics(channel, section, dry_run)
        raw8_dest = os.path.join(channel.FullPath, 'Raw8', TilePyramidNode.DefaultPath)
        if 'Raw8' in section.capture_pyramids or os.path.isdir(raw8_dest):
            raw8 = _get_or_create_filter(channel, 'Raw8', bits=8)
            _attach_tile_pyramid(raw8, section.capture_pyramids.get('Raw8'), dry_run)
            _lock_filter(raw8)
        raw16_dest = os.path.join(channel.FullPath, '16-bit', TilePyramidNode.DefaultPath)
        if '16-bit' in section.capture_pyramids or os.path.isdir(raw16_dest):
            raw16 = _get_or_create_filter(channel, '16-bit', bits=16)
            _attach_tile_pyramid(raw16, section.capture_pyramids.get('16-bit'), dry_run)
            _lock_filter(raw16)
        png_moved = _adopt_tem_assemble_pngs(channel, section, root_pngs, dry_run)
        xml_added = _attach_existing_imagesets(channel, section.number)
        relocated = _relocate_tileset_to_leveled(channel, section.viking_tileset, dry_run)
        for filter_node in channel.Filters:
            _lock_filter(filter_node)
            _mark_imageset_pngs_readonly(filter_node, dry_run)
        if png_moved or xml_added or relocated:
            dirty.append(channel)

    immuno_items = dict(section.immuno_channels)
    if os.path.isdir(dest_section):
        with os.scandir(dest_section) as entries:
            for entry in entries:
                if entry.is_dir() and entry.name != TEM_CHANNEL:
                    immuno_items.setdefault(entry.name, entry.path)
    for immuno_name, immuno_path in sorted(immuno_items.items()):
        immuno_channel = section_node.GetChannel(immuno_name)
        if immuno_channel is None:
            immuno_channel = _get_or_create_channel(section_node, immuno_name, meta.scale_nm)
        png_moved = _adopt_immuno_assemble_pngs(immuno_channel, immuno_name, section, dry_run)
        xml_added = _attach_existing_imagesets(immuno_channel, section.number)
        tileset_source = immuno_path if os.path.isdir(immuno_path) else None
        relocated = _relocate_immuno_tileset_to_vikingtem(immuno_channel, tileset_source, dry_run)
        for filter_node in immuno_channel.Filters:
            _lock_filter(filter_node)
            _mark_imageset_pngs_readonly(filter_node, dry_run)
        if png_moved or xml_added or relocated:
            dirty.append(immuno_channel)
    return dirty


def _adopt_section(block: BlockNode, section: SectionInventory, meta: VolumeMeta,
                   root_pngs: Iterable[RootPng], flip_list: list[int],
                   dry_run: bool) -> SectionNode | None:
    dest_section = os.path.join(block.FullPath, _section_template(section.number))
    dest_channel_path = os.path.join(dest_section, TEM_CHANNEL)
    if os.path.isfile(os.path.join(dest_section, 'VolumeData.xml')):
        prettyoutput.Log(f"SKIP section file adopt: {section.number}")
        section_node = block.GetSection(section.number)
        if section_node is None:
            section_node = _get_or_create_section(block, section.number)
        dirty_channels = _refresh_adopted_section(section_node, section, meta, root_pngs, dry_run)
        if not dry_run:
            for channel in dirty_channels:
                _save_xml(channel, recurse=True)
        return None

    _report_missing_tiles(section)

    section_node = _get_or_create_section(block, section.number)
    has_tem = (
        bool(section.mosaics or section.capture_pyramids or section.viking_tileset)
        or os.path.isdir(dest_channel_path))
    if has_tem:
        channel = _get_or_create_channel(section_node, TEM_CHANNEL, meta.scale_nm)
        _adopt_mosaics(channel, section, dry_run)

        raw8_dest = os.path.join(channel.FullPath, 'Raw8', TilePyramidNode.DefaultPath)
        if 'Raw8' in section.capture_pyramids or os.path.isdir(raw8_dest):
            raw8 = _get_or_create_filter(channel, 'Raw8', bits=8)
            _attach_tile_pyramid(raw8, section.capture_pyramids.get('Raw8'), dry_run)
            _lock_filter(raw8)
        raw16_dest = os.path.join(channel.FullPath, '16-bit', TilePyramidNode.DefaultPath)
        if '16-bit' in section.capture_pyramids or os.path.isdir(raw16_dest):
            raw16 = _get_or_create_filter(channel, '16-bit', bits=16)
            _attach_tile_pyramid(raw16, section.capture_pyramids.get('16-bit'), dry_run)
            _lock_filter(raw16)

        _adopt_tem_assemble_pngs(channel, section, root_pngs, dry_run)
        _attach_existing_imagesets(channel, section.number)
        _relocate_tileset_to_leveled(channel, section.viking_tileset, dry_run)

        for filter_node in channel.Filters:
            _lock_filter(filter_node)
            _mark_imageset_pngs_readonly(filter_node, dry_run)

        if section.histogram_path is not None:
            with open(section.histogram_path, encoding='utf-8', errors='replace') as handle:
                _add_notes(channel, handle.read(), os.path.basename(section.histogram_path))

    immuno_items = dict(section.immuno_channels)
    if os.path.isdir(dest_section):
        with os.scandir(dest_section) as entries:
            for entry in entries:
                if entry.is_dir() and entry.name != TEM_CHANNEL:
                    immuno_items.setdefault(entry.name, entry.path)

    for immuno_name, immuno_path in sorted(immuno_items.items()):
        immuno_channel = _get_or_create_channel(section_node, immuno_name, meta.scale_nm)
        _adopt_immuno_assemble_pngs(immuno_channel, immuno_name, section, dry_run)
        _attach_existing_imagesets(immuno_channel, section.number)
        tileset_source = immuno_path if os.path.isdir(immuno_path) else None
        _relocate_immuno_tileset_to_vikingtem(immuno_channel, tileset_source, dry_run)
        for filter_node in immuno_channel.Filters:
            _lock_filter(filter_node)
            _mark_imageset_pngs_readonly(filter_node, dry_run)

    if section.number in flip_list:
        _add_notes(section_node, 'Listed in FlipList.txt (pixels not flipped)', 'FlipList.txt')
    return section_node


def _imageset_fullpath(volume_root: str, section_number: int, filter_name: str, downsample: int) -> str:
    section = _section_template(section_number)
    image_name = _imageset_image_name(section_number, TEM_CHANNEL, filter_name)
    level = LevelNode.PredictPath(downsample)
    return os.path.join(
        volume_root, BLOCK_NAME, section, TEM_CHANNEL, filter_name, ImageSetNode.DefaultPath, level, image_name)


def _adopt_stos(block: BlockNode, volume_root: str, record: StosRecord, dry_run: bool) -> StosGroupNode | None:
    conventional = StosGroupNode.GenerateStosFilenameFromParts(
        record.mapped, record.control, TEM_CHANNEL, 'Raw8', TEM_CHANNEL, 'Raw8')
    added, group = block.GetOrCreateStosGroup(record.group_name, record.group_downsample)
    dest_path = os.path.join(group.FullPath, conventional)
    dest_exists = os.path.isfile(dest_path)
    control_blob = _imageset_fullpath(volume_root, record.control, 'Blob', record.downsample)
    mapped_blob = _imageset_fullpath(volume_root, record.mapped, 'Blob', record.downsample)
    control_mask = _imageset_fullpath(volume_root, record.control, 'Mask', record.downsample)
    mapped_mask = _imageset_fullpath(volume_root, record.mapped, 'Mask', record.downsample)
    if dest_exists:
        prettyoutput.Log(f"SKIP stos already present: {dest_path}")
    else:
        if not dry_run:
            for required in (control_blob, mapped_blob):
                if not os.path.isfile(required):
                    prettyoutput.Log(
                        f"SKIP stos {os.path.basename(record.source_path)}: missing assemble image {required}")
                    return None
            if not os.path.isfile(record.source_path):
                prettyoutput.Log(f"SKIP stos source missing: {record.source_path}")
                return None
        prettyoutput.Log(f"WRITE {dest_path} from {record.source_path}")
        if not dry_run:
            stos = StosFile.Load(record.source_path, resolve_paths=True)
            stos.ControlImageFullPath = control_blob
            stos.MappedImageFullPath = mapped_blob
            if os.path.isfile(control_mask) and os.path.isfile(mapped_mask):
                stos.ControlMaskFullPath = control_mask
                stos.MappedMaskFullPath = mapped_mask
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            stos.Save(dest_path)
            os.remove(record.source_path)

    added_mapping, mapping = group.GetOrCreateSectionMapping(record.mapped)
    transform = TransformNode.Create(
        Name=str(record.control),
        Type=record.transform_type,
        Path=conventional,
        attrib={
            'ControlSectionNumber': str(record.control),
            'MappedSectionNumber': str(record.mapped),
            'MappedChannelName': TEM_CHANNEL,
            'MappedFilterName': 'Raw8',
            'ControlChannelName': TEM_CHANNEL,
            'ControlFilterName': 'Raw8',
        })
    added, transform = mapping.UpdateOrAddChildByAttrib(transform, 'Path')
    if not dry_run:
        if os.path.isfile(control_blob):
            transform.attrib['ControlImageChecksum'] = str(FilesizeChecksum(control_blob))
        if os.path.isfile(mapped_blob):
            transform.attrib['MappedImageChecksum'] = str(FilesizeChecksum(mapped_blob))
        if os.path.isfile(control_mask):
            transform.attrib['ControlMaskImageChecksum'] = str(FilesizeChecksum(control_mask))
        if os.path.isfile(mapped_mask):
            transform.attrib['MappedMaskImageChecksum'] = str(FilesizeChecksum(mapped_mask))
        transform.ResetChecksum()
    _lock_transform(transform)
    return group


def _adopt_zips(dest_root: str, source_root: str, dry_run: bool) -> None:
    for name in _ROOT_ZIPS:
        source = os.path.join(source_root, name)
        dest = os.path.join(dest_root, name)
        if not os.path.isfile(source):
            if os.path.isfile(dest):
                prettyoutput.Log(f"SKIP zip already moved: {dest}")
            else:
                prettyoutput.Log(f"SKIP missing zip: {source}")
            continue
        _move(source, dest, dry_run)


def _load_stos_map(block: BlockNode, stos_map_path: str, center: int) -> None:
    stos_map = block.GetOrCreateStosMap('StosMap')
    stos_map.CenterSection = center
    with open(stos_map_path, encoding='utf-8') as handle:
        for line_number, line in enumerate(handle):
            line = line.strip()
            if not line or line_number == 0:
                continue
            if line.lower().startswith('mapped'):
                continue
            parts = re.split(r'\s+', line, maxsplit=3)
            if len(parts) < 2:
                continue
            try:
                mapped = int(parts[0])
                control = int(parts[1])
            except ValueError:
                continue
            stos_map.AddMapping(control, mapped)


def _copy_sidecars(dest_root: str, source_root: str, dry_run: bool) -> None:
    for name in _SIDECAR_FILES:
        _copy_if_needed(os.path.join(source_root, name), os.path.join(dest_root, name), dry_run)


def Import(VolumeElement: VolumeNode, ImportPath: str, Sections: list[int] | None = None,
           DryRun: bool = False, **kwargs) -> Generator[VolumeNode | None, None, None]:
    """Adopt ``ImportPath`` (Viking clone) into ``VolumeElement`` (modern dest)."""
    dest_root = VolumeElement.FullPath
    source_root = os.path.abspath(ImportPath)
    _refuse_if_unsafe(dest_root, source_root)

    inventory = inventory_source(source_root)
    _refuse_if_incomplete(inventory, dest_root)

    prettyoutput.Log(
        f"Inventoried {len(inventory.sections)} sections, {len(inventory.stos)} stos, "
        f"{len(inventory.root_pngs)} root assemble PNGs. Scale={inventory.meta.scale_nm} nm/px")

    requested: set[int] | None = None
    if Sections:
        requested = set(int(value) for value in Sections)

    dest_tem = os.path.join(dest_root, BLOCK_NAME)
    if os.path.isdir(dest_tem):
        with os.scandir(dest_tem) as entries:
            for entry in entries:
                if not entry.is_dir() or not entry.name.isdigit():
                    continue
                number = int(entry.name)
                if number in inventory.sections:
                    continue
                if requested is not None and number not in requested:
                    continue
                inventory.sections[number] = SectionInventory(number=number, path=entry.path)
                prettyoutput.Log(f"Resume dest section {number:04d} with no remaining source folder")

    dry_run = bool(DryRun)
    if dry_run:
        prettyoutput.Log("DryRun: printing planned moves, not writing dest data")

    _copy_sidecars(dest_root, source_root, dry_run)
    block = _get_or_create_block(VolumeElement)
    if inventory.stos_map_path is not None and not dry_run:
        _load_stos_map(block, inventory.stos_map_path, inventory.meta.default_section)
    if not dry_run:
        _save_xml(VolumeElement, recurse=False)
        _save_xml(block, recurse=False)

    section_numbers = [number for number in sorted(inventory.sections) if requested is None or number in requested]
    total = len(section_numbers)
    track_id = "adopt_viking:sections"
    if total:
        report_iterate(track_id, 0, total, "AdoptVikingVolume")

    completed = 0
    for number in section_numbers:
        section_node = _adopt_section(
            block,
            inventory.sections[number],
            inventory.meta,
            inventory.root_pngs,
            inventory.flip_list,
            dry_run)
        completed += 1
        report_iterate(track_id, completed, total, "AdoptVikingVolume", section=number)
        if not dry_run and section_node is not None:
            _save_xml(section_node, recurse=True)
            _save_xml(block, recurse=False)
        yield None
    if total:
        report_iterate_complete(track_id, total)

    for record in inventory.stos:
        if requested is not None and (record.mapped not in requested and record.control not in requested):
            continue
        if requested is not None and not (record.mapped in requested and record.control in requested):
            dest_mapped = os.path.join(block.FullPath, _section_template(record.mapped))
            dest_control = os.path.join(block.FullPath, _section_template(record.control))
            if not (os.path.isdir(dest_mapped) and os.path.isdir(dest_control)):
                prettyoutput.Log(
                    f"SKIP stos {os.path.basename(record.source_path)} until both sections are adopted")
                continue
        group = _adopt_stos(block, dest_root, record, dry_run)
        if not dry_run and group is not None:
            _save_xml(group, recurse=True)
            _save_xml(block, recurse=False)
        yield None

    _adopt_zips(dest_root, source_root, dry_run)
    if not dry_run:
        _save_xml(block, recurse=False)
        _save_xml(VolumeElement, recurse=False)
    yield None
