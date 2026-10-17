"""Where ExportTrainingData keeps its re-run cache, away from the exported data.

The spills, watermarks, RLE sidecars and fetched tiles under the work folder let ``-Update``, ``-Repair``
and ``-Cleanup`` skip work, but they are not part of the training set. They live next to ``-Output``, not in
it, so the output folder holds only what the trainer reads and can be copied into a set unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path

WORK_CACHE_FOLDER = "_work_cache"
"""Sibling of the output folder that holds one work folder per output. The leading underscore marks it as
not a volume, so tools that enumerate a set's volume folders can skip it."""

RLE_FOLDER = "rle"


def work_root(output_path: str | os.PathLike[str]) -> Path:
    """Return the work folder for one ``-Output`` folder, without creating it.

    It is ``../_work_cache/<output folder name>`` relative to the output, so ``…/SEM/v1/CPED`` caches in
    ``…/SEM/v1/_work_cache/CPED``. The path is a pure function of the output path, so worker processes and
    later runs find the same cache.
    """
    resolved = Path(output_path).resolve()
    return resolved.parent / WORK_CACHE_FOLDER / resolved.name


def rle_dir(output_path: str | os.PathLike[str]) -> Path:
    """Return the folder of per-location RLE sidecars, without creating it."""
    return work_root(output_path) / RLE_FOLDER
