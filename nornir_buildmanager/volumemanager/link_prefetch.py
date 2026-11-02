"""Load many linked containers at once instead of one per lookup.

Each ``Section``, ``Channel``, ``Filter`` and ``ImageSet`` is a separate
``VolumeData.xml`` that ``find()`` loads the first time a lookup reaches its
``*_Link`` stub, one file at a time. A per-pair stage reaches about six new files
per section, so on the NAS ``AssembleStosOverlays`` spent 58% of its main thread
in those serial reads (srv, RC3 Grid32, 2026-11-02).

:func:`preload_links` reads the stubs matched under many parents on a bounded
thread pool, then swaps each loaded container into the tree on the calling thread.
It handles errors and validation exactly as
``XContainerElementWrapper._replace_links`` does. Later ``find()`` calls see loaded
elements and read nothing. :func:`preload_section_images` walks the
Section, Channel, Filter, mask Filter and ImageSet chain for a set of sections.
"""

from __future__ import annotations

import concurrent.futures
import os
from collections.abc import Iterable, Sequence
from xml.etree import ElementTree

from nornir_shared import prettyoutput

from .elementwrapping import SetElementParent
from .xcontainerelementwrapper import XContainerElementWrapper
from .xelementwrapper import MissingElementError, XElementWrapper
import nornir_pools

DEFAULT_MAX_WORKERS = 16


def _matching_links(parent: XContainerElementWrapper, link_xpath: str) -> list[ElementTree.Element]:
    """``*_Link`` stubs under *parent* matching *link_xpath*, without triggering any load."""
    return [element for element in ElementTree.Element.findall(parent, link_xpath)
            if element.tag.endswith('_Link')]


def preload_links(requests: Iterable[tuple[XContainerElementWrapper, str]],
                  max_workers: int = DEFAULT_MAX_WORKERS) -> list[XElementWrapper]:
    """Load every ``*_Link`` stub matched by each ``(parent, link_xpath)`` request, in parallel.

    *link_xpath* names the stub, e.g. ``"Section_Link[@Number='12']"``. Returns the
    loaded containers that remain in the tree (cleaned or unreadable ones are dropped,
    as on the single-link path).
    """
    jobs: list[tuple[XContainerElementWrapper, ElementTree.Element, str]] = []
    seen: set[int] = set()
    for parent, link_xpath in requests:
        if not isinstance(parent, XContainerElementWrapper):
            continue
        for link in _matching_links(parent, link_xpath):
            if id(link) in seen:
                continue
            seen.add(id(link))
            jobs.append((parent, link, os.path.join(parent.FullPath, link.attrib['Path'])))

    if not jobs:
        return []

    loaded: list[XElementWrapper] = []
    workers = max(1, min(max_workers, len(jobs)))
    with nornir_pools.InstrumentedThreadPoolExecutor('metadata-preload', max_workers=workers,
                                               thread_name_prefix='metadata-preload') as pool:
        futures = [pool.submit(XContainerElementWrapper._load_wrap_link_element, path) for _, _, path in jobs]
        validations: list[tuple[XElementWrapper, concurrent.futures.Future]] = []
        for (parent, link, path), future in zip(jobs, futures):
            try:
                wrapped, element = future.result()
            except (OSError, ElementTree.ParseError) as e:
                parent.remove(link)
                prettyoutput.LogErr(f"Removing link node after error loading linked XML file: {path}\n{e}")
                continue
            except Exception as e:
                # As _replace_links: an unexpected error must not leave a stub the caller thinks loaded.
                prettyoutput.LogErr(f"Unexpected error loading linked XML file: {path}\n{e}")
                raise

            try:
                if wrapped:
                    SetElementParent(element, parent)
                parent._ReplaceChildElementInPlace(old=link, new=element)
            except MissingElementError as e:
                prettyoutput.LogErr(f"Missing element when replacing link: {e}, it may have already been replaced")
                continue

            if element.NeedsValidation:
                validations.append((element, pool.submit(element.IsValid)))
            else:
                loaded.append(element)

        for element, validation in validations:
            is_valid, _ = validation.result()
            if is_valid:
                loaded.append(element)
                continue
            cleaned, _ = element.CleanIfInvalid()
            if not cleaned:
                loaded.append(element)
    return loaded


