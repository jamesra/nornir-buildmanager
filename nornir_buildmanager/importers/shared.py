'''
Created on Apr 18, 2019

@author: u0490822
'''

import glob
import json
import logging
import math
import os
import re
import shutil
import sys
import time
import datetime
from collections.abc import Mapping
from typing import Iterable, NamedTuple, Callable, Sequence, cast
from xml.sax.saxutils import escape

import nornir_buildmanager
from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.volumemanager import (
    XElementWrapper,
    HistogramNode,
    ImageNode,
    DataNode,
    NotesNode,
    BlockNode,
    VolumeNode,
    SectionNode,
    ChannelNode,
    FilterNode,
)
import nornir_shared.files as files
import nornir_shared.prettyoutput as prettyoutput
from nornir_shared.histogram import Histogram
import nornir_shared.plot as plot
import nornir_pools
from nornir_imageregistration import noise_crush, tile_gains
from nornir_imageregistration.noise_crush import CrushParams
import numpy as np
from numpy.typing import NDArray
from PIL import Image


# Bump when histogram cache semantics change (e.g. default stride).
HISTOGRAM_CACHE_VERSION: int = 1
# Sidecar next to Histogram.xml: Histogram.cache.json
HISTOGRAM_CACHE_SIDECAR_SUFFIX: str = '.cache.json'
# Noise estimate next to Histogram.xml: Histogram.noise.json
NOISE_CACHE_VERSION: int = 1
NOISE_CACHE_SUFFIX: str = '.noise.json'
# Tiles sampled per section for the noise estimate; exposure is section-wide.
NOISE_SAMPLE_TILES: int = 16
# Per-tile brightness gains solved before the crush, next to Histogram.xml: Histogram.gains.json
GAINS_CACHE_VERSION: int = 1
GAINS_CACHE_SUFFIX: str = '.gains.json'
# Overlap statistics are measured on tiles reduced by this factor: the strips hold tens of thousands of pixels, and
# stage error of hundreds of pixels barely moves a strip's median (RC3: gains at 8 matched level 1).
IMPORT_GAIN_DOWNSAMPLE: int = 8

_NOTES_SKIP_FILENAMES: frozenset[str] = frozenset({'ContrastOverrides.txt', 'Timing.txt'})
_CAPTURE_NOTE_FALLBACK_EXTENSIONS: frozenset[str] = frozenset({'.idoc', '.mrc', '.dm4', '.pmg'})
_DEFAULT_NOTES_BLOCK_NAME: str = 'TEM'
_DEFAULT_NOTES_CHANNEL_NAME: str = 'TEM'


class FilenameMetadata(NamedTuple):
    fullpath: str
    number: int
    version: str
    name: str
    downsample: int
    extension: str


class MinMaxGamma(NamedTuple):
    min: float
    max: float
    gamma: float = 1.0


class ContrastValue(NamedTuple):
    Section: int
    Min: int
    Max: int
    Gamma: float = 1.0


# FilenameMetadata = collections.namedtuple('SectionInfo', 'fullpath number version name downsample extension')
# MinMaxGamma = collections.namedtuple('MinMaxGamma', 'min max gamma')

# Global instance of our parser for filenames that is initialized upon first use
_InputFileRegExParser = None


def GetSectionInfo(fullpath) -> FilenameMetadata:
    '''Given a path or filename returns the meta data we can determine from the name
       :returns: A named tuple with (fullpath number version name downsample extension)
    '''
    fileName = os.path.basename(fullpath)

    d = ParseMetadataFromFilename(fileName)

    return FilenameMetadata(fullpath, d['Number'], d['Version'], d['Name'], d['Downsample'], d['Extension'])  # type: ignore[arg-type]


def _creation_time(path: str) -> float:
    """File creation time where the platform records it, otherwise its modification time."""
    st = os.stat(path)
    birth = getattr(st, 'st_birthtime', None)
    return float(birth) if birth else float(st.st_mtime)


def SelectOneFilePerSection(paths: Iterable[str]) -> list[str]:
    """Keep one input file per section number, in path order.

    The highest version wins: recaptures are versioned upward and no version sorts first. Files
    that tie on version (e.g. ``0347.mrc`` copied twice under other names) are decided by the later
    creation date. Each file left out is logged. Files whose names do not parse are kept, so the
    importer reports them as before.
    """
    by_number: dict[int, list[tuple[FilenameMetadata, str]]] = {}
    unparsed: list[str] = []
    for path in paths:
        try:
            info = GetSectionInfo(path)
        except NornirUserException:
            unparsed.append(path)
            continue
        by_number.setdefault(int(info.number), []).append((info, path))

    chosen = list(unparsed)
    for number, candidates in sorted(by_number.items()):
        if len(candidates) == 1:
            chosen.append(candidates[0][1])
            continue
        best = max(candidates, key=lambda c: (str(c[0].version), _creation_time(c[1])))
        chosen.append(best[1])
        for info, path in candidates:
            if path != best[1]:
                prettyoutput.Log(f"Section {number}: importing {os.path.basename(best[1])}, skipping "
                                 f"{os.path.basename(path)} (higher version wins, then later creation date)")
    return sorted(chosen)


def FileMetaDataStrHeader():
    output = "{0:<22}\t{1:<6}{2:<5}{3:<16}{4:<5}{5}\n".format("Path", "#", "Ver", "Name", "Ds", "ext")
    return output


def FileMetaDataStr(data):
    '''Provides a pretty string for a FilenameMetadata tuple'''
    v = data.version
    if v == '\0':
        v = None
    output = "{0:<22}\t{1:<6}{2:<5}{3:<16}{4:<5}{5}".format(os.path.basename(data.fullpath), str(data.number), str(v),
                                                            str(data.name), str(data.downsample), str(data.extension))
    return output


def _TryCleanDataWithNotInCurrentImport(input_path: str,
                                        elements: Iterable[XElementWrapper],
                                        new_section_info: FilenameMetadata | None = None) -> bool:
    removed = False
    for elem in elements:
        try:
            old_section_info = GetSectionInfo(elem.Path)
        except NornirUserException:
            continue  # Do not remove information that doesn't have a parsable path

        if new_section_info is None:
            new_section_info = GetSectionInfo(input_path)

        if new_section_info.number != old_section_info.number:
            continue

        elem_file_path = os.path.join(input_path, elem.Path)
        if not os.path.exists(elem_file_path):
            elem.Clean(
                f"Removing <{elem.tag}> element created {elem.CreationTime}.  Source file not found in current import folder {elem_file_path}")
            removed = True

    return removed


def TryCleanNotes(containerObj, input_path: str, logger, new_section_info: FilenameMetadata | None = None) -> bool:
    """
    Remove notes elements whose files do not exist in the input path
    :param new_section_info: Section information for the section we are importing notes from
    :return: True if a Note element was removed
    """

    notes = containerObj.findall('Notes')
    return _TryCleanDataWithNotInCurrentImport(input_path, notes, new_section_info)


def TryCleanIdocCaptureData(containerObj, input_path: str, logger,
                            new_section_info: FilenameMetadata | None = None) -> bool:
    """
    Remove Data elements whose files do not exist in the input path
    :param new_section_info: Section information for the section we are importing notes from
    :return: True if an element was removed
    """
    data_elements = containerObj.findall('Data')
    filtered_list = []
    for data in data_elements:
        _, ext = os.path.splitext(data.Path)
        if ext == '.log' or ext == '.idoc':
            filtered_list.append(data)

    return _TryCleanDataWithNotInCurrentImport(input_path, filtered_list, new_section_info)


