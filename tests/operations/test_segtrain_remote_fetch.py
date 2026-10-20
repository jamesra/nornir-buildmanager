"""Characterization tests for ``RemoteTileSource.fetch``.

The example tests pin each branch of the per-tile cache decision through the injected
``http_get`` seam: trusted cache hits, directory-listing hits, size or date mismatches, stamp
backfill, tiles absent from the listing, conditional headers, 404/304/failed answers, and the
level's validation stamp and saved index. The Hypothesis test runs generated cache and server
states through ``fetch`` and through a frozen copy of the pre-refactor body and compares every
request, log line, cached file, saved index byte, and state field.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from nornir_buildmanager.operations.segmentationtraining import remote_tiles
from nornir_buildmanager.operations.segmentationtraining.remote_tiles import (
    INDEX_NAME,
    PNG_SIGNATURE,
    HttpResponse,
    RemoteTileSource,
    Revalidate,
    _LevelState,
    parse_viking_xml,
)
from nornir_buildmanager.operations.segmentationtraining.stitch import tile_filename

VIKING_XML = """<?xml version="1.0" encoding="utf-8"?>
<Volume Version="2" Name="Lab" InputChecksum="abc" path="http://lab.example/vol">
  <Sections>
    <Section Number="1" Path="sec1" Name="1">
      <Tileset path="TEM/Leveled/Tileset" name="TEM.Leveled" TileXDim="8" TileYDim="8"
               FilePrefix="" FilePostfix=".png" CoordFormat="03d">
        <Level path="001" Downsample="1" GridDimX="3" GridDimY="3" />
      </Tileset>
    </Section>
  </Sections>