def _loaded_child(parent: XElementWrapper, tag: str, attrib: str, value: str) -> XElementWrapper | None:
    """An already loaded child, found without triggering a load."""
    for child in ElementTree.Element.findall(parent, f"{tag}[@{attrib}='{value}']"):
        if isinstance(child, XElementWrapper):
            return child
    return None


def preload_section_images(block: XContainerElementWrapper,
                           section_channel_filters: Iterable[tuple[int, str, str]],
                           max_workers: int = DEFAULT_MAX_WORKERS) -> None:
    """Load the Section, Channel, Filter, mask Filter and their ImageSets for each ``(section, channel, filter)``.

    The result is what lookups like ``GetImage`` would load one file at a time. Nothing is
    created: a missing section, channel or filter is simply not loaded.
    """
    wanted = sorted(set(section_channel_filters))
    if not wanted:
        return

    numbers = sorted({number for number, _, _ in wanted})
    preload_links(((block, f"Section_Link[@Number='{number}']") for number in numbers), max_workers)

    channel_requests: list[tuple[XContainerElementWrapper, str]] = []
    for number, channel, _ in wanted:
        section = _loaded_child(block, 'Section', 'Number', str(number))
        if isinstance(section, XContainerElementWrapper):
            channel_requests.append((section, f"Channel_Link[@Name='{channel}']"))
    preload_links(channel_requests, max_workers)

    def channel_of(number: int, channel: str) -> XElementWrapper | None:
        section = _loaded_child(block, 'Section', 'Number', str(number))
        return None if section is None else _loaded_child(section, 'Channel', 'Name', channel)

    filter_requests: list[tuple[XContainerElementWrapper, str]] = []
    for number, channel, filter_name in wanted:
        channel_node = channel_of(number, channel)
        if isinstance(channel_node, XContainerElementWrapper):
            filter_requests.append((channel_node, f"Filter_Link[@Name='{filter_name}']"))
    preload_links(filter_requests, max_workers)

    # Masks are named by the loaded filter, so they are a level of their own.
    filters: list[XElementWrapper] = []
    mask_requests: list[tuple[XContainerElementWrapper, str]] = []
    for number, channel, filter_name in wanted:
        channel_node = channel_of(number, channel)
        if channel_node is None:
            continue
        filter_node = _loaded_child(channel_node, 'Filter', 'Name', filter_name)
        if filter_node is None:
            continue
        filters.append(filter_node)
        mask_name = getattr(filter_node, 'MaskName', None)
        if mask_name and isinstance(channel_node, XContainerElementWrapper):
            mask_requests.append((channel_node, f"Filter_Link[@Name='{mask_name}']"))
    preload_links(mask_requests, max_workers)

    for number, channel, filter_name in wanted:
        channel_node = channel_of(number, channel)
        if channel_node is None:
            continue
        mask_name = None
        filter_node = _loaded_child(channel_node, 'Filter', 'Name', filter_name)
        if filter_node is not None:
            mask_name = getattr(filter_node, 'MaskName', None)
        if mask_name:
            mask_node = _loaded_child(channel_node, 'Filter', 'Name', mask_name)
            if mask_node is not None:
                filters.append(mask_node)
    preload_links(((f, 'ImageSet_Link') for f in _unique(filters) if isinstance(f, XContainerElementWrapper)),
                  max_workers)


def _unique(nodes: Sequence[XElementWrapper]) -> list[XElementWrapper]:
    seen: set[int] = set()
    out: list[XElementWrapper] = []
    for node in nodes:
        if id(node) not in seen:
            seen.add(id(node))
            out.append(node)
    return out