def TryAddHistogram(containerObj: XElementWrapper,
                    InputPath: str,
                    image_ext: str | None = None,
                    min_cutoff=None,
                    max_cutoff=None,
                    gamma=None):
    """
    :param containerObj:
    :param InputPath:
    :param logger:
    :return:
    """

    # if new_section_info is None:
    #    new_section_info = GetSectionInfo(InputPath)

    if image_ext is None:
        image_ext = '.png'

    histogram_node = HistogramNode.Create(Type='RawDataHistogram')
    [added, histogram_node] = containerObj.UpdateOrAddChildByAttrib(histogram_node, 'Type')

    histogram_image_path = os.path.join(InputPath, f'Histogram{image_ext}')
    if os.path.exists(histogram_image_path):
        image_node = ImageNode.Create(Path=f'RawDataHistogram{image_ext}',
                                     attrib={'Name': 'RawDataHistogram'})
        [image_added, image_node] = histogram_node.UpdateOrAddChildByAttrib(image_node, 'Name')
        existing_removed = files.RemoveOutdatedFile(histogram_image_path, image_node.FullPath)
        if image_added or existing_removed:
            shutil.copyfile(histogram_image_path, image_node.FullPath)

    histogram_xml_path = os.path.join(InputPath, 'Histogram.xml')
    if os.path.exists(histogram_xml_path):
        image_node = DataNode.Create(Path='RawDataHistogram.xml',
                                    attrib={'Name': 'RawDataHistogram'})
        [data_added, image_node] = histogram_node.UpdateOrAddChildByAttrib(image_node, 'Name')
        # Copy the XML, not the image: the image branch above handles that (this copied the PNG under the XML's name
        # and failed when only Histogram.xml existed).
        existing_removed = files.RemoveOutdatedFile(histogram_xml_path, image_node.FullPath)
        if data_added or existing_removed:
            shutil.copyfile(histogram_xml_path, image_node.FullPath)

    autolevel_hint = histogram_node.GetOrCreateAutoLevelHint()
    autolevel_hint.UserRequestedGamma = gamma  # type: ignore[assignment]
    autolevel_hint.UserRequestedMaxIntensityCutoff = max_cutoff  # type: ignore[assignment]
    autolevel_hint.UserRequestedMinIntensityCutoff = min_cutoff  # type: ignore[assignment]

    return added or data_added or image_added or autolevel_hint.AttributesChanged or histogram_node.ChildrenChanged


def _notes_content_differs(source: str, dest: str) -> bool:
    """Return True when *dest* is missing or its bytes differ from *source*.

    The mtime comparison in ``RemoveOutdatedFile`` treats equal timestamps as "not
    outdated", so a notes file rewritten within the filesystem's timestamp granularity is
    not recopied -- while the ``<Notes>`` element *is* rebuilt, because it is read from the
    source. The copy on disk and the metadata then disagree, silently.

    Notes files are a few KB, so comparing content costs nothing next to the copy it guards
    and does not depend on clock resolution. Not a substitute for the mtime check in general:
    that check exists for the image pyramids, where reading both sides to compare them would
    be far more expensive than the copy. See review #245.
    """
    try:
        with open(dest, 'rb') as dest_handle:
            dest_bytes = dest_handle.read()
    except OSError:
        return True

    with open(source, 'rb') as source_handle:
        return source_handle.read() != dest_bytes


def _is_same_notes_path(source: str, dest: str) -> bool:
    """Return True when source and dest refer to the same filesystem path."""
    source_abs = os.path.normpath(os.path.abspath(source))
    dest_abs = os.path.normpath(os.path.abspath(dest))
    if source_abs == dest_abs:
        return True
    try:
        return os.path.samefile(source, dest)
    except OSError:
        return False


def _read_notes_text_with_encoding(filename: str) -> tuple[str, str]:
    """Decode notes bytes; record the codec actually used (#246).

    Prefer UTF-8 so the ``encoding`` attribute matches common modern files. Fall back to
    cp1252 for microscope-era Latin-1/Windows notes that are not valid UTF-8. cp1252
    accepts every byte sequence, so this always returns.
    """
    with open(filename, 'rb') as handle:
        raw = handle.read()
    try:
        return raw.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        return raw.decode('cp1252'), 'cp1252'


def TryAddNotes(containerObj,
                InputPath: str,
                logger: logging.Logger | None = None,
                new_section_info: FilenameMetadata | None = None) -> bool:
    """Copy notes ``*.txt`` files from InputPath and add or update ``<Notes>`` metadata.

    Recopies a notes file when the destination is missing, older than the source, or differs
    from it in content. Returns True when a file was copied or Notes text/encoding changed.
    ``new_section_info`` is accepted for importer call-site compatibility.
    """

    NotesFiles = glob.iglob(os.path.join(InputPath, '*.txt'))
    NotesAdded = False
    for filename in NotesFiles:
        NotesFilename = os.path.basename(filename)
        if NotesFilename in _NOTES_SKIP_FILENAMES:
            continue

        try:
            CopiedNotesFullPath = os.path.join(containerObj.FullPath, NotesFilename)
            os.makedirs(containerObj.FullPath, exist_ok=True)
            copied = False
            if not _is_same_notes_path(filename, CopiedNotesFullPath):
                # Content, not just mtime: equal timestamps read as "not outdated", which
                # left a stale copy on disk beside freshly rebuilt <Notes> metadata.
                outdated = files.RemoveOutdatedFile(filename, CopiedNotesFullPath)
                if outdated or _notes_content_differs(filename, CopiedNotesFullPath):
                    shutil.copyfile(filename, CopiedNotesFullPath)
                    copied = True

            notesTxt, encoding = _read_notes_text_with_encoding(filename)
            notesTxt = notesTxt.replace('\0', '')

            if len(notesTxt) > 0:
                XMLnotesTxt = escape(notesTxt)

                NotesNodeObj = NotesNode.Create(Text=XMLnotesTxt,
                                                SourceFilename=NotesFilename)
                containerObj.RemoveOldChildrenByAttrib('Notes', 'Path', NotesFilename)
                [added, NotesNodeObj] = containerObj.UpdateOrAddChildByAttrib(NotesNodeObj, 'SourceFilename')

                old_text = NotesNodeObj.text
                old_encoding = NotesNodeObj.attrib.get('encoding')
                NotesNodeObj.text = XMLnotesTxt
                NotesNodeObj.encoding = encoding
                text_changed = old_text != XMLnotesTxt
                encoding_changed = old_encoding != encoding
                if text_changed:
                    NotesNodeObj.AttributesChanged = True

                NotesAdded = NotesAdded or added or copied or text_changed or encoding_changed
            elif copied:
                NotesAdded = True

        except Exception:
            (_etype, evalue, etraceback) = sys.exc_info()
            prettyoutput.Log("Attempt to include notes from " + filename + " failed.\n" + str(evalue))
            prettyoutput.Log(etraceback)

    return NotesAdded


def _folder_has_import_notes(folder_path: str) -> bool:
    """Return True if folder_path contains a notes ``*.txt`` file that is not skipped."""
    for filename in glob.iglob(os.path.join(folder_path, '*.txt')):
        if os.path.basename(filename) not in _NOTES_SKIP_FILENAMES:
            return True
    return False


def _section_info_from_capture_siblings(folder_path: str) -> FilenameMetadata | None:
    """Parse section metadata from capture files beside notes in an unnumbered folder."""
    try:
        with os.scandir(folder_path) as scanner:
            for entry in scanner:
                if not entry.is_file():
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if ext not in _CAPTURE_NOTE_FALLBACK_EXTENSIONS:
                    continue
                try:
                    info = GetSectionInfo(entry.path)
                except NornirUserException:
                    continue
                return FilenameMetadata(folder_path,
                                        info.number,
                                        info.version,
                                        info.name,
                                        info.downsample,
                                        info.extension)
    except OSError:
        return None
    return None


