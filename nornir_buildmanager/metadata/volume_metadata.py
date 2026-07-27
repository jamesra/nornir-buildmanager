"""
Abstract interface for volume metadata storage backends.

Volume metadata follows the hierarchy:
  Volume -> Block -> Section -> Channel -> Filter -> {TilePyramid, ImageSet, Tileset, Histogram}
  Block also contains StosGroup and StosMap entries for slice-to-slice registration.

Each node has arbitrary string attributes stored as key-value pairs. The tree structure
supports both XML (legacy, sharded or single-file) and SQLite backends.
"""

import abc
from typing import Optional, List, Dict, Any, Tuple


class MetadataNode:
    """Represents a single node in the metadata tree, independent of backend."""

    __slots__ = ('tag', 'attribs', 'text', 'children', '_db_id')

    def __init__(self, tag: str, attribs: Optional[Dict[str, str]] = None,
                 text: Optional[str] = None, children: Optional[List['MetadataNode']] = None,
                 db_id: Optional[int] = None):
        self.tag = tag
        self.attribs = dict(attribs) if attribs else {}
        self.text = text
        self.children = list(children) if children else []
        self._db_id = db_id

    def get(self, attrib_name: str, default: Optional[str] = None) -> Optional[str]:
        return self.attribs.get(attrib_name, default)

    def find_children(self, tag: str) -> List['MetadataNode']:
        return [c for c in self.children if c.tag == tag]

    def find_child(self, tag: str, attrib_name: Optional[str] = None,
                   attrib_value: Optional[str] = None) -> Optional['MetadataNode']:
        for c in self.children:
            if c.tag != tag:
                continue
            if attrib_name is not None:
                if c.attribs.get(attrib_name) != attrib_value:
                    continue
            return c
        return None

    def walk(self):
        """Depth-first iterator yielding (parent, node) tuples. Root yields (None, self)."""
        yield (None, self)
        for child in self.children:
            yield (self, child)
            for pair in child._walk_children():
                yield pair

    def _walk_children(self):
        for child in self.children:
            yield (self, child)
            for pair in child._walk_children():
                yield pair

    def __repr__(self):
        attrib_str = ' '.join(f'{k}="{v}"' for k, v in sorted(self.attribs.items())[:4])
        return f"<MetadataNode {self.tag} {attrib_str}>"


class VolumeMetadataBackend(abc.ABC):
    """Abstract backend for reading and writing volume metadata."""

    @abc.abstractmethod
    def load(self) -> Optional[MetadataNode]:
        """Load the complete metadata tree and return the root MetadataNode (Volume).
        Returns None if no metadata exists."""

    @abc.abstractmethod
    def save(self, root: MetadataNode) -> None:
        """Persist the complete metadata tree rooted at the given node."""

    @abc.abstractmethod
    def exists(self) -> bool:
        """Return True if metadata storage already exists at the configured location."""

    @abc.abstractmethod
    def get_backend_type(self) -> str:
        """Return a string identifying the backend type, e.g. 'xml' or 'sqlite'."""

    def get_schema_version(self) -> int:
        """Return the schema version of the backend storage. Override in subclasses."""
        return 1
