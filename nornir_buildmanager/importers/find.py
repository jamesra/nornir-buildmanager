import concurrent.futures
import os
import re
from typing import Generator

import nornir_buildmanager
import nornir_buildmanager.importers.shared as shared
import nornir_shared
from nornir_shared import prettyoutput


def find_section_candidates(ImportPath: str, DesiredSectionList: list[int] | None) -> dict[
    int, list[shared.FilenameMetadata]]:
    """
    This fetches the directories that could contain sections so that when we recurse through the section folders we can
    yield the sections we find right away instead of waiting for all sections to be found first
    :param ImportPath:
    :param extension:
    :param DesiredSectionList:
    :return: A list of DirEntry objects that could contain a valid section for the given section number.  The highest version directory is listed first.  The list is in descending order.
    """

    found_sections = {}  # type : dict[int, list[shared.FilenameMetadata]]

    # Checking for .idocs here handles the case of directly importing the section directory instead of the parent directory
    try:
        root_meta_data = shared.GetSectionInfo(ImportPath)
        found_sections[root_meta_data.number] = [root_meta_data]
    except nornir_buildmanager.NornirUserException:
        # This means we are going to check child directory names only and not this folder for idocs
        root_meta_data = None

    with os.scandir(ImportPath) as scanner:
        for entry in scanner:
            if not entry.is_dir():
                continue

            try:
                meta_data = shared.GetSectionInfo(os.path.join(ImportPath, entry.name))
            except nornir_buildmanager.NornirUserException:
                prettyoutput.LogErr(f"Could not parse required metadata from {entry.name}")
                continue

            # Skip this section if it is not in the desired range
            if DesiredSectionList is not None:
                if meta_data.number not in DesiredSectionList:
                    continue

            if meta_data.number is None:
                prettyoutput.error("Could not parse section number from {0} filename".format(idocFullPath))
            else:
                if meta_data.number in found_sections:
                    found_sections[meta_data.number].append(meta_data)
                else:
                    found_sections[meta_data.number] = [meta_data]

    # Sort the lists by the version number
    for key, candidate_data_list in found_sections.items():
        found_sections[key] = sorted(candidate_data_list, key=lambda entry: entry.version, reverse=True)

    return found_sections


def find_section_directory_metadata(section_meta_data: shared.FilenameMetadata,
                                    matching_pattern: re.Pattern | frozenset[str]) -> tuple[
    shared.FilenameMetadata, list[str]]:
    """
    :param ImportPath:
    :param section_meta_data:
    :param matching_pattern:
    :return: yields all idoc files for a given section directory
    """
    # matching_pattern = nornir_shared.files.ensure_regex_or_set(extension)
    matched_file_list = []
    with os.scandir(section_meta_data.fullpath) as path_scanner:
        for entry in path_scanner:
            if entry.is_file() is False:
                continue

            if nornir_shared.files.check_if_str_matches(entry.name, matching_pattern):
                matched_file_list.append(os.path.join(section_meta_data.fullpath, entry.name))

    return section_meta_data, matched_file_list


def section_directory_metadata_generator(match_names: list[shared.FilenameMetadata],
                                         matching_pattern: re.Pattern | frozenset[str]) -> Generator[
    tuple[shared.FilenameMetadata, list[str]], None, None]:
    """
    :param ImportPath:
    :param match_names:
    :param matching_pattern:
    :return: yields the FileMetadata and .idoc file that should be imported for any given section number
    """

    with concurrent.futures.ThreadPoolExecutor() as executor:
        yield from executor.map(
            lambda section_meta_data: find_section_directory_metadata(section_meta_data, matching_pattern), match_names)