def _iter_notes_source_dirs(import_path: str) -> list[FilenameMetadata]:
    """Yield one source folder per section number from an IDoc-style import tree."""
    from nornir_buildmanager.importers import find

    candidates = find.find_section_candidates(import_path, None)
    by_number: dict[int, FilenameMetadata] = {}
    used_paths: set[str] = set()
    for number, metas in candidates.items():
        if not metas:
            continue
        by_number[number] = metas[0]
        used_paths.add(os.path.normpath(metas[0].fullpath))

    dirs_to_check = [import_path]
    try:
        with os.scandir(import_path) as scanner:
            for entry in scanner:
                if entry.is_dir():
                    dirs_to_check.append(entry.path)
    except OSError:
        return list(by_number.values())

    for folder in dirs_to_check:
        if os.path.normpath(folder) in used_paths:
            continue
        if not _folder_has_import_notes(folder):
            continue
        info = _section_info_from_capture_siblings(folder)
        if info is None:
            continue
        if info.number in by_number:
            continue
        by_number[info.number] = info
        used_paths.add(os.path.normpath(folder))

    return list(by_number.values())


def _find_existing_section(volume_obj: VolumeNode, section_number: int) -> SectionNode | None:
    """Return the first section with the given number across all blocks."""
    for block in volume_obj.Blocks:
        section = block.GetSection(section_number)
        if section is not None:
            return section
    return None


def _get_or_create_section_for_notes(volume_obj: VolumeNode, section_number: int) -> SectionNode:
    """Return an existing section or create TEM block/section nodes to hold notes."""
    section = _find_existing_section(volume_obj, section_number)
    if section is not None:
        return section

    block = volume_obj.GetBlock(_DEFAULT_NOTES_BLOCK_NAME)
    if block is None:
        [_added, block] = volume_obj.UpdateOrAddChildByAttrib(
            BlockNode.Create(_DEFAULT_NOTES_BLOCK_NAME), 'Name')

    [_created, section] = block.GetOrCreateSection(section_number)
    return section


def _channels_for_notes(section_obj: SectionNode) -> list[ChannelNode]:
    """Return existing channels, creating a TEM channel when the section has none."""
    channels: list[ChannelNode] = list(section_obj.Channels)  # type: ignore[arg-type]
    if channels:
        return channels

    [_added, channel] = section_obj.UpdateOrAddChildByAttrib(
        ChannelNode.Create(_DEFAULT_NOTES_CHANNEL_NAME), 'Name')
    return [cast(ChannelNode, channel)]


def RecoverNotesFromImportDir(volume_obj: VolumeNode,
                              import_path: str,
                              logger: logging.Logger | None = None) -> bool:
    """Copy changed notes from an IDoc-style import tree into matching volume sections.

    Creates a TEM Block/Section/Channel when the section is missing. Existing
    sections receive notes on every channel they already have.
    """
    if not os.path.exists(import_path):
        raise ValueError(f"Import Path does not exist: {import_path}")

    changed = False
    for meta in _iter_notes_source_dirs(import_path):
        source_dir = meta.fullpath
        if not _folder_has_import_notes(source_dir):
            prettyoutput.Log(f"No notes files found in {source_dir}.")
            continue

        section_obj = _get_or_create_section_for_notes(volume_obj, meta.number)
        for channel in _channels_for_notes(section_obj):
            cleaned = TryCleanNotes(channel, source_dir, logger, meta)
            added = TryAddNotes(channel, source_dir, logger, meta)
            changed = changed or cleaned or added

    return changed


def ParseMetadataFromFilename(string: str):
    '''
    Parses the filename of an input file to determine
        Number : Section Number
        Version : A letter indicating whether this is a recapture of the same section. In increasing alphabetical order.  'B' would be a recapture of 'A'
         
    '''
    global _InputFileRegExParser
    if _InputFileRegExParser is None:
        _InputFileRegExParser = re.compile(r"""
            (?P<Number>\d+)                            #Section Number
            #(?P<VersionSpace>\s)?                     #Possible space between section number and version
            (
                (?P<VersionSpace>[\s|_]+)?            #Possible space between section number and version
                (?P<Version>[^_|^\s]((?=[_|\s|\.])|$))
            )?                                         #Version letter 
            (
              (?P<DetailsSpace>[_|\s]+)                #Divider between section number/version and name, always present
              (?P<Name>(
                [a-zA-Z0-9]                            #Any letters
                |
                [ ](?![0-9]+\.)
              )+)                                      #Any spaces not followed by numbers and a period (The downsample value) 
            )?                                         #Name
            (
              (?P<DownsampleSpace>[_|\s]+)            #Divider between name and downsample/extension
              (?P<Downsample>\d+)                     #Downsample level if present
            )?
            #)                                             #Match the end of string if NumberOnly is not defined 
            (?P<Extension>\.\w+)?                          #Extension if present
            
            """, re.VERBOSE)

    m = _InputFileRegExParser.match(string)
    raiseException = m is None
    if m is not None:

        d = m.groupdict()
        section_number = d.get('Number', None)
        if section_number is not None:
            d['Number'] = int(section_number)
        else:
            raiseException = True

        version = d.get('Version', None)
        if version is None:
            version = '\0'  # Assign a letter that will sort earlier than 'A' in case someone names the first recapture A instead of B...
            d['Version'] = str.upper(version)
        else:
            d['Version'] = str.upper(str.strip(d['Version']))

        ds = d.get('Downsample', None)
        if ds is not None:
            d['Downsample'] = int(ds)

        if not raiseException:
            return d

    if raiseException:
        friendlyFormatDescription = "{Section#}[VersionLetter][_Section Name][_Downsample]\n\t{} => Required\t[] => Optional"
        raise NornirUserException(
            f'\n"{string}" cannot be parsed.\nFile/Directory meta-data is expected to be in the format:\n\t{friendlyFormatDescription}')


def CleanOutliersFromHistogram(hObj: Histogram) -> Histogram:
    """
    For Max-Value outliers this is a legacy function that supports old versions of SerialEM that falsely reported
    maxint for some pixels even though the camera was a 14-bit camera.  This applies to the original RC1 data.
    By the time RC2 was collected in March 2018 this bug was fixed

    However this function is worth retaining because Max and Min outliers can rarely occur if a tile is removed
    from the input before import but remain in the iDoc data.
    """

    hNew = Histogram.TryRemoveMaxValueOutlier(hObj, TrimOnly=False)
    if hNew is not None:
        hObj = hNew

    hNew = Histogram.TryRemoveMinValueOutlier(hObj, TrimOnly=False)
    if hNew is not None:
        hObj = hNew

    return hObj


def replace_extension(filename: str, new_extension: str) -> str:
    """
    Replace the extension of a file with a new extension
    :param filename: Filename to change
    :param new_extension: New extension to use
    :return: Filename with the new extension
    """
    (base, _) = os.path.splitext(filename)
    return f"{base}.{new_extension}"


