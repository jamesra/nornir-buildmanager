"""Shared STOS input-image freshness checks (brute and grid refine)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ImageCheckSnapshot:
    """Filesystem/checksum facts for one STOS input image (or mask).

    ``stored_checksum`` is the transform attribute (filesize string).
    ``image_checksum`` is the Image node metadata value when present.
    Scan decisions always re-stat ``image_path`` rather than trusting these alone.
    """

    stored_checksum: str
    image_checksum: str | None
    image_path: str | None


def is_stos_input_image_outdated(check: ImageCheckSnapshot, output_stos_path: str) -> bool:
    """Return whether one input image/mask makes a STOS product stale.

    Always ``stat``s the image when a path is available. Outdated when the live
    filesize disagrees with the stored transform or Image-node checksum, or when
    the image mtime is newer than the output ``.stos``.
    """
    if check.image_path is None and check.image_checksum is None:
        return True

    if not check.image_path or not os.path.exists(check.image_path):
        return True

    try:
        image_stat = os.stat(check.image_path)
        output_mtime = os.path.getmtime(output_stos_path)
    except OSError:
        return True

    live_size = str(image_stat.st_size)

    if len(check.stored_checksum) > 0 and live_size != check.stored_checksum:
        return True

    if check.image_checksum is not None and live_size != str(check.image_checksum):
        return True

    if image_stat.st_mtime > output_mtime:
        return True

    return False
