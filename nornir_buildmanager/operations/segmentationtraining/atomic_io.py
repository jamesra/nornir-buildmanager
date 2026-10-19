"""Atomic text writes for the export's sidecars and caches.

A reader (the trainer, the gallery, or a later run) sees either the old file or the new one, never a
partial write: the text goes to ``<name>.tmp`` beside the target, then ``os.replace`` swaps it in.
"""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` (utf-8, platform newlines) to ``path`` through ``<name>.tmp`` and ``os.replace``.

    The temp name is fixed, so two writers of the same path must not run at once.
    """
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def write_text_if_changed(path: Path, text: str) -> bool:
    """Atomically write ``text`` to ``path`` unless the file already holds it; return whether it wrote.

    An identical file is left alone, so its modification time does not change.
    """
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    atomic_write_text(path, text)
    return True
