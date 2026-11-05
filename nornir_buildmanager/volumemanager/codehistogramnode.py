from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from nornir_buildmanager.volumemanager import XElementWrapper


class CodeHistogramNode(XElementWrapper):
    """Full-resolution code histogram of a crushed filter's level-1 tiles, stored as a child of its ``<Filter>``.

    ``Bins`` is ``max_code + 1``; the text holds that many space-separated pixel counts, one per code.
    Display metadata for viewers (Viking): no nornir stage reads it as contrast.
    """

    TAG = 'CodeHistogram'

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = CodeHistogramNode.TAG
        if attrib is None:
            attrib = {}

        super().__init__(tag=tag, attrib=attrib, **extra)

    @classmethod
    def Create(cls, counts: NDArray) -> CodeHistogramNode:
        obj = CodeHistogramNode(attrib={'Bins': str(len(counts))})
        obj.text = CodeHistogramNode.FormatCounts(counts)
        return obj

    @staticmethod
    def FormatCounts(counts: NDArray) -> str:
        """Space-separated integer counts."""
        return ' '.join(str(int(c)) for c in np.asarray(counts).ravel())

    @property
    def Bins(self) -> int:
        return int(self.attrib.get('Bins', '0'))

    @property
    def Counts(self) -> NDArray | None:
        """The counts, or None when the text does not hold ``Bins`` integers."""
        text = self.text or ''
        try:
            counts = np.array(text.split(), dtype=np.int64)
        except ValueError:
            return None
        if len(counts) != self.Bins:
            return None
        return counts

    @property
    def NeedsValidation(self) -> bool:
        return False

    def CleanIfInvalid(self) -> tuple[bool, str]:
        return False, ""
