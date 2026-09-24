"""Process-lifetime index of AnnotationCrops product files for one output tree.

One parallel scandir per folder is reused across sections. Lookups stay in
memory. Individual stat calls are reserved for write-through and the exception path.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PRODUCT_FOLDERS: tuple[str, ...] = (
    "images",
    "masks",
    "overlays",
    "ignored",
    "_work/rle",
)

_MEMBER_FOLDERS = frozenset({"masks", "ignored", "_work/rle"})

_indexes: dict[str, CropProductIndex] = {}
_indexes_lock = threading.Lock()


class CropProductIndex:
    """Basename to mtime maps for the product folders under one output path."""

    output: Path
    _by_folder: dict[str, dict[str, float]]
    _members: dict[str, dict[str, dict[int, str]]]

    def __init__(self, output: str | Path) -> None:
        self.output = Path(output).resolve()
        self._by_folder = {folder: {} for folder in PRODUCT_FOLDERS}
        self._members = {folder: {} for folder in _MEMBER_FOLDERS}
        self._build()

    def exists(self, folder: str, name: str) -> bool:
        """True when *name* was indexed under *folder*."""
        return name in self._by_folder.get(folder, {})

    def mtime(self, folder: str, name: str) -> float | None:
        """Indexed mtime, or None when the name is absent."""
        return self._by_folder.get(folder, {}).get(name)

    def names_with_prefix(self, folder: str, prefix: str) -> list[str]:
        """Indexed basenames in *folder* that start with *prefix*."""
        entries = self._by_folder.get(folder, {})
        return [name for name in entries if name.startswith(prefix)]

    def members(self, folder: str, key: str) -> list[tuple[int, str]]:
        """``(location_id, filename)`` pairs indexed for *key* in *folder*."""
        table = self._members.get(folder, {}).get(key, {})
        return list(table.items())

    def note(self, folder: str, name: str, mtime: float | None = None) -> None:
        """Record *name* after a successful write. Stats the file when *mtime* is omitted."""
        if mtime is None:
            path = self._folder_path(folder) / name
            try:
                if not path.is_file():
                    self.forget(folder, name)
                    return
                mtime = path.stat().st_mtime
            except OSError:
                self.forget(folder, name)
                return
        self._by_folder.setdefault(folder, {})[name] = mtime
        self._register_member(folder, name)

    def note_path(self, path: str | Path) -> None:
        """Write-through for an absolute product path under this output tree."""
        folder, name = self._split_path(Path(path))
        if folder is None:
            return
        self.note(folder, name)

    def forget(self, folder: str, name: str) -> None:
        """Drop *name* after a successful unlink."""
        self._by_folder.get(folder, {}).pop(name, None)
        self._unregister_member(folder, name)

    def forget_path(self, path: str | Path) -> None:
        """Drop an absolute product path after unlink."""
        folder, name = self._split_path(Path(path))
        if folder is None:
            return
        self.forget(folder, name)

    def refresh_one(self, folder: str, name: str) -> float | None:
        """Live stat for the exception path, then update the index."""
        path = self._folder_path(folder) / name
        try:
            if not path.is_file():
                self.forget(folder, name)
                return None
            stamped = path.stat().st_mtime
        except OSError:
            self.forget(folder, name)
            return None
        self.note(folder, name, stamped)
        return stamped

    def rescan(self, folder: str) -> None:
        """Replace one folder's index after ignore moves, not on every section."""
        self._by_folder[folder] = _scan_folder(self._folder_path(folder))
        self._rebuild_members(folder)

    def _build(self) -> None:
        with ThreadPoolExecutor(max_workers=len(PRODUCT_FOLDERS)) as pool:
            futures = {
                pool.submit(_scan_folder, self._folder_path(folder)): folder
                for folder in PRODUCT_FOLDERS
            }
            for future in as_completed(futures):
                folder = futures[future]
                self._by_folder[folder] = future.result()
                self._rebuild_members(folder)

    def _folder_path(self, folder: str) -> Path:
        return self.output.joinpath(*folder.split("/"))

    def _split_path(self, path: Path) -> tuple[str | None, str]:
        try:
            relative = path.resolve().relative_to(self.output)
        except ValueError:
            return None, path.name
        if relative.parent == Path("."):
            return None, relative.name
        return relative.parent.as_posix(), relative.name

    def _rebuild_members(self, folder: str) -> None:
        if folder not in _MEMBER_FOLDERS:
            return
        table: dict[str, dict[int, str]] = {}
        for name in self._by_folder.get(folder, {}):
            key, location_id = _split_member_stem(Path(name).stem)
            if location_id is None:
                continue
            table.setdefault(key, {})[location_id] = name
        self._members[folder] = table

    def _register_member(self, folder: str, name: str) -> None:
        if folder not in _MEMBER_FOLDERS:
            return
        key, location_id = _split_member_stem(Path(name).stem)
        if location_id is None:
            return
        self._members.setdefault(folder, {}).setdefault(key, {})[location_id] = name

    def _unregister_member(self, folder: str, name: str) -> None:
        if folder not in _MEMBER_FOLDERS:
            return
        key, location_id = _split_member_stem(Path(name).stem)
        if location_id is None:
            return
        table = self._members.get(folder, {}).get(key)
        if table is not None:
            table.pop(location_id, None)


def get_product_index(output_path: str | Path) -> CropProductIndex:
    """Return the cached index for *output_path*, building it on first use."""
    key = str(Path(output_path).resolve())
    with _indexes_lock:
        existing = _indexes.get(key)
        if existing is None:
            existing = CropProductIndex(key)
            _indexes[key] = existing
        return existing


def drop_product_index(output_path: str | Path) -> None:
    """Forget a cached index. Tests use this between isolated trees."""
    key = str(Path(output_path).resolve())
    with _indexes_lock:
        _indexes.pop(key, None)


def _scan_folder(folder: Path) -> dict[str, float]:
    """Map file basenames to mtimes. Missing directories scan as empty."""
    found: dict[str, float] = {}
    if not folder.is_dir():
        return found
    with os.scandir(folder) as iterator:
        for entry in iterator:
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                found[entry.name] = entry.stat(follow_symlinks=False).st_mtime
            except OSError:
                continue
    return found


def _split_member_stem(stem: str) -> tuple[str, int | None]:
    if "_" not in stem:
        return stem, None
    key, token = stem.rsplit("_", 1)
    if not token.isdigit():
        return stem, None
    return key, int(token)