def PlotHistogram(histogramFullPath: str, sectionNumber: int, minCutoff: float, maxCutoff: float,
                  force_recreate: bool):
    """
    :param histogramFullPath:  Output path of the image
    :param sectionNumber:
    :param minCutoff:
    :param maxCutoff:
    :param force_recreate:  If true recreate the histogram even if it exists
    :return:
    """
    HistogramImageFullPath = replace_extension(histogramFullPath, "png")
    if not os.path.exists(histogramFullPath):
        # The cache could not be written (read-only import directory); there is nothing to plot.
        return

    try:
        ImageRemoved = files.RemoveOutdatedFile(histogramFullPath, HistogramImageFullPath)

        if ImageRemoved or force_recreate or not os.path.exists(HistogramImageFullPath) or files.IsOlderThan(
                HistogramImageFullPath, datetime.date(year=2022, month=10, day=25)):
            #        pool = nornir_pools.GetGlobalMultithreadingPool()
            # pool.add_task(HistogramImageFullPath, plot.Histogram, histogramFullPath, HistogramImageFullPath, Title="Section %d\nRaw Data Pixel Intensity" % (sectionNumber), LinePosList=[minCutoff, maxCutoff])
            os.makedirs(os.path.dirname(HistogramImageFullPath), exist_ok=True)
            plot.Histogram(histogramFullPath, HistogramImageFullPath,
                           Title=f"Section {sectionNumber}\nRaw Data Pixel Intensity", LinePosList=[minCutoff, maxCutoff],
                           range_is_power_of_two=True)
    except OSError as e:
        # The plot is a convenience; a read-only import directory must not fail the import.
        prettyoutput.Log(f"Could not plot the histogram at {HistogramImageFullPath}: {e}")


def histogram_cache_sidecar_path(histogram_cache_path: str) -> str:
    """Return path of the JSON sidecar beside ``Histogram.xml`` (``*.cache.json``)."""
    (base, _) = os.path.splitext(histogram_cache_path)
    return f"{base}{HISTOGRAM_CACHE_SIDECAR_SUFFIX}"


def _cache_input_basenames(cache_inputs: Sequence[str]) -> list[str]:
    return sorted(os.path.basename(p) for p in cache_inputs)


def _is_histogram_cache_fresh(histogram_cache_path: str,
                              cache_inputs: Sequence[str],
                              expected_stride: int) -> bool:
    """Return True if XML + sidecar match version, stride, input set, and mtimes."""
    if not os.path.exists(histogram_cache_path):
        return False
    sidecar = histogram_cache_sidecar_path(histogram_cache_path)
    if not os.path.exists(sidecar):
        return False
    try:
        with open(sidecar, 'r', encoding='utf-8') as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False

    if meta.get('version') != HISTOGRAM_CACHE_VERSION:
        return False
    if int(meta.get('stride', -1)) != int(expected_stride):
        return False
    if list(meta.get('inputs', [])) != _cache_input_basenames(cache_inputs):
        return False

    try:
        xml_mtime_ns = files.file_mtime_ns(histogram_cache_path)
    except OSError:
        return False
    input_mtimes_ns: list[int] = []
    for path in cache_inputs:
        try:
            if os.path.exists(path):
                input_mtimes_ns.append(files.file_mtime_ns(path))
        except OSError:
            continue
    if input_mtimes_ns and files.is_significantly_newer(max(input_mtimes_ns), xml_mtime_ns):
        return False
    return True


def _write_histogram_cache_sidecar(histogram_cache_path: str,
                                   cache_inputs: Sequence[str],
                                   stride: int) -> None:
    sidecar = histogram_cache_sidecar_path(histogram_cache_path)
    meta = {
        'version': HISTOGRAM_CACHE_VERSION,
        'stride': int(stride),
        'inputs': _cache_input_basenames(cache_inputs),
    }
    os.makedirs(os.path.dirname(sidecar) or '.', exist_ok=True)
    with open(sidecar, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=0, sort_keys=True)


def _is_legacy_histogram_current(histogram_path: str, cache_inputs: Sequence[str]) -> bool:
    """True for a Histogram.xml written before cache sidecars existed that is still newer than its inputs."""
    if not os.path.exists(histogram_path) or os.path.exists(histogram_cache_sidecar_path(histogram_path)):
        return False
    try:
        xml_mtime_ns = files.file_mtime_ns(histogram_path)
        input_mtimes_ns = [files.file_mtime_ns(p) for p in cache_inputs]
    except OSError:
        return False
    return bool(input_mtimes_ns) and not files.is_significantly_newer(max(input_mtimes_ns), xml_mtime_ns)


def _save_histogram_cache(histogram_obj: Histogram, histogram_cache_path: str, cache_inputs: Sequence[str],
                          stride: int, write_xml: bool = True) -> None:
    """Write the cache XML and sidecar; a read-only or unwritable location only costs the cache."""
    try:
        os.makedirs(os.path.dirname(histogram_cache_path) or '.', exist_ok=True)
        if write_xml:
            histogram_obj.Save(histogram_cache_path)
        _write_histogram_cache_sidecar(histogram_cache_path, cache_inputs, stride)
    except OSError as e:
        prettyoutput.Log(f"Could not cache the histogram at {histogram_cache_path}: {e}")


def ensure_histogram_cache(histogram_cache_path: str,
                           calculate_histogram: Callable[[], Histogram],
                           cache_inputs: Sequence[str],
                           stride: int = 1,
                           legacy_cache_path: str | None = None) -> Histogram:
    """Load a fresh histogram cache or recompute, clean, and save XML + sidecar.

    Freshness requires matching cache version/stride, matching input basenames,
    and XML mtime at least as new as every existing path in *cache_inputs*.

    :param legacy_cache_path: A Histogram.xml from before cache sidecars existed. Used instead of
        recomputing when it has no sidecar and is not older than every input; recomputing a
        section histogram can mean reading tens of GB.
    """
    if _is_histogram_cache_fresh(histogram_cache_path, cache_inputs, stride):
        histogram_obj = Histogram.Load(histogram_cache_path)
        if histogram_obj is not None:
            return histogram_obj

    if legacy_cache_path is not None and _is_legacy_histogram_current(legacy_cache_path, cache_inputs):
        histogram_obj = Histogram.Load(legacy_cache_path)
        if histogram_obj is not None:
            prettyoutput.Log(f"Using histogram {legacy_cache_path}: written before cache sidecars, newer than "
                             f"its input")
            same_file = os.path.normcase(os.path.abspath(legacy_cache_path)) == \
                os.path.normcase(os.path.abspath(histogram_cache_path))
            _save_histogram_cache(histogram_obj, histogram_cache_path, cache_inputs, stride,
                                  write_xml=not same_file)
            return histogram_obj

    histogram_obj = calculate_histogram()
    histogram_obj = CleanOutliersFromHistogram(histogram_obj)
    _save_histogram_cache(histogram_obj, histogram_cache_path, cache_inputs, stride)
    return histogram_obj