</Volume>
"""

VOLUME = parse_viking_xml(VIKING_XML, source="http://lab.example/vol/x.vikingxml")
TILESET = VOLUME.tilesets[1][0]
LEVEL_URL = TILESET.level_url(VOLUME.base_url, 1)
NOW = 5_000_000.0


def _name(ix: int, iy: int) -> str:
    return tile_filename("", ".png", ix, iy)


def _png(tag: bytes) -> bytes:
    return PNG_SIGNATURE + b"body" + tag


# ---------------------------------------------------------------------- frozen old body


def _old_fetch(self: RemoteTileSource, downsample: int, coords: Iterable[tuple[int, int]]) -> None:
    """Pre-refactor ``RemoteTileSource.fetch`` body, kept verbatim for the equivalence test."""
    state = self._levels.get(int(downsample))
    if state is None:
        return
    wanted: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for ix, iy in coords:
        if ix < 0 or iy < 0 or (state.grid_x and ix >= state.grid_x) or (state.grid_y and iy >= state.grid_y):
            continue
        name = tile_filename(self.tileset.prefix, self.tileset.postfix, ix, iy)
        if name in seen or name in state.missing:
            continue
        seen.add(name)
        wanted.append((name, ix, iy))
    if not wanted:
        return
    state.directory.mkdir(parents=True, exist_ok=True)
    trusted = self._trusted(state)
    if not trusted and any((state.directory / name).is_file() for name, _, _ in wanted):
        self._ensure_listing(state)
    jobs: list[tuple[str, dict[str, str], bool]] = []
    for name, _ix, _iy in wanted:
        cached = state.directory / name
        have = cached.is_file() and cached.stat().st_size > 0
        entry = state.tiles.get(name) or {}
        if have and trusted:
            continue
        if have and state.listing is not None:
            listed = state.listing.get(name)
            if listed is None:
                self._forget(state, name, cached)
                state.missing.add(name)
                continue
            same_size = listed.size is None or listed.size == cached.stat().st_size
            same_stamp = listed.stamp is None or entry.get("listed") in (None, listed.stamp)
            if same_size and same_stamp:
                if listed.stamp and entry.get("listed") != listed.stamp:
                    state.tiles.setdefault(name, {})["listed"] = listed.stamp
                    state.dirty = True
                continue
            jobs.append((name, {}, True))
            continue
        if not have and state.listing is not None and name not in state.listing:
            state.missing.add(name)
            continue
        headers: dict[str, str] = {}
        if have:
            if entry.get("etag"):
                headers["If-None-Match"] = str(entry["etag"])
            if entry.get("lastModified"):
                headers["If-Modified-Since"] = str(entry["lastModified"])
        jobs.append((name, headers, have))
    results = self._run_downloads(state, jobs)
    for name, outcome in results.items():
        entry = state.tiles.setdefault(name, {})
        if outcome is None:
            continue
        kind, response = outcome
        if kind == "missing":
            state.missing.add(name)
            state.tiles.pop(name, None)
            state.dirty = True
        elif kind == "ok" and response is not None:
            entry["etag"] = response.headers.get("etag", "")
            entry["lastModified"] = response.headers.get("last-modified", "")
            entry["size"] = len(response.body)
            if state.listing and name in state.listing and state.listing[name].stamp:
                entry["listed"] = state.listing[name].stamp
            state.dirty = True
    for name, _headers, _have in jobs:
        if name in state.tiles and state.listing and name in state.listing and state.listing[name].stamp:
            state.tiles[name]["listed"] = state.listing[name].stamp
            state.dirty = True
    if not trusted:
        state.checked_this_run = True
        state.validated_at = self._clock()
        state.dirty = True
    self._save_index(state)


# ---------------------------------------------------------------------- scripted server


@dataclass
class Script:
    """Scripted answers: ``tiles`` maps a tile name to an answer kind, ``listing`` is the folder's.

    Tile kinds: ``png`` (200 with ETag and Last-Modified), ``png-bare`` (200, no validators),
    ``html`` (200, not a PNG), ``empty`` (200, no body), ``304``, ``404``, ``500``, ``raise``
    (``OSError``). Unlisted names answer 404. ``listing`` is ``None`` (404), ``"raise"``, or a JSON
    array of ``{name, size, mtime}`` rows.
    """

    tiles: dict[str, str] = field(default_factory=dict)
    listing: list[dict[str, Any]] | str | None = None
    requests: list[tuple[str, tuple[tuple[str, str], ...]]] = field(default_factory=list)

    def __call__(self, url: str, headers: Mapping[str, str] | None = None) -> HttpResponse:
        self.requests.append((url, tuple(sorted((headers or {}).items()))))
        if url == LEVEL_URL + "/":
            if self.listing == "raise":
                raise OSError("listing refused")
            if self.listing is None:
                return HttpResponse(404, {})
            return HttpResponse(200, {"content-type": "application/json"}, json.dumps(self.listing).encode())
        name = url.rsplit("/", 1)[-1]
        kind = self.tiles.get(name, "404")
        body = _png(name.encode())
        if kind == "png":
            return HttpResponse(200, {"etag": f'"{name}"', "last-modified": "Mon, 01 May 2023 12:00:00 GMT"}, body)
        if kind == "png-bare":
            return HttpResponse(200, {}, body)
        if kind == "html":
            return HttpResponse(200, {}, b"<html>error</html>")
        if kind == "empty":
            return HttpResponse(200, {}, b"")
        if kind == "raise":
            raise OSError("connection reset")
        return HttpResponse(int(kind), {})

    def tile_requests(self) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
        return [(url.rsplit("/", 1)[-1], headers) for url, headers in self.requests if not url.endswith("/")]


def _seed(root: Path, files: Mapping[str, bytes], index: Mapping[str, Any] | None) -> Path:
    """Write cached tiles and an optional saved index into the level folder; return the folder."""
    level = root / "z1" / "TEM.Leveled" / "1"
    level.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (level / name).write_bytes(body)
    if index is not None:
        (level / INDEX_NAME).write_text(json.dumps(index))
    return level


def _source(root: Path, script: Script, **kwargs: Any) -> RemoteTileSource:
    kwargs.setdefault("clock", lambda: NOW)
    return RemoteTileSource(VOLUME, TILESET, root, http_get=script, threads=1, **kwargs)


def _state(source: RemoteTileSource) -> _LevelState:
    return source._levels[1]


def _index(level: Path) -> dict[str, Any]:
    return json.loads((level / INDEX_NAME).read_text())


# ---------------------------------------------------------------------- examples


def test_trusted_cache_hit_makes_no_request_and_keeps_the_index(tmp_path: Path) -> None:
    level = _seed(tmp_path, {_name(0, 0): _png(b"old")}, {"version": 1, "validatedAt": 1.0, "tiles": {}})
    script = Script(tiles={_name(0, 0): "png"})
    source = _source(tmp_path, script, revalidate=Revalidate("never"))
    source.fetch(1, [(0, 0)])
    assert script.requests == []
    assert _state(source).tiles == {}  # a trusted hit is not a job: no bookkeeping entry
    assert _index(level) == {"version": 1, "validatedAt": 1.0, "tiles": {}}  # not dirty: not rewritten
    assert not _state(source).checked_this_run


def test_listing_hit_backfills_the_listed_stamp_without_a_tile_request(tmp_path: Path) -> None:
    body = _png(b"same")
    level = _seed(tmp_path, {_name(0, 0): body}, {"version": 1, "validatedAt": None, "tiles": {_name(0, 0): {"etag": "e"}}})
    script = Script(listing=[{"name": _name(0, 0), "size": len(body), "mtime": "2023-05-01 12:00"}])
    source = _source(tmp_path, script)
    source.fetch(1, [(0, 0)])
    assert script.tile_requests() == []
    assert [url for url, _ in script.requests] == [LEVEL_URL + "/"]
    assert _index(level) == {"version": 1, "validatedAt": NOW, "tiles": {_name(0, 0): {"etag": "e", "listed": "2023-05-01 12:00"}}}


def test_listing_size_mismatch_refetches_without_conditional_headers(tmp_path: Path) -> None:
    level = _seed(tmp_path, {_name(1, 0): _png(b"short")}, {"version": 1, "tiles": {_name(1, 0): {"etag": "e", "lastModified": "m"}}})
    script = Script(tiles={_name(1, 0): "png"}, listing=[{"name": _name(1, 0), "size": 9999, "mtime": "2023-06-01 08:00"}])
    source = _source(tmp_path, script)
    source.fetch(1, [(1, 0)])
    assert script.tile_requests() == [(_name(1, 0), ())]
    entry = _index(level)["tiles"][_name(1, 0)]
    assert entry == {
        "etag": f'"{_name(1, 0)}"',
        "lastModified": "Mon, 01 May 2023 12:00:00 GMT",
        "size": len(_png(_name(1, 0).encode())),
        "listed": "2023-06-01 08:00",
    }


def test_listing_stamp_change_refetches_and_a_304_keeps_the_new_stamp(tmp_path: Path) -> None:
    body = _png(b"x")
    level = _seed(tmp_path, {_name(0, 1): body}, {"version": 1, "tiles": {_name(0, 1): {"listed": "old"}}})
    script = Script(tiles={_name(0, 1): "304"}, listing=[{"name": _name(0, 1), "size": len(body), "mtime": "new"}])
    source = _source(tmp_path, script)
    source.fetch(1, [(0, 1)])
    assert script.tile_requests() == [(_name(0, 1), ())]
    assert source.not_modified == 1
    assert _index(level)["tiles"] == {_name(0, 1): {"listed": "new"}}


def test_cached_tile_absent_from_the_listing_is_forgotten_and_marked_missing(tmp_path: Path) -> None:
    level = _seed(tmp_path, {_name(0, 0): _png(b"a"), _name(1, 0): _png(b"b")}, {"version": 1, "tiles": {_name(0, 0): {"etag": "e"}}})
    script = Script(tiles={_name(2, 0): "png"}, listing=[{"name": _name(1, 0)}, {"name": _name(2, 0)}])
    source = _source(tmp_path, script)
    source.fetch(1, [(0, 0), (1, 0), (2, 0), (2, 1)])
    assert not (level / _name(0, 0)).exists()
    assert _state(source).missing == {_name(0, 0), _name(2, 1)}  # (2, 1) is not cached and not listed
    assert script.tile_requests() == [(_name(2, 0), ())]  # a listed tile with no size or date counts as current
    assert set(_index(level)["tiles"]) == {_name(2, 0)}


def test_conditional_headers_carry_the_saved_etag_and_last_modified(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        {_name(0, 0): _png(b"a"), _name(1, 0): _png(b"b"), _name(2, 0): _png(b"c")},
        {
            "version": 1,
            "tiles": {
                _name(0, 0): {"etag": "E0", "lastModified": "L0"},
                _name(1, 0): {"etag": "", "lastModified": "L1"},
            },
        },
    )
    script = Script(tiles={_name(0, 0): "304", _name(1, 0): "304", _name(2, 0): "304"})
    source = _source(tmp_path, script)
    source.fetch(1, [(0, 0), (1, 0), (2, 0)])
    assert script.tile_requests() == [
        (_name(0, 0), (("If-Modified-Since", "L0"), ("If-None-Match", "E0"))),
        (_name(1, 0), (("If-Modified-Since", "L1"),)),
        (_name(2, 0), ()),
    ]
    assert source.not_modified == 3


def test_404_pops_the_entry_and_failed_answers_leave_an_empty_entry(tmp_path: Path) -> None:
    level = _seed(tmp_path, {_name(0, 0): _png(b"a")}, {"version": 1, "tiles": {_name(0, 0): {"etag": "E"}}})
    script = Script(tiles={_name(0, 0): "404", _name(1, 0): "500", _name(2, 0): "html", _name(0, 1): "raise", _name(1, 1): "empty"})
    source = _source(tmp_path, script)
    source.fetch(1, [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1)])
    state = _state(source)
    assert state.missing == {_name(0, 0)}
    assert state.tiles == {_name(1, 0): {}, _name(2, 0): {}, _name(0, 1): {}, _name(1, 1): {}}
    assert sorted(path.name for path in level.glob("*.png")) == [_name(0, 0)]  # a 404 does not delete the cached file
    assert source.downloaded == 0
    source.fetch(1, [(0, 0)])  # missing this run: not asked again
    assert sum(name == _name(0, 0) for name, _ in script.tile_requests()) == 1


def test_validation_stamp_is_set_once_per_untrusted_pass(tmp_path: Path) -> None:
    clock = [100.0]
    script = Script(tiles={_name(0, 0): "png", _name(1, 0): "png"})
    source = _source(tmp_path, script, clock=lambda: clock[0])
    source.fetch(1, [(0, 0)])
    state = _state(source)
    assert (state.checked_this_run, state.validated_at, state.dirty) == (True, 100.0, False)
    clock[0] = 200.0
    source.fetch(1, [(0, 0), (1, 0)])  # policy "once": now trusted, only the new tile is fetched
    assert state.validated_at == 100.0
    assert [name for name, _ in script.tile_requests()] == [_name(0, 0), _name(1, 0)]
    assert _index(state.directory)["validatedAt"] == 100.0


def test_out_of_grid_repeated_and_unknown_level_coords_do_nothing(tmp_path: Path) -> None:
    script = Script(tiles={_name(0, 0): "png"})
    source = _source(tmp_path, script)
    source.fetch(7, [(0, 0)])
    source.fetch(1, [(-1, 0), (3, 0), (0, 3)])
    assert script.requests == []
    assert not _state(source).directory.exists()
    source.fetch(1, [(0, 0), (0, 0)])
    assert script.tile_requests() == [(_name(0, 0), ())]


# ---------------------------------------------------------------------- equivalence

NAMES = [_name(ix, iy) for ix in range(3) for iy in range(3)]
TILE_KINDS = ["png", "png-bare", "html", "empty", "304", "404", "500", "raise"]
POLICIES = [Revalidate("once"), Revalidate("always"), Revalidate("never"), Revalidate("hours", 1.0)]


@st.composite
def scenarios(draw: st.DrawFn) -> dict[str, Any]:
    """A cache folder, saved index, scripted server, policy, and a short series of fetch calls."""
    files: dict[str, bytes] = {}
    entries: dict[str, dict[str, Any]] = {}
    listing_rows: list[dict[str, Any]] = []
    tiles: dict[str, str] = {}
    stamps = st.sampled_from(["s1", "s2", ""])
    for name in NAMES:
        cache = draw(st.sampled_from(["none", "empty", "short", "long"]))
        if cache != "none":
            files[name] = {"empty": b"", "short": _png(b"s"), "long": _png(b"long-body")}[cache]
        if draw(st.booleans()):
            entry: dict[str, Any] = {}
            for key, values in (("etag", st.sampled_from(["E", ""])), ("lastModified", st.sampled_from(["L", ""])), ("listed", stamps)):
                if draw(st.booleans()):
                    entry[key] = draw(values)
            entries[name] = entry
        if draw(st.booleans()):
            row: dict[str, Any] = {"name": name}
            size = draw(st.sampled_from([None, len(_png(b"s")), len(_png(b"long-body")), 3]))
            if size is not None:
                row["size"] = size
            stamp = draw(stamps)
            if stamp:
                row["mtime"] = stamp
            listing_rows.append(row)
        tiles[name] = draw(st.sampled_from(TILE_KINDS))
    listing_mode = draw(st.sampled_from(["404", "raise", "rows", "no-tiles"]))
    listing: list[dict[str, Any]] | str | None
    if listing_mode == "404":
        listing = None
    elif listing_mode == "raise":
        listing = "raise"
    elif listing_mode == "no-tiles":
        listing = [{"name": "readme.txt", "size": 5}]
    else:
        listing = listing_rows
    index: dict[str, Any] | None = None
    if draw(st.booleans()):
        index = {"version": 1, "validatedAt": draw(st.sampled_from([None, NOW - 60.0, NOW - 7200.0])), "tiles": entries}
    coord = st.tuples(st.integers(-1, 3), st.integers(-1, 3))
    calls = draw(st.lists(st.tuples(st.sampled_from([1, 1, 1, 2]), st.lists(coord, max_size=8)), min_size=1, max_size=3))
    return {
        "files": files,
        "index": index,
        "tiles": tiles,
        "listing": listing,
        "policy": draw(st.sampled_from(POLICIES)),
        "volume_unchanged": draw(st.booleans()),
        "calls": calls,
    }


class _Capture(logging.Handler):
    """Collects (level, message) pairs from the remote_tiles logger."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[tuple[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.levelname, record.getMessage()))


