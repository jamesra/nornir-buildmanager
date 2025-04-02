from __future__ import annotations

from xml.etree import ElementTree as ElementTree


class DuplicateElementError(Exception):
    """Exception thrown when a unique element is duplicated"""

    def __init__(self, element, message):
        self.element = element
        super().__init__(message)

    def __str__(self):
        return f"{self.element.FullPath} had a duplicate element\n{self}"


class MissingElementError(Exception):
    """Exception thrown when an expected element is missing"""

    def __init__(self, element, message: str):
        self.element = element
        super().__init__(message)

    def __str__(self):
        return f"Missing element {self.element}\n{self}"
