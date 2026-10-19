"""Lenient JSON sidecar and cache reads in segmentationtraining.

Each reader treats a missing, unreadable, malformed, or non-object file as absent. A file that is
not valid utf-8 is not swallowed: ``UnicodeDecodeError`` still propagates, as it always has.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nornir_buildmanager.operations.segmentationtraining import ingest
from nornir_buildmanager.operations.segmentationtraining.atomic_io import (
    read_json_object,
)
from nornir_buildmanager.operations.segmentationtraining.circles import (
    _load_inscribed_cache,
)
from nornir_buildmanager.operations.segmentationtraining.remote_tiles import (
    INDEX_NAME,
    VOLUME_STATE_NAME,
    RemoteSession,
    RemoteTileSource,
    _LevelState,
)
from nornir_buildmanager.operations.segmentationtraining.shapes import (
    LINKS_CACHE_VERSION,
    _load_links_cache,
)

#: File contents every reader must treat as "no usable file" (``None`` means the file is absent).
UNUSABLE: dict[str, bytes | None] = {
    "missing": None,
    "malformed": b"{not json",
    "list": b"[1, 2]",
    "string": b'"text"',
    "number": b"3",
    "null": b"null",
}
NOT_UTF8 = b'{"a": "\xff"}'


def _put(path: Path, content: bytes | None) -> None:
    if content is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _dump(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")


# --------------------------------------------------------------------------- ingest


@pytest.mark.parametrize("content", list(UNUSABLE.values()) + [b"{}"], ids=list(UNUSABLE) + ["empty"])
def test_load_cache_meta_unusable_is_empty(tmp_path: Path, content: bytes | None) -> None:
    out = tmp_path / "out"
    _put(ingest.cache_meta_path(out), content)
    assert ingest.load_cache_meta(out) == {}


def test_load_cache_meta_reads_object(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _put(ingest.cache_meta_path(out), _dump({"sections": [3, 1]}))
    assert ingest.load_cache_meta(out) == {"sections": [3, 1]}
    assert ingest.load_cache_meta_sections(out) == [3, 1]


@pytest.mark.parametrize("content", list(UNUSABLE.values()) + [b"{}"], ids=list(UNUSABLE) + ["empty"])
def test_load_export_source_unusable_falls_back_to_cache_meta(tmp_path: Path, content: bytes | None) -> None:
    out = tmp_path / "out"
    _put(ingest.export_source_path(out), content)
    assert ingest.load_export_source(out) == {}
    _put(ingest.cache_meta_path(out), _dump({"source": {"kind": "odata"}, "filter": "f"}))
    assert ingest.load_export_source(out)["filter"] == "f"


def test_load_export_source_reads_object(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _put(ingest.export_source_path(out), _dump({"kind": "odata", "exporter": "tiled"}))
    assert ingest.load_export_source(out) == {"kind": "odata", "exporter": "tiled"}


@pytest.mark.parametrize("content", list(UNUSABLE.values()) + [b"{}"], ids=list(UNUSABLE) + ["empty"])
def test_write_export_source_ignores_unusable_existing(tmp_path: Path, content: bytes | None) -> None:
    out = tmp_path / "out"
    _put(ingest.export_source_path(out), content)
    ingest.write_export_source(out, {"source": {"kind": "sql"}})
    written = json.loads(ingest.export_source_path(out).read_text(encoding="utf-8"))
    assert "exporter" not in written
    assert "odata" not in written


def test_write_export_source_keeps_prior_exporter_and_odata(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _put(ingest.export_source_path(out), _dump({"exporter": "Tiled", "odata": " http://host/odata "}))
    ingest.write_export_source(out, {"source": {"kind": "sql"}})
    written = json.loads(ingest.export_source_path(out).read_text(encoding="utf-8"))
    assert written["exporter"] == "tiled"
    assert written["odata"] == "http://host/odata"


# --------------------------------------------------------------------------- circles / shapes


@pytest.mark.parametrize("content", list(UNUSABLE.values()) + [b"{}"], ids=list(UNUSABLE) + ["empty"])
def test_inscribed_cache_unusable_is_empty(tmp_path: Path, content: bytes | None) -> None:
    path = tmp_path / "inscribed.json"
    _put(path, content)
    assert _load_inscribed_cache(path) == {}


def test_inscribed_cache_reads_items(tmp_path: Path) -> None:
    path = tmp_path / "inscribed.json"
    _put(path, _dump({"version": 1, "items": {"k": [1, 2, 3]}}))
    assert _load_inscribed_cache(path) == {"k": [1, 2, 3]}
    _put(path, _dump({"version": 0, "items": {"k": [1, 2, 3]}}))
    assert _load_inscribed_cache(path) == {}


def _links(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"version": LINKS_CACHE_VERSION, "ids": "d", "root": "r", "links": [[1, 2], [3, 4]]}
    payload.update(changes)
    return payload


@pytest.mark.parametrize("content", list(UNUSABLE.values()) + [b"{}"], ids=list(UNUSABLE) + ["empty"])
def test_links_cache_unusable_is_none(tmp_path: Path, content: bytes | None) -> None:
    path = tmp_path / "links.json"
    _put(path, content)
    assert _load_links_cache(path, "d", "r") is None


def test_links_cache_reads_pairs_when_keys_match(tmp_path: Path) -> None:
    path = tmp_path / "links.json"
    _put(path, _dump(_links()))
    assert _load_links_cache(path, "d", "r") == [(1, 2), (3, 4)]
    assert _load_links_cache(path, "other", "r") is None
    assert _load_links_cache(path, "d", "other") is None


# --------------------------------------------------------------------------- remote tiles


def _level(folder: Path) -> _LevelState:
    return _LevelState(directory=folder, url="http://h/", grid_x=1, grid_y=1, postfix=".png",
                       tiles={"pre": {}}, validated_at=7.0)


@pytest.mark.parametrize("content", list(UNUSABLE.values()) + [b"{}"], ids=list(UNUSABLE) + ["empty"])
def test_load_index_unusable_leaves_state(tmp_path: Path, content: bytes | None) -> None:
    _put(tmp_path / INDEX_NAME, content)
    state = _level(tmp_path)
    RemoteTileSource._load_index(state)
    assert state.tiles == {"pre": {}}
    assert state.validated_at == 7.0


def test_load_index_reads_tiles(tmp_path: Path) -> None:
    _put(tmp_path / INDEX_NAME, _dump({"version": 1, "tiles": {"a": {"etag": "x"}}, "validatedAt": 3}))
    state = _level(tmp_path)
    RemoteTileSource._load_index(state)
    assert state.tiles == {"a": {"etag": "x"}}
    assert state.validated_at == 3.0


class _Volume:
    def fingerprint(self) -> dict[str, Any]:
        return {"source": "s", "checksum": "c", "etag": None, "lastModified": None}


def _matches(folder: Path) -> bool:
    session = object.__new__(RemoteSession)
    session.volume = _Volume()  # type: ignore[assignment]
    session.cache_root = folder
    return session._matches_saved_state()


@pytest.mark.parametrize("content", list(UNUSABLE.values()) + [b"{}"], ids=list(UNUSABLE) + ["empty"])
def test_saved_state_unusable_does_not_match(tmp_path: Path, content: bytes | None) -> None:
    _put(tmp_path / VOLUME_STATE_NAME, content)
    assert _matches(tmp_path) is False


def test_saved_state_matches_checksum(tmp_path: Path) -> None:
    _put(tmp_path / VOLUME_STATE_NAME, _dump({"source": "s", "checksum": "c"}))
    assert _matches(tmp_path) is True
    _put(tmp_path / VOLUME_STATE_NAME, _dump({"source": "s", "checksum": "other"}))
    assert _matches(tmp_path) is False


# --------------------------------------------------------------------------- helper


JSON_VALUES = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(),
    lambda children: st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4),
    max_leaves=12,
)


@settings(max_examples=100, deadline=None)
@given(value=JSON_VALUES)
def test_read_json_object_returns_objects_only(value: Any) -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "x.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        expected = value if isinstance(value, dict) else None
        assert read_json_object(path) == expected


@pytest.mark.parametrize("content", list(UNUSABLE.values()), ids=list(UNUSABLE))
def test_read_json_object_unusable_is_none(tmp_path: Path, content: bytes | None) -> None:
    path = tmp_path / "x.json"
    _put(path, content)
    assert read_json_object(path) is None


def test_read_json_object_directory_is_none(tmp_path: Path) -> None:
    assert read_json_object(tmp_path) is None


def test_read_json_object_not_utf8_raises(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    _put(path, NOT_UTF8)
    with pytest.raises(UnicodeDecodeError):
        read_json_object(path)


# --------------------------------------------------------------------------- not utf-8


def _not_utf8_readers(tmp_path: Path) -> dict[str, tuple[Path, Callable[[], Any]]]:
    out = tmp_path / "out"
    return {
        "load_cache_meta": (ingest.cache_meta_path(out), lambda: ingest.load_cache_meta(out)),
        "load_export_source": (ingest.export_source_path(out), lambda: ingest.load_export_source(out)),
        "write_export_source": (
            ingest.export_source_path(out),
            lambda: ingest.write_export_source(out, {"source": {"kind": "sql"}}),
        ),
        "inscribed": (tmp_path / "inscribed.json", lambda: _load_inscribed_cache(tmp_path / "inscribed.json")),
        "links": (tmp_path / "links.json", lambda: _load_links_cache(tmp_path / "links.json", "d", "r")),
        "index": (tmp_path / INDEX_NAME, lambda: RemoteTileSource._load_index(_level(tmp_path))),
        "saved_state": (tmp_path / VOLUME_STATE_NAME, lambda: _matches(tmp_path)),
    }


@pytest.mark.parametrize(
    "reader",
    ["load_cache_meta", "load_export_source", "write_export_source", "inscribed", "links", "index", "saved_state"],
)
def test_not_utf8_still_raises(tmp_path: Path, reader: str) -> None:
    path, call = _not_utf8_readers(tmp_path)[reader]
    _put(path, NOT_UTF8)
    with pytest.raises(UnicodeDecodeError):
        call()
