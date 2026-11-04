"""Per-section pixel-spacing corrections for StosBrute's scale estimate (``-PixelSpacingOverrides``).

Some cameras record the wrong pixel size: on RC3 the Gatan US4000 sections are recorded at
2.176 nm/px but register at about 2.29 against the OneView sections. StosBrute pre-scales the
mapped image by the Mapped/Control pixel-size ratio, so a wrong pixel size puts the true
scale outside its search band. An overrides file corrects the pixel size StosBrute uses
without changing the volume metadata.

File format, one entry per line; ``#`` starts a comment, blank lines are ignored::

    # section or range <whitespace> value
    259         6.528      units per pixel, in the units of the volume's Scale metadata
    2-150       x1.038     factor applied to the section's metadata pixel size

When several lines cover a section, the last one wins.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

_LINE = re.compile(r'^(?P<first>\d+)(?:\s*-\s*(?P<last>\d+))?\s+(?P<factor>[xX*])?(?P<value>\S+)$')


@dataclass(frozen=True)
class PixelSpacingOverride:
    """One line of an overrides file: an absolute pixel size or a factor for a section range."""

    first: int
    last: int
    value: float
    is_factor: bool
    line_number: int

    def apply(self, units_per_pixel: float) -> float:
        """Return the corrected pixel size for a section whose metadata says *units_per_pixel*."""
        return units_per_pixel * self.value if self.is_factor else self.value

    def describe(self) -> str:
        """Short form for logs, e.g. ``2-150 x1.038 (line 3)``."""
        sections = str(self.first) if self.first == self.last else f'{self.first}-{self.last}'
        value = f'x{self.value:g}' if self.is_factor else f'{self.value:g}'
        return f'{sections} {value} (line {self.line_number})'


class PixelSpacingOverrides:
    """Parsed overrides file; :meth:`lookup` finds the entry for a section."""

    source: str
    entries: tuple[PixelSpacingOverride, ...]

    def __init__(self, entries: list[PixelSpacingOverride], source: str) -> None:
        self.entries = tuple(entries)
        self.source = source

    @classmethod
    def parse(cls, text: str, source: str = '<text>') -> PixelSpacingOverrides:
        """Parse overrides text; raises ValueError naming the line of any malformed entry."""
        entries: list[PixelSpacingOverride] = []
        for line_number, raw in enumerate(text.splitlines(), start=1):
            line = raw.split('#', 1)[0].strip()
            if not line:
                continue
            match = _LINE.match(line)
            if match is None:
                raise ValueError(f'{source} line {line_number}: expected "<section>[-<last>] <value>" '
                                 f'or "<section>[-<last>] x<factor>", got {raw.strip()!r}')
            first = int(match['first'])
            last = int(match['last']) if match['last'] is not None else first
            try:
                value = float(match['value'])
            except ValueError:
                raise ValueError(f'{source} line {line_number}: {match["value"]!r} is not a number') from None
            if last < first:
                raise ValueError(f'{source} line {line_number}: range {first}-{last} is reversed')
            if not value > 0:
                raise ValueError(f'{source} line {line_number}: value must be positive, got {value:g}')
            entries.append(PixelSpacingOverride(first, last, value, match['factor'] is not None, line_number))
        return cls(entries, source)

    @classmethod
    def load(cls, path: str) -> PixelSpacingOverrides:
        """Read and parse an overrides file."""
        with open(path, encoding='utf-8') as f:
            return cls.parse(f.read(), source=path)

    def lookup(self, section_number: int) -> PixelSpacingOverride | None:
        """Return the last entry covering *section_number*, or None."""
        for entry in reversed(self.entries):
            if entry.first <= section_number <= entry.last:
                return entry
        return None

    def __len__(self) -> int:
        return len(self.entries)


def load_pixel_spacing_overrides(path: str | None) -> PixelSpacingOverrides | None:
    """Pipeline argument -> overrides; None when the argument is unset or empty."""
    if path is None or not str(path).strip() or str(path) == 'None':
        return None
    path = str(path).strip()
    if not os.path.isfile(path):
        raise ValueError(f'-PixelSpacingOverrides file not found: {path}')
    return PixelSpacingOverrides.load(path)
