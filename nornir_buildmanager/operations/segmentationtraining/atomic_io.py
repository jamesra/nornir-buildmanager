"""Atomic text writes, and lenient JSON reads, for the export's sidecars and caches.

A reader (the trainer, the gallery, or a later run) sees either the old file or the new one, never a
partial write: the text goes to ``<name>.tmp`` beside the target, then ``os.replace`` swaps it in.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


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


def read_json_object(path: Path) -> dict[str, Any] | None:
    """Return the JSON object stored in ``path`` (utf-8), or ``None`` when there is no usable object.

    A missing or unreadable file (``OSError``), malformed JSON, and a payload that is not an object all
    give ``None``. Text that is not valid utf-8 raises ``UnicodeDecodeError``; it is not swallowed.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def read_manifest_rows(output: Path) -> list[dict[str, Any]]:
    """Return the rows of ``<output>/manifest.jsonl`` (one JSON value per non-blank line, in file order).

    A missing manifest gives ``[]``. A malformed line raises ``json.JSONDecodeError``.
    """
    path = output / "manifest.jsonl"
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_manifest_rows(output: Path, rows: list[dict[str, Any]]) -> None:
    """Atomically replace ``<output>/manifest.jsonl`` with ``rows`` (compact JSON, one row per line)."""
    atomic_write_text(
        output / "manifest.jsonl",
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
    )
