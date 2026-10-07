'''
Created on Apr 18, 2019

@author: u0490822
'''

import glob
import json
import logging
import os
import re
import shutil
import sys
import datetime
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
)
import nornir_shared.files as files
import nornir_shared.prettyoutput as prettyoutput
from nornir_shared.histogram import Histogram
import nornir_shared.plot as plot


# Bump when histogram cache semantics change (e.g. default stride).
HISTOGRAM_CACHE_VERSION: int = 1
# Sidecar next to Histogram.xml: Histogram.cache.json
HISTOGRAM_CACHE_SIDECAR_SUFFIX: str = '.cache.json'

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
        existing_removed = files.RemoveOutdatedFile(histogram_image_path, image_node.FullPath)
        if data_added or existing_removed:
            shutil.copyfile(histogram_image_path, image_node.FullPath)

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
    ImageRemoved = files.RemoveOutdatedFile(histogramFullPath, HistogramImageFullPath)

    if ImageRemoved or force_recreate or not os.path.exists(HistogramImageFullPath) or files.IsOlderThan(
            HistogramImageFullPath, datetime.date(year=2022, month=10, day=25)):
        #        pool = nornir_pools.GetGlobalMultithreadingPool()
        # pool.add_task(HistogramImageFullPath, plot.Histogram, histogramFullPath, HistogramImageFullPath, Title="Section %d\nRaw Data Pixel Intensity" % (sectionNumber), LinePosList=[minCutoff, maxCutoff])
        os.makedirs(os.path.dirname(HistogramImageFullPath), exist_ok=True)
        plot.Histogram(histogramFullPath, HistogramImageFullPath,
                       Title=f"Section {sectionNumber}\nRaw Data Pixel Intensity", LinePosList=[minCutoff, maxCutoff],
                       range_is_power_of_two=True)


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


def ensure_histogram_cache(histogram_cache_path: str,
                           calculate_histogram: Callable[[], Histogram],
                           cache_inputs: Sequence[str],
                           stride: int = 1) -> Histogram:
    """Load a fresh histogram cache or recompute, clean, and save XML + sidecar.

    Freshness requires matching cache version/stride, matching input basenames,
    and XML mtime at least as new as every existing path in *cache_inputs*.
    """
    if _is_histogram_cache_fresh(histogram_cache_path, cache_inputs, stride):
        histogram_obj = Histogram.Load(histogram_cache_path)
        if histogram_obj is not None:
            return histogram_obj

    histogram_obj = calculate_histogram()
    histogram_obj = CleanOutliersFromHistogram(histogram_obj)
    os.makedirs(os.path.dirname(histogram_cache_path) or '.', exist_ok=True)
    histogram_obj.Save(histogram_cache_path)
    _write_histogram_cache_sidecar(histogram_cache_path, cache_inputs, stride)
    return histogram_obj


def GetSectionContrastSettings(section_number: int,
                               contrast_map: dict[int, ContrastValue],
                               contrast_cutoffs: tuple[float, float],
                               calculate_histogram: Callable[[], Histogram],
                               histogram_cache_path: str,
                               cache_inputs: Sequence[str] | None = None,
                               histogram_stride: int = 1) -> MinMaxGamma:
    """Resolve section Min/Max/Gamma from overrides and/or AutoLevel on a cached hist.

    Always ensures ``Histogram.xml`` is fresh for plotting. When both Min and Max
    are overridden, AutoLevel is not used for contrast values.
    """
    if cache_inputs is None:
        cache_inputs = []

    histogram_obj = ensure_histogram_cache(
        histogram_cache_path=histogram_cache_path,
        calculate_histogram=calculate_histogram,
        cache_inputs=cache_inputs,
        stride=histogram_stride)

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


if __name__ == "__main__":
    pass