def _observe(fetch: Any, root: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    """Run *scenario* with *fetch* as ``RemoteTileSource.fetch`` and return everything observable."""
    level = _seed(root, scenario["files"], scenario["index"])
    script = Script(tiles=dict(scenario["tiles"]), listing=scenario["listing"])
    ticks = iter(NOW + step for step in range(100))
    source = RemoteTileSource(
        VOLUME,
        TILESET,
        root,
        http_get=script,
        threads=1,
        revalidate=scenario["policy"],
        volume_unchanged=scenario["volume_unchanged"],
        clock=lambda: next(ticks),
    )
    capture = _Capture()
    logger = remote_tiles._logger
    old_level = logger.level
    logger.addHandler(capture)
    logger.setLevel(logging.DEBUG)
    snapshots: list[Any] = []
    try:
        for downsample, coords in scenario["calls"]:
            fetch(source, downsample, coords)
            state = _state(source)
            snapshots.append(
                (
                    json.dumps(state.tiles, sort_keys=False),
                    sorted(state.missing),
                    state.validated_at,
                    state.checked_this_run,
                    state.dirty,
                    state.listing,
                    state.listing_tried,
                    (source.requests, source.downloaded, source.not_modified),
                    {path.name: path.read_bytes() for path in sorted(level.iterdir())} if level.exists() else None,
                )
            )
    finally:
        logger.removeHandler(capture)
        logger.setLevel(old_level)
    return {"requests": script.requests, "logs": capture.records, "snapshots": snapshots}


@settings(max_examples=1500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(scenarios())
def test_fetch_matches_the_frozen_pre_refactor_body(scenario: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory() as old_root, tempfile.TemporaryDirectory() as new_root:
        old = _observe(_old_fetch, Path(old_root), scenario)
        new = _observe(RemoteTileSource.fetch, Path(new_root), scenario)
    assert new == old
