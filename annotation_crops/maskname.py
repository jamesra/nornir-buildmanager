"""Mask filename ``{image_key}_{location_id}.png``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MaskName:
    """Stem of one AnnotationCrops mask PNG."""

    image_key: str
    location_id: int

    def format(self) -> str:
        """Return ``{image_key}_{location_id}.png``."""
        return f"{self.image_key}_{self.location_id}.png"

    @classmethod
    def parse(cls, name: str) -> MaskName | None:
        """Parse a mask filename. Other names return None.

        The location id is the digit tail after the last underscore. Callers that
        need a stricter image-key pattern keep that check at the call site.
        """
        filename = Path(name).name
        if not filename.endswith(".png"):
            return None
        image_key, separator, tail = filename[:-4].rpartition("_")
        if not separator or not tail.isdigit():
            return None
        return cls(image_key=image_key, location_id=int(tail))
