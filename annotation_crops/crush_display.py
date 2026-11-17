"""Display contrast for quantized ("crushed") tiles: a port of Viking's ``CrushInfo`` window and gamma rules.

This is the one implementation every reader of tile images for user display shares: the gallery converter and score
(``annotation-gallery/converter/tilesource.py``) and Nornir's training-crop export (``stitch.disk_tile_loader``), so a
crop, a card and Viking all show the same gray for the same code. It lives in ``annotation_crops``, which both the
gallery image and Nornir already import. Readers of already-exported crops (thumbnails, masks, the viewer) need nothing:
the export has written display gray.

Nornir's ``Quantized`` filter stores a small integer code per pixel (``floor(counts / step)``), not a contrast-stretched
intensity, so the raw PNG looks nearly black. Viking shows it through a display window (black code .. white code) and a
gamma; both come from the section's code histogram unless the user saves their own. This module reproduces Viking's
*default* (Auto) window and gamma exactly, so the gallery's cards and the segmentation tiles match what an annotator sees
in Viking. Customising the contrast is deliberately not offered yet: :func:`display_lut` takes only the section's
:class:`CrushInfo`, and a later per-section override would pass a window and gamma in its place.

The rules mirror ``Viking-Net4.8/Clients/VolumeModel/CrushInfo.cs`` (``AutoRange``, ``AutoGamma``, ``DefaultWindow``,
``DefaultGamma``) and ``TileLayout.fx`` (``shown = clamp((v - min) / (max - min)) ^ (1 / gamma)``). Keep them in step.

Per-tile gain (the ``nornir_crush`` PNG chunk) is not applied: assembled tilesets carry no per-tile chunk, and a
gain-first import already divided the tiles by their gain before quantizing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple
from xml.etree import ElementTree

import numpy as np

# The 8-bit PNGs Viking decodes hold a code up to 255; deeper codes are left as stored (Viking does the same).
STORAGE_MAXIMUM = 255.0
MAX_BITS = 8


def _attr_ci(attributes: Mapping[str, str], name: str) -> Optional[str]:
    lowered = name.lower()
    for key, value in attributes.items():
        if str(key).lower() == lowered:
            return value
    return None


def _float(attributes: Mapping[str, str], name: str) -> Optional[float]:
    value = _attr_ci(attributes, name)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class CrushInfo:
    """The ``Crush*`` attributes of a Tileset element, plus its optional ``CodeHistogram`` child."""

    step: int
    offset: int
    bits: int
    display_range: Optional[Tuple[float, float]] = None
    histogram: Optional[Tuple[int, ...]] = None

    @property
    def is_supported(self) -> bool:
        """Only 8-bit code PNGs are decoded; wider codes are shown as stored."""
        return self.bits <= MAX_BITS

    @staticmethod
    def from_attributes(
        attributes: Mapping[str, str], histogram: Optional[Sequence[int]] = None
    ) -> Optional["CrushInfo"]:
        """Crush parameters from ``Crush*`` attributes (VikingXML tileset or Nornir filter), or None when the element is
        not marked crushed or its attributes are unusable. ``histogram`` is the per-code pixel count, if known."""
        if _attr_ci(attributes, "CrushVersion") is None:
            return None
        step, bits = _float(attributes, "CrushStep"), _float(attributes, "CrushBits")
        if step is None or bits is None or int(step) < 1 or int(bits) < 1:
            return None
        offset = _float(attributes, "CrushOffset")
        low, high = _float(attributes, "CrushDisplayMin"), _float(attributes, "CrushDisplayMax")
        display = (low, high) if low is not None and high is not None and high > low else None
        counts = tuple(int(v) for v in histogram) if histogram is not None and len(histogram) else None
        return CrushInfo(int(step), int(offset or 0), int(bits), display, counts)

    @staticmethod
    def from_element(element: ElementTree.Element) -> Optional["CrushInfo"]:
        """:meth:`from_attributes` for a VikingXML element, reading its optional ``CodeHistogram`` child."""
        histogram = None
        for child in element:
            if child.tag.split("}")[-1].lower() == "codehistogram":
                try:
                    histogram = [int(part) for part in (child.text or "").split()]
                except ValueError:
                    histogram = None
                break
        return CrushInfo.from_attributes(element.attrib, histogram)

    def _histogram_range(self, low_fraction: float, high_fraction: float) -> Optional[Tuple[float, float]]:
        """The codes holding the given fractions of the pixels, skipping bin 0 (the empty section edge)."""
        counts = self.histogram
        if counts is None or len(counts) < 2:
            return None
        total = sum(counts[1:])
        if total <= 0:
            return None
        low, high, running = -1.0, float(len(counts) - 1), 0
        for i in range(1, len(counts)):
            running += counts[i]
            if low < 0 and running >= total * low_fraction:
                low = float(i)
            if running >= total * high_fraction:
                high = float(i)
                break
        return (low, high) if high > low else None

    def auto_range(self) -> Tuple[float, float]:
        """Clip the darkest and brightest 0.1% of pixels; else Nornir's display range; else every code."""
        return self._histogram_range(0.001, 0.999) or self.display_range or (0.0, float((1 << min(self.bits, 16)) - 1))

    def auto_gamma(self, black: float, white: float) -> float:
        """The gamma that shows the window's median pixel at mid grey (1 without a usable histogram)."""
        counts = self.histogram
        if counts is None or not white > black:
            return 1.0
        inside = sum(counts[i] for i in range(1, len(counts)) if black <= i <= white)
        if inside <= 0:
            return 1.0
        running, median = 0, 1
        for i in range(1, len(counts)):
            if i < black or i > white:
                continue
            running += counts[i]
            if running * 2 >= inside:
                median = i
                break
        # Keep the median strictly inside the window so the logarithm is finite.
        t = min(max((median - black) / (white - black), 0.02), 0.98)
        return min(max(math.log(t) / math.log(0.5), 0.25), 4.0)

    def default_window(self) -> Tuple[float, float]:
        """The Auto window in PNG sample units (0 to 1)."""
        low, high = self.auto_range()
        return low / STORAGE_MAXIMUM, high / STORAGE_MAXIMUM

    def default_gamma(self) -> float:
        low, high = self.auto_range()
        return self.auto_gamma(round(low), round(high))


def display_lut(
    crush: CrushInfo, window: Optional[Tuple[float, float]] = None, gamma: Optional[float] = None
) -> np.ndarray:
    """A 256-entry uint8 table mapping a stored code to its displayed gray, with Viking's window and gamma.

    ``window`` (in 0..1 sample units) and ``gamma`` default to the section's Auto values; they are the hook for a later
    per-section contrast override. Codes are never negative, so code 0 stays black unless the window starts above it.
    """
    low, high = window if window is not None else crush.default_window()
    gamma = crush.default_gamma() if gamma is None else gamma
    gamma = min(max(gamma, 0.25), 4.0)
    samples = np.arange(256, dtype=np.float64) / STORAGE_MAXIMUM
    windowed = np.clip((samples - low) / max(high - low, 1e-6), 0.0, 1.0)
    shown = windowed ** (1.0 / max(gamma, 0.01))
    return np.rint(shown * 255.0).astype(np.uint8)


def apply_display(codes: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Map an 8-bit code image through the display table."""
    return lut[codes]

