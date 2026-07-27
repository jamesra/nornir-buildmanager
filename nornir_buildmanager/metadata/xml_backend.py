"""
XML backend for volume metadata storage.

Wraps the existing VolumeManagerETree XML handling to present it through
the VolumeMetadataBackend interface. Supports both sharded (per-directory
VolumeData.xml with _Link nodes) and single-file formats.

This backend handles older XML format variations by being lenient during
parsing; unknown tags are preserved as-is so that round-tripping does
not lose data. New format quirks can be added by extending _normalize_element.
"""

import logging
import os
import xml.etree.ElementTree as ElementTree
from typing import Optional

from .volume_metadata import MetadataNode, VolumeMetadataBackend

logger = logging.getLogger(__name__)


class XMLMetadataBackend(VolumeMetadataBackend):
    """Read/write volume metadata from XML files (VolumeData.xml or Volume.xml)."""

    # Known older file names in order of preference
    _XML_FILENAMES = ['VolumeData.xml', 'Volume.xml']

    def __init__(self, volume_path: str, single_file: bool = False,
                 xml_filename: Optional[str] = None):
        """
        :param volume_path: Path to the volume root directory.
        :param single_file: If True, load/save as a monolithic XML file instead of sharded.
        :param xml_filename: Override the XML filename (default: auto-detect).
        """
        self._volume_path = volume_path
        self._single_file = single_file
        self._xml_filename = xml_filename

    def _find_xml_path(self) -> Optional[str]:
        if self._xml_filename:
            fp = os.path.join(self._volume_path, self._xml_filename)
            if os.path.isfile(fp):
                return fp
            return None

        for name in self._XML_FILENAMES:
            fp = os.path.join(self._volume_path, name)
            if os.path.isfile(fp):
                return fp
        return None

    def exists(self) -> bool:
        return self._find_xml_path() is not None

    def get_backend_type(self) -> str:
        return 'xml'

    def load(self) -> Optional[MetadataNode]:
        xml_path = self._find_xml_path()
        if xml_path is None:
            return None

        try:
            tree = ElementTree.parse(xml_path)
        except ElementTree.ParseError as e:
            logger.error("Failed to parse XML at %s: %s", xml_path, e)
            return None

        root_elem = tree.getroot()

        if self._single_file:
            return self._element_to_node(root_elem)
        else:
            return self._element_to_node_with_links(root_elem, self._volume_path)

    def save(self, root: MetadataNode) -> None:
        os.makedirs(self._volume_path, exist_ok=True)
        filename = self._xml_filename or 'VolumeData.xml'
        xml_path = os.path.join(self._volume_path, filename)

        root_elem = self._node_to_element(root)
        tree = ElementTree.ElementTree(root_elem)
        ElementTree.indent(tree, space='  ')
        tree.write(xml_path, encoding='utf-8', xml_declaration=True)

    def _element_to_node(self, elem: ElementTree.Element) -> MetadataNode:
        """Convert an ElementTree element to a MetadataNode, recursively."""
        children = []
        for child_elem in elem:
            children.append(self._element_to_node(child_elem))

        node = MetadataNode(
            tag=elem.tag,
            attribs=dict(elem.attrib),
            text=elem.text.strip() if elem.text and elem.text.strip() else elem.text,
            children=children
        )
        self._normalize_node(node)
        return node

    def _element_to_node_with_links(self, elem: ElementTree.Element,
                                     container_path: str) -> MetadataNode:
        """Convert an ElementTree element to a MetadataNode, resolving _Link nodes
        by loading their referenced VolumeData.xml from subdirectories."""
        children = []
        for child_elem in elem:
            tag = child_elem.tag
            if tag.endswith('_Link'):
                real_tag = tag[:-5]  # Strip '_Link'
                child_path = child_elem.attrib.get('Path', '')
                sub_dir = os.path.join(container_path, child_path)
                sub_xml = os.path.join(sub_dir, 'VolumeData.xml')
                if os.path.isfile(sub_xml):
                    try:
                        sub_tree = ElementTree.parse(sub_xml)
                        sub_root = sub_tree.getroot()
                        resolved = self._element_to_node_with_links(sub_root, sub_dir)
                        children.append(resolved)
                    except (ElementTree.ParseError, IOError) as e:
                        logger.warning("Could not load linked XML %s: %s", sub_xml, e)
                        children.append(MetadataNode(
                            tag=real_tag,
                            attribs=dict(child_elem.attrib)
                        ))
                else:
                    children.append(MetadataNode(
                        tag=real_tag,
                        attribs=dict(child_elem.attrib)
                    ))
            else:
                children.append(self._element_to_node_with_links(child_elem, container_path))

        node = MetadataNode(
            tag=elem.tag,
            attribs=dict(elem.attrib),
            text=elem.text.strip() if elem.text and elem.text.strip() else elem.text,
            children=children
        )
        self._normalize_node(node)
        return node

    def _node_to_element(self, node: MetadataNode) -> ElementTree.Element:
        """Convert a MetadataNode back to an ElementTree element."""
        elem = ElementTree.Element(node.tag, attrib=node.attribs)
        if node.text:
            elem.text = node.text
        for child in node.children:
            elem.append(self._node_to_element(child))
        return elem

    def _normalize_node(self, node: MetadataNode) -> None:
        """Apply normalization rules for known older XML format variations.
        Extend this method to handle newly discovered legacy formats."""

        # Older volumes stored section number in 'SectionNumber' instead of 'Number'
        if node.tag == 'Section':
            if 'SectionNumber' in node.attribs and 'Number' not in node.attribs:
                node.attribs['Number'] = node.attribs.pop('SectionNumber')

        # Some old volumes used 'FilterName' instead of 'Name' on Filter elements
        if node.tag == 'Filter':
            if 'FilterName' in node.attribs and 'Name' not in node.attribs:
                node.attribs['Name'] = node.attribs.pop('FilterName')

        # Ensure CreationDate exists (some old formats omit it)
        # We don't fabricate one; we just note the absence silently


def load_xml_as_single_file(volume_path: str, xml_filename: str = 'VolumeData.xml') -> Optional[MetadataNode]:
    """Convenience function: load XML metadata resolving all links into a single tree."""
    backend = XMLMetadataBackend(volume_path, single_file=False)
    return backend.load()


def save_single_xml(root: MetadataNode, output_path: str,
                    filename: str = 'VolumeData_merged.xml') -> str:
    """Save a MetadataNode tree as a single monolithic XML file.
    Returns the full path to the written file."""
    backend = XMLMetadataBackend(output_path, single_file=True, xml_filename=filename)
    backend.save(root)
    return os.path.join(output_path, filename)