def GetSectionContrastSettings(section_number: int,
                               contrast_map: dict[int, ContrastValue],
                               contrast_cutoffs: tuple[float, float],
                               calculate_histogram: Callable[[], Histogram],
                               histogram_cache_path: str,
                               cache_inputs: Sequence[str] | None = None,
                               histogram_stride: int = 1,
                               legacy_histogram_cache_path: str | None = None) -> MinMaxGamma:
    """Resolve section Min/Max/Gamma from overrides and/or AutoLevel on a cached hist.

    Always ensures ``Histogram.xml`` is fresh for plotting. When both Min and Max
    are overridden, AutoLevel is not used for contrast values.

    :param legacy_histogram_cache_path: See ensure_histogram_cache's legacy_cache_path.
    """
    if cache_inputs is None:
        cache_inputs = []

    histogram_obj = ensure_histogram_cache(
        histogram_cache_path=histogram_cache_path,
        calculate_histogram=calculate_histogram,
        cache_inputs=cache_inputs,
        stride=histogram_stride,
        legacy_cache_path=legacy_histogram_cache_path)

    Gamma = 1.0
    override = contrast_map.get(section_number)
    full_override = (
        override is not None
        and override.Min is not None
        and override.Max is not None
    )

    if full_override:
        assert override is not None
        ActualMosaicMin = override.Min
        ActualMosaicMax = override.Max
        if override.Gamma is not None:
            Gamma = override.Gamma
    else:
        (ActualMosaicMin, ActualMosaicMax) = histogram_obj.AutoLevel(
            contrast_cutoffs[0], 1.0 - contrast_cutoffs[1])
        if override is not None:
            if override.Min is not None:
                ActualMosaicMin = override.Min
            if override.Max is not None:
                ActualMosaicMax = override.Max
            if override.Gamma is not None:
                Gamma = override.Gamma

    return MinMaxGamma(min=ActualMosaicMin,
                       max=ActualMosaicMax,
                       gamma=Gamma)


class SectionNoise(NamedTuple):
    sigma: float
    sample_min: float
    sample_max: float
    source_dtype: str
    #: Robust minimum of the sampled tiles (the ``SIGNED_OFFSET_QUANTILE`` quantile), signed sources only. The crush offset
    #: follows it, so one stray negative pixel no longer drags the offset (RC2 139, int32 TIFs: a -15951 outlier made
    #: CrushOffset -15951 and 9-bit codes where 8 bits fit). None in caches written before it existed.
    sample_low: float | None = None


#: Quantile of the sampled pixels used as the minimum of a signed section (1 in 10,000 pixels may be clipped to code 0).
SIGNED_OFFSET_QUANTILE: float = 1e-4


def _noise_cache_path(histogram_cache_path: str) -> str:
    (base, _) = os.path.splitext(histogram_cache_path)
    return f"{base}{NOISE_CACHE_SUFFIX}"


