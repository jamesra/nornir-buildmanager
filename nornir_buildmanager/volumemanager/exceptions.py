from __future__ import annotations

from xml.etree import ElementTree as ElementTree


class DuplicateElementError(Exception):
    """Exception thrown when a unique element is duplicated"""
    element: ElementTree.Element

    def __init__(self, element: ElementTree.Element, message):
        self.element = element
        super().__init__(message)

    def __str__(self):
        return f"{self.element.FullPath} had a duplicate element\n{self}"  # type: ignore[union-attr]


class MissingElementError(Exception):
    """Exception thrown when an expected element is missing"""
    element: ElementTree.Element

    def __init__(self, element: ElementTree.Element, message: str):
        self.element = element
        super().__init__(message)

    def __str__(self):
        return f"Missing element {self.element}\n{self}"


class MissingAttributeError(Exception):
    """Exception thrown when an expected attribute is missing"""

    element: ElementTree.Element
    _attribute: str

    def __init__(self, element: ElementTree.Element, attribute: str, message: str):
        self.element = element
        self._attribute = attribute
        super().__init__(message)

    def __str__(self):
        return f"Missing attribute {self._attribute} on element {self.element}\n{self}"
