"""``ignore.json``: a sorted JSON array of location ids."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable


def ignore_path(crops: str | os.PathLike[str]) -> Path:
    """Return ``{AnnotationCrops}/ignore.json``."""
    return Path(crops) / "ignore.json"


def load_ignore_ids(crops: str | os.PathLike[str]) -> set[int]:
    """Location ids in ``ignore.json``.

    A missing file, a non-list, or unreadable JSON is an empty set. A corrupt
    list must not abort a long index.
    """
    path = ignore_path(crops)
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        print(f"index: unreadable ignore list {path}")
        return set()
    if not isinstance(payload, list):
        return set()
    ids: set[int] = set()
    for item in payload:
        try:
            ids.add(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def save_ignore_ids(crops: str | os.PathLike[str], ids: Iterable[int]) -> None:
    """Write ``ignore.json`` as a sorted JSON array of ints."""
    ordered = sorted({int(item) for item in ids})
    ignore_path(crops).write_text(json.dumps(ordered), encoding="utf-8")