def _load_fresh_noise_cache(noise_cache_path: str, cache_inputs: Sequence[str],
                            gains_checksum: str | None = None) -> SectionNoise | None:
    """Return the cached estimate if its version, inputs and gains match and no input is newer."""
    if not os.path.exists(noise_cache_path):
        return None
    try:
        with open(noise_cache_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        if meta.get('version') != NOISE_CACHE_VERSION or meta.get('method') != noise_crush.SIGMA_METHOD:
            return None
        if list(meta.get('inputs', [])) != _cache_input_basenames(cache_inputs):
            return None
        if meta.get('gains') != gains_checksum:
            return None  # estimated on tiles divided by other gains (or by none)
        cache_mtime_ns = files.file_mtime_ns(noise_cache_path)
        input_mtimes_ns = [files.file_mtime_ns(p) for p in cache_inputs if os.path.exists(p)]
        if input_mtimes_ns and files.is_significantly_newer(max(input_mtimes_ns), cache_mtime_ns):
            return None
        source_dtype = str(meta['source_dtype'])
        sample_low = meta.get('sample_low')
        if sample_low is None and np.issubdtype(np.dtype(source_dtype), np.signedinteger):
            return None  # signed caches from before sample_low: re-estimate, their offset may be an outlier's
        return SectionNoise(float(meta['sigma']), float(meta['sample_min']), float(meta['sample_max']),
                            source_dtype, None if sample_low is None else float(sample_low))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def GetSectionNoise(histogram_cache_path: str,
                    cache_inputs: Sequence[str],
                    num_tiles: int,
                    load_tile: Callable[[int], NDArray],
                    tile_gains_applied: Mapping[int, float] | None = None,
                    gains_checksum: str | None = None) -> SectionNoise:
    """Estimate the section's noise sigma (counts) from evenly sampled tiles, cached beside Histogram.xml.

    The cache holds sigma rather than the step, so changing ``-CrushK`` re-reads no tiles.
    A read-only import directory only costs the cache; the estimate is still returned.

    With ``tile_gains_applied`` (gain-first imports) each sampled tile is measured as it will be quantized, divided by
    its gain: sigma, minimum and maximum are in gain-corrected counts.  ``gains_checksum`` keys the cache to those gains.
    """
    noise_cache_path = _noise_cache_path(histogram_cache_path)
    cached = _load_fresh_noise_cache(noise_cache_path, cache_inputs, gains_checksum)
    if cached is not None:
        return cached

    sigmas: list[float] = []
    sample_min = math.inf
    sample_max = -math.inf
    sample_low = math.inf
    source_dtype = ''
    for iTile in noise_crush.sample_indices(num_tiles, NOISE_SAMPLE_TILES):
        tile = load_tile(iTile)
        source_dtype = tile.dtype.name
        gain = 1.0 if tile_gains_applied is None else tile_gains_applied.get(iTile, 1.0)
        sigmas.append(noise_crush.estimate_noise_sigma(tile) / gain)  # the estimate scales with the counts
        sample_min = min(sample_min, float(np.min(tile)) / gain)
        sample_max = max(sample_max, float(np.max(tile)) / gain)
        if np.issubdtype(tile.dtype, np.signedinteger):
            sample_low = min(sample_low,
                             float(np.quantile(np.asarray(tile)[::4, ::4], SIGNED_OFFSET_QUANTILE)) / gain)
        del tile

    noise = SectionNoise(noise_crush.section_sigma(sigmas), sample_min, sample_max, source_dtype,
                         sample_low if math.isfinite(sample_low) else None)
    meta = {'version': NOISE_CACHE_VERSION, 'method': noise_crush.SIGMA_METHOD,
            'inputs': _cache_input_basenames(cache_inputs), 'sigma': noise.sigma,
            'sample_min': noise.sample_min, 'sample_max': noise.sample_max, 'sample_low': noise.sample_low,
            'source_dtype': source_dtype, 'tile_sigmas': sigmas, 'gains': gains_checksum}
    try:
        os.makedirs(os.path.dirname(noise_cache_path) or '.', exist_ok=True)
        with open(noise_cache_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=0, sort_keys=True)
    except OSError as e:
        prettyoutput.Log(f"Could not cache the noise estimate at {noise_cache_path}: {e}")

    return noise


CAMERA_BPP_IGNORED_WHEN_CRUSHING = (
    "-CameraBpp {bpp} is ignored: it only applies to legacy (-NoCrush) imports. Crushed codes come from the "
    "measured noise and the raw counts, so a camera bit depth is unnecessary when crushing.")


def CropToForImport(crop_to: int | str | None) -> int:
    """The -CropTo value of an import: the square size frames are cropped to, 0 for no cropping.

    :raises ValueError: when the value is negative or not a whole number.
    """
    if crop_to is None or crop_to == '':
        return 0
    size = int(crop_to)
    if size < 0:
        raise ValueError(f"-CropTo must not be negative: {size}")
    return size


def CropBorderForFrame(frame_shape: Sequence[int], crop_to: int) -> int:
    """The border that crops a frame of *frame_shape* to ``crop_to`` pixels square, 0 for a frame already that size.

    Backs ``-CropTo`` for MRC imports, whose frame size varies within a volume (RC1: 4096 frames with an 8 pixel
    artefact border beside 4080 frames that have none).  A frame smaller than the target is left as it is.

    :raises ValueError: when the target is negative, or the frame is not square or differs from the target by an odd
        number of pixels, so no symmetric border exists.
    """
    if crop_to < 0:
        raise ValueError(f"-CropTo must not be negative: {crop_to}")
    if crop_to == 0:
        return 0
    if len(set(int(n) for n in frame_shape)) != 1:
        raise ValueError(f"-CropTo needs a square frame, not {tuple(int(n) for n in frame_shape)}")
    excess = int(frame_shape[0]) - crop_to
    if excess <= 0:
        return 0
    if excess % 2:
        raise ValueError(f"-CropTo {crop_to} cannot be reached symmetrically from a {int(frame_shape[0])} pixel frame")
    return excess // 2


def CameraBppForImport(camera_bpp: int | None, no_crush: bool,
                       logger: logging.Logger | None = None) -> int | None:
    """The -CameraBpp value an import should use: unchanged for -NoCrush, None (with a warning) otherwise.

    CameraBpp clamps the legacy contrast-stretch range. For crushed imports it changes nothing that matters
    (the crush step, offset and sigma never depend on it) but it can nudge the section histogram's top bin and
    with it the crush bit depth, so it is dropped and the user is told it was unnecessary.
    """
    if camera_bpp is None or no_crush:
        return camera_bpp
    message = CAMERA_BPP_IGNORED_WHEN_CRUSHING.format(bpp=camera_bpp)
    prettyoutput.LogErr(message)
    (logger or logging.getLogger(__name__)).warning(message)
    return None


def SectionCrushParams(noise: SectionNoise, histogram: Histogram | None, k: float) -> CrushParams:
    """Crush parameters for a section: the step from its sigma, storage depth from its intensity range.

    The range is the wider of the sampled tiles and the section histogram, whose top
    occupied bin also covers the tiles that were not sampled. Crushed imports no longer build a
    histogram before converting (``histogram`` None): the sample sets the depth, and ``CrushTiles``
    reports any value the codes had to clip. A signed section's offset follows its robust minimum
    (``SectionNoise.sample_low``), not the single lowest sampled pixel.
    """
    section_max = noise.sample_max
    if histogram is not None:
        top_bin = histogram.MaxNonEmptyBin()
        if top_bin is not None:
            section_max = max(section_max, histogram.BinValue(top_bin, 1.0))

    section_min = noise.sample_min if noise.sample_low is None else noise.sample_low
    return noise_crush.make_crush_params(noise.sigma, section_max, noise.source_dtype, k=k,
                                         section_min=section_min)


def CrushedFilterName(params: CrushParams) -> str:
    """Filter that holds crushed tiles: Raw8 normally, Raw16 when the codes need more than 8 bits."""
    return 'Raw8' if params.bits <= 8 else 'Raw16'


def CountsToCrushCodes(value: float, params: CrushParams) -> float:
    """Express a raw-count intensity (contrast cutoff, override) in crushed code units."""
    return (float(value) - params.offset) / params.step


def ResolveCrushMode(channel: ChannelNode, filter_name: str, requested: CrushParams | None,
                     logger: logging.Logger) -> tuple[CrushParams | None, bool]:
    """Decide whether *filter_name* is built crushed, and clean it when its tiles must change.

    :return: (params to build with, or None for the legacy contrast-stretched tiles;
              True if the existing filter was removed)

    An existing filter the importer did not crush is left alone even when crushing is
    requested: re-importing to add sections must not rewrite every imported section
    and everything built from it. Remove the filter to crush an existing section.
    A locked filter keeps whatever it holds.
    """
    filter_node = channel.GetFilter(filter_name)
    if filter_node is None:
        return requested, False

    existing = CrushParams.from_attributes(filter_node.attrib)
    if existing is None:
        if requested is not None:
            logger.info("Keeping existing uncrushed filter %s; remove it to crush this section",
                        filter_node.FullPath)
        return None, False

    if requested is not None and requested.same_quantization(existing):
        return existing, False

    if filter_node.Locked:
        logger.warning("Locked filter keeps its crush parameters (step %d): %s", existing.step,
                       filter_node.FullPath)
        return existing, False

    reason = "crushing turned off" if requested is None else \
        f"crush step {existing.step} -> {requested.step}, offset {existing.offset} -> {requested.offset}, bits {existing.bits} -> {requested.bits}"
    filter_node.Clean(f"Crush mismatch: {reason}")
    return requested, True


def SetFilterCrushAttributes(filter_node: XElementWrapper, params: CrushParams, gain_first: bool = False) -> None:
    """Record *params* on the filter so later runs and readers know its tiles are crushed.

    *gain_first* marks tiles divided by their brightness gain before quantizing: their gains are diagnostic, and
    TileGains leaves the filter alone (its correction would be applied twice).
    """
    for (key, value) in params.to_attributes().items():
        setattr(filter_node, key, value)  # the wrapper marks the node changed, so the pipeline saves it
    if gain_first:
        setattr(filter_node, noise_crush.GAIN_FIRST_ATTRIB, '1')


def IsGainFirstFilter(filter_node: XElementWrapper | None) -> bool:
    """True when the filter's crushed tiles were gain-corrected before they were quantized."""
    return filter_node is not None and filter_node.attrib.get(noise_crush.GAIN_FIRST_ATTRIB) == '1'


def GainFirstAllowed(channel: ChannelNode, requested: bool = True) -> bool:
    """Whether a new crushed import of this channel should correct gains first.

    A channel that already holds a crushed filter imported without it keeps the old kind: re-importing to add sections
    must not make the crush parameters of the finished ones differ (``ResolveCrushMode`` would clean them).
    """
    if not requested:
        return False
    for name in ('Raw8', 'Raw16'):
        existing = channel.GetFilter(name)
        if existing is not None and CrushParams.from_attributes(existing.attrib) is not None \
                and not IsGainFirstFilter(existing):
            return False
    return True


def ExistingGainFirstParams(channel: ChannelNode, k: float | None) -> CrushParams | None:
    """The crush parameters of this channel's gain-first filter when they were made with the same ``k``, else None.

    A restarted import then needs neither the tile gains nor the noise estimate to know its parameters: the gains are
    solved only if tiles still have to be written.
    """
    wanted = noise_crush.DEFAULT_CRUSH_K if k is None else float(k)
    for name in ('Raw8', 'Raw16'):
        existing = channel.GetFilter(name)
        if IsGainFirstFilter(existing):
            params = CrushParams.from_attributes(existing.attrib)
            if params is not None and params.k == wanted:
                return params
    return None


class ImportTileGains(NamedTuple):
    """Brightness gain of each tile of a section, solved from the Stage overlaps before the crush."""
    solved: dict[int, float]
    checksum: str
    residual_mad: float
    pairs_used: int

    def applied(self, tile: int) -> float:
        """The divisor used for *tile*: its solved gain, or 1 for a tile too dim to amplify."""
        gain = self.solved.get(tile, 1.0)
        return gain if gain >= noise_crush.MIN_CORRECTABLE_GAIN else 1.0

    def solved_gain(self, tile: int) -> float:
        """The gain measured for *tile* (1 for a tile that was not measured)."""
        return self.solved.get(tile, 1.0)

    @property
    def applied_gains(self) -> dict[int, float]:
        return {tile: self.applied(tile) for tile in self.solved}

    @property
    def flagged(self) -> list[int]:
        """Tiles with essentially no beam, quantized as captured."""
        return sorted(t for t, g in self.solved.items() if g < noise_crush.MIN_CORRECTABLE_GAIN)


def _gains_cache_path(histogram_cache_path: str) -> str:
    (base, _) = os.path.splitext(histogram_cache_path)
    return f"{base}{GAINS_CACHE_SUFFIX}"


def _load_fresh_gains_cache(gains_cache_path: str, cache_inputs: Sequence[str], variant: str,
                            downsample: int) -> ImportTileGains | None:
    """The cached gains if their inputs, tile geometry (``variant``) and reduction match and no input is newer."""
    try:
        with open(gains_cache_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        if meta.get('version') != GAINS_CACHE_VERSION or meta.get('variant') != variant or \
                meta.get('downsample') != downsample or \
                list(meta.get('inputs', [])) != _cache_input_basenames(cache_inputs):
            return None
        cache_mtime_ns = files.file_mtime_ns(gains_cache_path)
        input_mtimes_ns = [files.file_mtime_ns(p) for p in cache_inputs if os.path.exists(p)]
        if input_mtimes_ns and files.is_significantly_newer(max(input_mtimes_ns), cache_mtime_ns):
            return None
        solved = {int(t): float(g) for t, g in meta['gains'].items()}
        return ImportTileGains(solved, tile_gains.gains_checksum(solved), float(meta['residual_mad']),
                               int(meta['pairs_used']))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
        return None


def SolveImportTileGains(histogram_cache_path: str,
                         cache_inputs: Sequence[str],
                         placements: Mapping[int, tile_gains.TilePlacement],
                         read_tile: Callable[[int], NDArray],
                         variant: str,
                         downsample: int = IMPORT_GAIN_DOWNSAMPLE,
                         section_number: int | None = None) -> ImportTileGains:
    """Solve a section's tile gains from the overlaps of its stage placements, cached beside Histogram.xml.

    ``read_tile(id)`` returns the tile exactly as it will be quantized (cropped and flipped), reduced by ``downsample``
    and in raw counts.  The importer reads every tile of the section anyway to convert it; this is one pass over the
    section before the first tile is quantized, because each tile's codes depend on a gain that needs all the overlaps.
    ``variant`` names whatever else decides the geometry (crop border, flip) so a changed option re-solves.
    """
    gains_cache_path = _gains_cache_path(histogram_cache_path)
    cached = _load_fresh_gains_cache(gains_cache_path, cache_inputs, variant, downsample)
    if cached is not None:
        return cached

    started = time.perf_counter()
    result = tile_gains.compute_tile_gains(placements, read_tile, downsample, low=0.5, high=float('inf'))
    elapsed = time.perf_counter() - started
    solved = {int(t): round(float(g), 6) for t, g in result.gains.items()}
    gains = ImportTileGains(solved, tile_gains.gains_checksum(solved), float(result.residual_mad),
                            int(result.pairs_used))
    values = np.array(list(solved.values())) if solved else np.ones(1)
    label = '' if section_number is None else f"section {section_number}: "
    dim = f", {len(gains.flagged)} tiles too dim to correct" if gains.flagged else ''
    unpaired = f", {len(result.tiles_without_pairs)} without usable overlaps (gain from capture-order ties)"         if result.tiles_without_pairs else ''
    prettyoutput.Log(f"Tile gains {label}{len(solved)} tiles, {result.pairs_used} overlaps "
                     f"({result.pairs_rejected} rejected, {result.pairs_skipped} without tissue), "
                     f"residual MAD {result.residual_mad:.4f}, gain {values.min():.3f}-{values.max():.3f}{dim}{unpaired}, solved in {elapsed:.0f} s")
    meta = {'version': GAINS_CACHE_VERSION, 'variant': variant, 'downsample': downsample,
            'inputs': _cache_input_basenames(cache_inputs), 'gains': {str(t): g for t, g in solved.items()},
            'residual_mad': gains.residual_mad, 'pairs_used': gains.pairs_used,
            'solve_seconds': round(elapsed, 1), 'tiles_without_pairs': sorted(int(t) for t in result.tiles_without_pairs)}
    try:
        os.makedirs(os.path.dirname(gains_cache_path) or '.', exist_ok=True)
        with open(gains_cache_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=0, sort_keys=True)
    except OSError as e:
        prettyoutput.Log(f"Could not cache the tile gains at {gains_cache_path}: {e}")
    return gains


class CrushSectionStats(NamedTuple):
    """Code histogram and clip counts summed over the tiles one ``CrushTiles`` call converted."""
    code_histogram: NDArray
    clipped_high: int
    clipped_low: int
    tiles: int


def CrushTiles(source_to_target: dict[str, str], params: CrushParams, flip: bool,
               progress_name: str | None = None, progress_task_key: str | None = None,
               source_gains: Mapping[str, tuple[float, float]] | None = None) -> CrushSectionStats | None:
    """Crush source tile files into target PNGs on a process pool with a bounded number in flight.

    Returns the code histogram and clip counts of the converted tiles, accumulated as each tile is
    written (James, 2026-11-05: no separate histogram pass for crushed sections), or None when there
    was nothing to convert. Clipped values are logged: they mean the sampled tiles under-estimated the
    section's range.

    ``source_gains`` (gain-first imports) maps a source path to its (applied, solved) gain: the tile is divided by the
    applied gain before it is quantized and records the solved gain in its chunk.

    A failed tile is logged and re-raised, as ``ConvertImagesInDict`` does under
    ``__debug__``; otherwise the caller's ``RemoveInvalidMosaicImages`` drops it.
    """
    if not source_to_target:
        return None

    num_threads = min(len(source_to_target), os.cpu_count() or 1)
    pool = nornir_pools.GetMultithreadingPool("CrushTiles", num_threads=num_threads)
    max_in_flight = num_threads * 2
    reporter = prettyoutput.TaskProgressReporter(progress_task_key or 'CrushTiles', len(source_to_target),
                                                 name=progress_name)
    pending: list = []
    completed = 0
    histogram = np.zeros(params.max_code + 1, dtype=np.int64)
    clipped_high = 0
    clipped_low = 0
    tiles = 0

    def drain_one():
        nonlocal completed, histogram, clipped_high, clipped_low, tiles
        (task, source) = pending.pop(0)
        try:
            stats = task.wait_return()
            if stats is not None:
                histogram += stats.code_histogram
                clipped_high += stats.clipped_high
                clipped_low += stats.clipped_low
                tiles += 1
        except Exception as e:
            prettyoutput.LogErr(f"Failed to crush {source}\n{e}")
            if __debug__:
                raise
        completed += 1
        reporter.update(completed, element=os.path.basename(source), path=source)

    try:
        reporter.start()
        for (source, target) in source_to_target.items():
            gain_args = () if source_gains is None else source_gains.get(source, (1.0, 1.0))
            task = pool.add_task(f"{source} -> {target}", noise_crush.crush_tile_to_file,
                                 source, target, params, flip, *gain_args)
            pending.append((task, source))
            if len(pending) >= max_in_flight:
                drain_one()

        while pending:
            drain_one()
    finally:
        reporter.complete()
        pool.wait_completion()
        pool.shutdown()

    if clipped_high or clipped_low:
        prettyoutput.LogErr(f"Crush clipped {clipped_high} pixels above code {params.max_code} and {clipped_low} below "
                            f"offset {params.offset} in {tiles} tiles: the sampled tiles under-estimated the range")
    return CrushSectionStats(histogram, clipped_high, clipped_low, tiles)


def CrushCodeSpan(code_histogram: NDArray, cutoffs: tuple[float, float]) -> float | None:
    """Span in codes between the ``cutoffs`` of a code histogram, as the import levels a raw histogram.

    ``cutoffs`` are the import's ``ContrastCutoffs`` (-Min, -Max): both are cumulative fractions, (0.0001, 0.9999) by
    default, so -Max is the fraction *below* the high cutoff (levelling trims ``1 - cutoffs[1]`` from the top).
    """
    codes = CrushCodeCutoffs(code_histogram, cutoffs)
    if codes is None:
        return None
    (low, high) = codes
    return float(max(1, high - low + 1))


def CrushCodeCutoffs(code_histogram: NDArray, cutoffs: tuple[float, float]) -> tuple[int, int] | None:
    """(low, high) codes of a code histogram at the import's cumulative ``cutoffs`` fractions, or None when empty.

    low is the first code past the low fraction, high the first code reaching the high fraction (see
    ``CrushCodeSpan``, which is ``high - low + 1``).
    """
    counts = np.asarray(code_histogram, dtype=np.float64)
    total = counts.sum()
    if total <= 0:
        return None
    cumulative = np.cumsum(counts) / total
    low = int(np.searchsorted(cumulative, cutoffs[0], side='right'))  # first code past the low fraction
    high = int(np.searchsorted(cumulative, cutoffs[1], side='left'))  # first code reaching the high fraction
    return low, high


#: The import's default -Min/-Max cumulative fractions, used when display metadata is computed outside an import.
DEFAULT_CONTRAST_CUTOFFS = (0.0001, 0.9999)


def CrushDisplayRange(code_histogram: NDArray, cutoffs: tuple[float, float]) -> tuple[float, float] | None:
    """Display window (min, max) in codes at the ``cutoffs`` fractions; max > min. None for an empty histogram."""
    codes = CrushCodeCutoffs(code_histogram, cutoffs)
    if codes is None:
        return None
    (low, high) = codes
    return float(low), float(max(high, low + 1))


def GetFilterCodeHistogram(filter_node: FilterNode, params: CrushParams) -> NDArray | None:
    """The filter's stored code histogram, or None when missing or not sized for ``params`` (max_code + 1 bins)."""
    counts = filter_node.CodeHistogram
    if counts is None or len(counts) != params.max_code + 1:
        return None
    return counts


def SetFilterCodeHistogram(filter_node: FilterNode, code_histogram: NDArray, cutoffs: tuple[float, float]) -> bool:
    """Store the code histogram on the filter and the display range from it; True when the filter changed.

    The caller yields the filter so the pipeline saves it.
    """
    changed = filter_node.SetCodeHistogram(code_histogram)
    display = CrushDisplayRange(np.asarray(code_histogram), cutoffs)
    if display is not None:
        display = (round(display[0], 3), round(display[1], 3))
    if filter_node.CrushDisplayRange != display:
        filter_node.CrushDisplayRange = display
        changed = True
    return changed


def _tile_code_histogram(path: str, bins: int) -> NDArray:
    """Code histogram of one crushed tile; codes above the top bin (none expected) count in the top bin."""
    with Image.open(path) as im:
        counts = np.bincount(np.asarray(im).ravel(), minlength=bins)
    if len(counts) > bins:
        counts[bins - 1] += counts[bins:].sum()
        counts = counts[:bins]
    return counts.astype(np.int64)


def CodeHistogramFromTiles(tile_dir: str, params: CrushParams, image_ext: str = '.png',
                           sample_every: int = 1) -> NDArray | None:
    """Sum the code histogram of the crushed tiles in ``tile_dir`` (level 1), or None when there are none.

    For sections whose tiles exist but were not converted by this import (nothing to convert, or only some tiles),
    so the histogram cannot come from the conversion. Tiles are decoded on a thread pool with a bounded number in
    flight, so memory holds a few tiles at a time. Cost: one decode of every level-1 tile.

    ``sample_every`` N > 1 reads every Nth tile in name order, for a histogram that is close to the full one (cutoffs at
    0.0001/0.9999 move by a code or two) for 1/N of the reading.
    """
    if not os.path.isdir(tile_dir):
        return None
    suffix = ('' if image_ext.startswith('.') else '.') + image_ext.lower()
    paths = [entry.path for entry in os.scandir(tile_dir)
             if entry.is_file() and entry.name.lower().endswith(suffix)]
    if not paths:
        return None
    if sample_every > 1:
        paths = sorted(paths)[::sample_every]

    bins = params.max_code + 1
    num_threads = min(len(paths), os.cpu_count() or 1)
    pool = nornir_pools.GetThreadPool("CodeHistogram", num_threads=num_threads)  # PNG decode releases the GIL
    max_in_flight = num_threads * 2
    histogram = np.zeros(bins, dtype=np.int64)
    pending: list = []

    def drain_one() -> None:
        nonlocal histogram
        (task, path) = pending.pop(0)
        try:
            histogram += task.wait_return()
        except (OSError, ValueError) as e:  # an unreadable tile is the import's to report; leave it out of the histogram
            prettyoutput.LogErr(f"Code histogram: could not read {path}\n{e}")

    try:
        for path in paths:
            pending.append((pool.add_task(path, _tile_code_histogram, path, bins), path))
            if len(pending) >= max_in_flight:
                drain_one()
        while pending:
            drain_one()
    finally:
        pool.wait_completion()
        pool.shutdown()
    return histogram


def UpdateFilterCodeHistogram(filter_node: FilterNode, params: CrushParams, cutoffs: tuple[float, float],
                              tile_dir: str, converted: CrushSectionStats | None, total_tiles: int,
                              image_ext: str = '.png') -> bool:
    """Keep a crushed filter's ``<CodeHistogram>`` and ``CrushDisplayMin/Max`` current; True when it changed.

    - Every tile converted now: the conversion's histogram (no extra pass).
    - Nothing converted and a histogram of the right size stored: kept; only the display range is refreshed.
    - Otherwise (no histogram yet, or only some tiles converted): read from the level-1 tiles.
    """
    if converted is not None and converted.tiles >= total_tiles > 0:
        histogram = converted.code_histogram
    else:
        histogram = GetFilterCodeHistogram(filter_node, params) if converted is None else None
        if histogram is None:
            histogram = CodeHistogramFromTiles(tile_dir, params, image_ext=image_ext)
    if histogram is None:
        return False
    return SetFilterCodeHistogram(filter_node, histogram, cutoffs)


def CrushCodeSpanFromContrastOverride(override: ContrastValue | None, params: CrushParams) -> float | None:
    """Span in codes from a section's contrast override (ContrastOverrides / histogram cutoffs file), or None.

    A legacy import stretched [Min, Max] counts to 0-255, so the prune scores of that import (and the thresholds tuned
    on them) scale with 255 / (Max - Min). Using the same counts for a crushed section reproduces that scale: on four
    RC2 reimports the legacy-cutoff span put crushed scores at 221-231x the 2015 scores (legacy: about 217x), where the
    0.0001/0.9999 code-histogram span gave 98-209x (2026-11-05). No contrast is applied; only Prune reads the span.
    """
    if override is None or override.Max is None or override.Min is None or override.Max <= override.Min:
        return None
    return (float(override.Max) - float(override.Min)) / float(params.step)


def ClearCrushedFilterContrast(filter_node: FilterNode) -> None:
    """Remove contrast values from a crushed filter (no contrast mapping is applied to crushed codes).

    A filter written before this rule (RC1_Crush, 2026-11) keeps its Prune scale: its cutoff span is
    moved to ``CrushCodeSpan`` first when no span is recorded.
    """
    (min_cutoff, max_cutoff) = (filter_node.MinIntensityCutoff, filter_node.MaxIntensityCutoff)
    if filter_node.CrushCodeSpan is None and min_cutoff is not None and max_cutoff is not None \
            and max_cutoff > min_cutoff:
        filter_node.CrushCodeSpan = max_cutoff - min_cutoff
    filter_node.MinIntensityCutoff = None
    filter_node.MaxIntensityCutoff = None
    filter_node.Gamma = None


if __name__ == "__main__":
    pass
