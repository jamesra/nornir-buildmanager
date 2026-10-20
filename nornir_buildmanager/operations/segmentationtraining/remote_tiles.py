"""Tiles from another lab's Viking volume, read through its VikingXML.

``ExportTrainingData`` reads tiles from local tileset folders. For a volume that lives on
another server, :class:`RemoteTileSource` makes one level folder per downsample on local disk
look like that tileset: a cache directory that is filled, and checked against the server, a
little before the existing missing-tile checks run. Stitching, repair and ``-Update`` read the
cache directory exactly as they would read a Nornir tileset.

Only the tiles the planned crops cover are requested. Images change less often than transforms
and annotations, so a cache hit is validated as cheaply as the server allows, cheapest first:

1. **Volume.** The VikingXML is compared with the one the cache was filled from (its
   ``InputChecksum``, else ETag or Last-Modified). Unchanged: every cached tile is current and
   no tile request is made.
2. **Level folder.** One request for the folder's directory listing (IIS, nginx, Apache or a JSON
   index) gives every tile's name and, when shown, size and date. Changed or vanished tiles are
   found without per-tile requests.
3. **Tile.** A conditional GET (``If-None-Match`` / ``If-Modified-Since``) answered by 304.
4. A server that offers none of these costs a full refetch of the tiles used.

``Revalidate`` chooses how often: ``once`` (the default) checks each level once per run,
``always`` never trusts the cache, ``never`` always does, and a number of hours skips a level
checked that recently. No credentials are sent: the other lab's endpoints are open.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import re
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from nornir_buildmanager.operations.segmentationtraining.atomic_io import (
    atomic_write_text,
    read_json_object,
)
from nornir_buildmanager.operations.segmentationtraining.stitch import tile_filename

_logger = logging.getLogger(__name__)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
INDEX_NAME = "_index.json"
VOLUME_STATE_NAME = "_volume.json"
_MAX_REDIRECTS = 4
_RETRIES = 3
_TIMEOUT_SECONDS = 60.0
DEFAULT_FETCH_THREADS = 8


# --------------------------------------------------------------------------- HTTP


@dataclass(frozen=True)
class HttpResponse:
    """Status, lower-cased headers and body of one GET."""

    status: int
    headers: Mapping[str, str]
    body: bytes = b""


#: ``(url, extra_headers) -> HttpResponse``. Tests inject their own.
HttpGet = Callable[[str, Mapping[str, str] | None], HttpResponse]

_local = threading.local()


def _connection(scheme: str, netloc: str) -> http.client.HTTPConnection:
    pool: dict[tuple[str, str], http.client.HTTPConnection] = _local.__dict__.setdefault("pool", {})
    key = (scheme, netloc)
    connection = pool.get(key)
    if connection is None:
        factory = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        connection = factory(netloc, timeout=_TIMEOUT_SECONDS)
        pool[key] = connection
    return connection


def _drop_connection(scheme: str, netloc: str) -> None:
    pool: dict[tuple[str, str], http.client.HTTPConnection] = _local.__dict__.get("pool", {})
    connection = pool.pop((scheme, netloc), None)
    if connection is not None:
        try:
            connection.close()
        except OSError:
            pass


def default_http_get(url: str, headers: Mapping[str, str] | None = None) -> HttpResponse:
    """GET with a keep-alive connection per thread, redirects, and retries on transient failures.

    4xx answers (including 404 and 304) are returned, not raised. Anything still failing after
    the retries raises ``OSError``.
    """
    request_headers = {"Accept": "*/*", **(headers or {})}
    target = url
    for _hop in range(_MAX_REDIRECTS + 1):
        parts = urllib.parse.urlsplit(target)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise OSError(f"Not an http(s) URL: {target}")
        path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
        last_error: Exception | None = None
        response: HttpResponse | None = None
        for attempt in range(_RETRIES):
            try:
                connection = _connection(parts.scheme, parts.netloc)
                connection.request("GET", path, headers=request_headers)
                raw = connection.getresponse()
                body = raw.read()
                lowered = {key.lower(): value for key, value in raw.getheaders()}
                if raw.status >= 500:
                    raise http.client.HTTPException(f"HTTP {raw.status} from {target}")
                response = HttpResponse(raw.status, lowered, body)
                break
            except (OSError, http.client.HTTPException) as exc:
                last_error = exc
                _drop_connection(parts.scheme, parts.netloc)
                time.sleep(0.4 * (2**attempt))
        if response is None:
            raise OSError(f"GET {target} failed: {last_error}")
        if response.status in (301, 302, 303, 307, 308) and "location" in response.headers:
            target = urllib.parse.urljoin(target, response.headers["location"])
            continue
        return response
    raise OSError(f"Too many redirects fetching {url}")


def _join_url(*parts: str) -> str:
    """Join with single slashes; the first part keeps its scheme and any leading slash."""
    cleaned = [str(part).replace("\\", "/") for part in parts if str(part) != ""]
    if not cleaned:
        return ""
    joined = cleaned[0].rstrip("/")
    for part in cleaned[1:]:
        joined += "/" + part.strip("/")
    return joined


# --------------------------------------------------------------------------- VikingXML


@dataclass(frozen=True)
class RemoteLevel:
    """One downsample level of a remote tileset."""

    downsample: int
    path: str
    grid_x: int
    grid_y: int


@dataclass(frozen=True)
class RemoteTileset:
    """A section's Channel.Filter tileset as the VikingXML describes it."""

    section: int
    section_path: str
    name: str
    path: str
    tile_x: int
    tile_y: int
    prefix: str
    postfix: str
    levels: Mapping[int, RemoteLevel]

    def _channel_filter(self) -> tuple[str, str]:
        """``Channel.Filter`` from the name; else ``Channel/Filter/...`` from the tileset path.

        Some volumes name the tileset just ``Leveled``; their path (``SEM/Leveled/Tileset``) still
        says which channel and filter it is.
        """
        if "." in self.name:
            channel, filter_name = self.name.split(".", 1)
            return channel, filter_name
        parts = [part for part in re.split(r"[\\/]", self.path) if part]
        if len(parts) >= 2:
            return parts[0], parts[1]
        return self.name, ""

    @property
    def channel(self) -> str:
        return self._channel_filter()[0]

    @property
    def filter(self) -> str:
        return self._channel_filter()[1]

    def level_url(self, base: str, downsample: int) -> str:
        """Folder URL of one level, without a trailing slash."""
        level = self.levels[int(downsample)]
        return _join_url(base, self.section_path, self.path, level.path)


@dataclass(frozen=True)
class RemoteVolume:
    """The parts of a VikingXML the exporter uses."""

    name: str | None
    base_url: str
    checksum: str
    tilesets: Mapping[int, tuple[RemoteTileset, ...]]
    source: str
    etag: str = ""
    last_modified: str = ""

    def sections(self) -> list[int]:
        return sorted(self.tilesets)

    def fingerprint(self) -> dict[str, str]:
        """What identifies this version of the volume for the cache's whole-volume check."""
        return {
            "source": self.source,
            "checksum": self.checksum,
            "etag": self.etag,
            "lastModified": self.last_modified,
        }


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def parse_viking_xml(
    text: str,
    *,
    source: str,
    host: str | None = None,
    etag: str = "",
    last_modified: str = "",
) -> RemoteVolume:
    """Read sections and tilesets from VikingXML text.

    Tile URLs are ``base / Section.Path / Tileset.path / Level.path / file``. ``base`` is *host*
    when given, else the Volume element's merged ``path`` (host plus the volume's folder), else its
    ``host``, else the folder the VikingXML itself came from.
    """
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ValueError(f"{source} is not valid VikingXML: {exc}") from exc
    if root.tag != "Volume":
        raise ValueError(f"{source}: expected a Volume element, found {root.tag}")
    if host:
        base = host.strip()
    else:
        base = (root.get("path") or root.get("host") or "").strip()
        if not base:
            base = source.rsplit("/", 1)[0] if "/" in source.replace("\\", "/") else ""
    tilesets: dict[int, list[RemoteTileset]] = {}
    sections = root.find("Sections")
    section_nodes = sections.findall("Section") if sections is not None else root.findall("Section")
    for section in section_nodes:
        number = _int(section.get("Number"), -1)
        if number < 0:
            continue
        for node in section.findall("Tileset"):
            levels: dict[int, RemoteLevel] = {}
            for level in node.findall("Level"):
                downsample = _int(level.get("Downsample"), 0)
                if downsample < 1:
                    continue
                levels[downsample] = RemoteLevel(
                    downsample=downsample,
                    path=level.get("path") or level.get("Path") or "",
                    grid_x=_int(level.get("GridDimX")),
                    grid_y=_int(level.get("GridDimY")),
                )
            if not levels:
                continue
            tilesets.setdefault(number, []).append(
                RemoteTileset(
                    section=number,
                    section_path=section.get("Path") or "",
                    name=node.get("name") or "",
                    path=node.get("path") or "",
                    tile_x=_int(node.get("TileXDim")),
                    tile_y=_int(node.get("TileYDim")),
                    prefix=node.get("FilePrefix") or "",
                    postfix=node.get("FilePostfix") or ".png",
                    levels=levels,
                )
            )
    return RemoteVolume(
        name=root.get("Name") or None,
        base_url=base,
        checksum=root.get("InputChecksum") or "",
        tilesets={z: tuple(sorted(items, key=lambda item: item.name)) for z, items in tilesets.items()},
        source=source,
        etag=etag,
        last_modified=last_modified,
    )


def load_viking_xml(
    source: str,
    *,
    host: str | None = None,
    http_get: HttpGet | None = None,
) -> RemoteVolume:
    """Load a VikingXML from an http(s) URL or a local file."""
    if re.match(r"^https?://", source, re.IGNORECASE):
        response = (http_get or default_http_get)(source, None)
        if response.status != 200:
            raise OSError(f"GET {source} returned HTTP {response.status}")
        return parse_viking_xml(
            response.body.decode("utf-8-sig", errors="replace"),
            source=source,
            host=host,
            etag=response.headers.get("etag", ""),
            last_modified=response.headers.get("last-modified", ""),
        )
    path = Path(source)
    stamp = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(path.stat().st_mtime))
    return parse_viking_xml(path.read_text(encoding="utf-8-sig"), source=str(path), host=host, last_modified=stamp)


# --------------------------------------------------------------------------- listing


_ANCHOR = re.compile(r'<a\s[^>]*href="([^"]+)"[^>]*>', re.IGNORECASE)
_DATE_TIME = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AP]M)?"
    r"|\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?"
    r"|\d{1,2}-[A-Za-z]{3}-\d{4}\s+\d{2}:\d{2}"
    r"|[A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}\s*[AP]M)",
)


@dataclass(frozen=True)
class ListedTile:
    """One entry of a directory listing: size and date text when the listing shows them."""

    size: int | None
    stamp: str | None


def parse_listing(body: bytes, content_type: str, postfix: str) -> dict[str, ListedTile]:
    """Tile names (ending in *postfix*) in a directory listing, or ``{}`` if it isn't one.

    Understands IIS, Apache and nginx HTML indexes and a JSON array of ``{name, size, ...}``.
    A listing with no tile names is treated as no listing at all.
    """
    text = body.decode("utf-8", errors="replace")
    found: dict[str, ListedTile] = {}
    if "json" in content_type.lower() or text.lstrip().startswith(("[", "{")):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        items = payload.get("files") if isinstance(payload, dict) else payload
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    name = str(item.get("name") or item.get("Name") or "")
                    size = item.get("size", item.get("Size"))
                    stamp = item.get("mtime", item.get("lastModified", item.get("LastModified")))
                elif isinstance(item, str):
                    name, size, stamp = item, None, None
                else:
                    continue
                if name.endswith(postfix):
                    found[name] = ListedTile(int(size) if isinstance(size, (int, float)) else None, str(stamp) if stamp else None)
            return found
    for line in re.split(r"<br\s*/?>|\r?\n", text, flags=re.IGNORECASE):
        for anchor in _ANCHOR.finditer(line):
            name = urllib.parse.unquote(anchor.group(1)).rstrip("/").rsplit("/", 1)[-1]
            if not name.endswith(postfix):
                continue
            before, after = line[: anchor.start()], line[anchor.end() :]
            after = re.split(r"</a>", after, maxsplit=1, flags=re.IGNORECASE)[-1]
            stamp_match = _DATE_TIME.search(before) or _DATE_TIME.search(after)
            stamp = stamp_match.group(0).strip() if stamp_match else None
            rest = _DATE_TIME.sub(" ", re.sub(r"<[^>]+>", " ", before + " " + after))
            sizes = re.findall(r"(?<![\w.:-])(\d{2,})(?![\w.:-])", rest)
            size = int(sizes[0]) if len(sizes) == 1 else None
            found[name] = ListedTile(size, stamp)
    return found


# --------------------------------------------------------------------------- policy


@dataclass(frozen=True)
class Revalidate:
    """How often cached tiles are checked against the server."""

    mode: str = "once"  # once | always | never | hours
    hours: float = 0.0


def parse_revalidate(value: str | float | None) -> Revalidate:
    """``once`` (default), ``always``, ``never``, or a number of hours."""
    if value is None or str(value).strip() == "":
        return Revalidate()
    text = str(value).strip().lower()
    if text in ("once", "always", "never"):
        return Revalidate(text)
    try:
        hours = float(text)
    except ValueError as exc:
        raise ValueError(f"-TileRevalidate must be once, always, never or a number of hours, got {value!r}") from exc
    if hours < 0:
        raise ValueError("-TileRevalidate hours must not be negative")
    return Revalidate("hours", hours)


# --------------------------------------------------------------------------- cache


@dataclass
class _LevelState:
    """Per-level bookkeeping for one run, over the persisted ``_index.json``."""

    directory: Path
    url: str
    grid_x: int
    grid_y: int
    postfix: str
    tiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    validated_at: float | None = None
    checked_this_run: bool = False
    listing: dict[str, ListedTile] | None = None
    listing_tried: bool = False
    missing: set[str] = field(default_factory=set)
    dirty: bool = False


class RemoteTileSource:
    """The cache directories and fetcher for one section's tileset.

    ``level_dirs`` goes to the export as the tileset's level folders. ``fetch(downsample, coords)``
    makes every available tile in *coords* current in the cache; tiles the server does not have
    are left absent so the export's own missing-tile handling reports them.
    """

    def __init__(
        self,
        volume: RemoteVolume,
        tileset: RemoteTileset,
        cache_root: str | os.PathLike[str],
        *,
        http_get: HttpGet | None = None,
        revalidate: Revalidate | None = None,
        volume_unchanged: bool = False,
        threads: int = DEFAULT_FETCH_THREADS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.volume = volume
        self.tileset = tileset
        self._http = http_get or default_http_get
        self._policy = revalidate or Revalidate()
        self._volume_unchanged = volume_unchanged
        self._threads = max(1, int(threads))
        self._clock = clock
        self._lock = threading.Lock()
        self.requests = 0
        self.downloaded = 0
        self.not_modified = 0
        self._levels: dict[int, _LevelState] = {}
        base = Path(cache_root) / f"z{tileset.section}" / (tileset.name or "tiles")
        for downsample, level in tileset.levels.items():
            directory = base / str(downsample)
            state = _LevelState(
                directory=directory,
                url=tileset.level_url(volume.base_url, downsample),
                grid_x=level.grid_x,
                grid_y=level.grid_y,
                postfix=tileset.postfix,
            )
            self._load_index(state)
            self._levels[downsample] = state

    @property
    def level_dirs(self) -> dict[int, str]:
        return {downsample: str(state.directory) for downsample, state in self._levels.items()}

    # ------------------------------------------------------------------ index

    @staticmethod
    def _load_index(state: _LevelState) -> None:
        """Fill ``state`` from the level's saved tile index; leave it unchanged when there is no usable version-1 index."""
        payload = read_json_object(state.directory / INDEX_NAME)
        if payload is not None and payload.get("version") == 1:
            tiles = payload.get("tiles")
            state.tiles = tiles if isinstance(tiles, dict) else {}
            validated = payload.get("validatedAt")
            state.validated_at = float(validated) if isinstance(validated, (int, float)) else None

    def _save_index(self, state: _LevelState) -> None:
        if not state.dirty:
            return
        state.directory.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "validatedAt": state.validated_at, "tiles": state.tiles}
        atomic_write_text(state.directory / INDEX_NAME, json.dumps(payload))
        state.dirty = False

    # ------------------------------------------------------------------ policy

    def _trusted(self, state: _LevelState) -> bool:
        """True when cached tiles of this level may be used without asking the server."""
        mode = self._policy.mode
        if mode == "never":
            return True
        if mode == "always":
            return False
        if self._volume_unchanged and state.validated_at is not None:
            return True
        if mode == "hours":
            return state.validated_at is not None and (self._clock() - state.validated_at) < self._policy.hours * 3600.0
        return state.checked_this_run

    def _get(self, url: str, headers: Mapping[str, str] | None = None) -> HttpResponse:
        with self._lock:
            self.requests += 1
        return self._http(url, headers)

    # ------------------------------------------------------------------ listing

    def _ensure_listing(self, state: _LevelState) -> None:
        """Probe the level folder's directory listing once per run; remember whether it has one."""
        if state.listing_tried:
            return
        state.listing_tried = True
        try:
            response = self._get(state.url + "/")
        except OSError as exc:
            _logger.info("Remote tiles: no listing at %s (%s)", state.url, exc)
            return
        if response.status != 200:
            return
        listing = parse_listing(response.body, response.headers.get("content-type", ""), state.postfix)
        if listing:
            state.listing = listing
            _logger.info("Remote tiles: %s offers a listing of %d tiles", state.url, len(listing))

    # ------------------------------------------------------------------ fetch

    def fetch(self, downsample: int, coords: Iterable[tuple[int, int]]) -> None:
        """Make the available tiles among *coords* current in the cache for one level."""
        state = self._levels.get(int(downsample))
        if state is None:
            return
        wanted = self._wanted_tiles(state, coords)
        if not wanted:
            return
        state.directory.mkdir(parents=True, exist_ok=True)
        trusted = self._trusted(state)
        if not trusted and any((state.directory / name).is_file() for name in wanted):
            self._ensure_listing(state)
        jobs: list[tuple[str, dict[str, str], bool]] = []
        for name in wanted:
            job = self._plan_job(state, name, trusted)
            if job is not None:
                jobs.append(job)
        self._apply_outcomes(state, jobs, self._run_downloads(state, jobs))
        if not trusted:
            state.checked_this_run = True
            state.validated_at = self._clock()
            state.dirty = True
        self._save_index(state)

    def _wanted_tiles(self, state: _LevelState, coords: Iterable[tuple[int, int]]) -> list[str]:
        """Tile names for *coords* inside the level's grid, first occurrence only, skipping tiles known missing this run."""
        wanted: list[str] = []
        seen: set[str] = set()
        for ix, iy in coords:
            if ix < 0 or iy < 0 or (state.grid_x and ix >= state.grid_x) or (state.grid_y and iy >= state.grid_y):
                continue
            name = tile_filename(self.tileset.prefix, self.tileset.postfix, ix, iy)
            if name in seen or name in state.missing:
                continue
            seen.add(name)
            wanted.append(name)
        return wanted

    def _plan_job(self, state: _LevelState, name: str, trusted: bool) -> tuple[str, dict[str, str], bool] | None:
        """Decide one tile from the cache, the level's listing and the saved index.

        Returns the download job ``(name, request headers, have cached copy)``, or ``None`` when
        the tile needs no request. A ``None`` may still update ``state``: a cached tile the listing
        no longer shows is deleted and marked missing, an uncached unlisted tile is marked missing,
        and a listing hit backfills the entry's ``listed`` date.
        """
        cached = state.directory / name
        have = cached.is_file() and cached.stat().st_size > 0
        entry = state.tiles.get(name) or {}
        if have and trusted:
            return None
        if have and state.listing is not None:
            listed = state.listing.get(name)
            if listed is None:
                self._forget(state, name, cached)
                state.missing.add(name)
                return None
            same_size = listed.size is None or listed.size == cached.stat().st_size
            # A cache filled before a listing was seen has no listed date yet: size decides.
            same_stamp = listed.stamp is None or entry.get("listed") in (None, listed.stamp)
            if same_size and same_stamp:
                if listed.stamp and entry.get("listed") != listed.stamp:
                    state.tiles.setdefault(name, {})["listed"] = listed.stamp
                    state.dirty = True
                return None
            return name, {}, True
        if not have and state.listing is not None and name not in state.listing:
            state.missing.add(name)
            return None
        headers: dict[str, str] = {}
        if have:
            if entry.get("etag"):
                headers["If-None-Match"] = str(entry["etag"])
            if entry.get("lastModified"):
                headers["If-Modified-Since"] = str(entry["lastModified"])
        return name, headers, have

    @staticmethod
    def _apply_outcomes(
        state: _LevelState,
        jobs: list[tuple[str, dict[str, str], bool]],
        results: dict[str, tuple[str, HttpResponse | None] | None],
    ) -> None:
        """Record download outcomes in the level's tile index: validators of new bodies, 404s as missing, listed dates."""
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

    def _forget(self, state: _LevelState, name: str, cached: Path) -> None:
        try:
            cached.unlink()
        except OSError:
            pass
        state.tiles.pop(name, None)
        state.dirty = True

    def _run_downloads(
        self,
        state: _LevelState,
        jobs: list[tuple[str, dict[str, str], bool]],
    ) -> dict[str, tuple[str, HttpResponse | None] | None]:
        results: dict[str, tuple[str, HttpResponse | None] | None] = {}
        if not jobs:
            return results

        def work(job: tuple[str, dict[str, str], bool]) -> tuple[str, tuple[str, HttpResponse | None] | None]:
            name, headers, _have = job
            try:
                response = self._get(_join_url(state.url, name), headers)
            except OSError as exc:
                _logger.warning("Remote tile %s/%s: %s", state.url, name, exc)
                return name, None
            if response.status == 304:
                with self._lock:
                    self.not_modified += 1
                return name, ("current", None)
            if response.status == 404:
                return name, ("missing", None)
            if response.status != 200 or not self._plausible(response):
                _logger.warning("Remote tile %s/%s: HTTP %s, not stored", state.url, name, response.status)
                return name, None
            self._store(state.directory / name, response.body)
            with self._lock:
                self.downloaded += 1
            return name, ("ok", response)

        if self._threads <= 1 or len(jobs) == 1:
            for job in jobs:
                name, outcome = work(job)
                results[name] = outcome
            return results
        import nornir_pools

        from nornir_buildmanager.operations.segmentationtraining.poolutil import (
            submit_bounded,
        )

        pool = nornir_pools.GetThreadPool("segtrain-remote-tiles", num_threads=self._threads)

        def submit(job: tuple[str, dict[str, str], bool]) -> Any:
            return pool.add_task(f"remote-tile-{job[0]}", work, job)

        for task in submit_bounded(submit, jobs, max_in_flight=self._threads * 2):
            name, outcome = task.wait_return()
            results[name] = outcome
        return results

    def _plausible(self, response: HttpResponse) -> bool:
        """A tile body that is empty, or an HTML error page where a PNG belongs, is not stored."""
        if not response.body:
            return False
        if self.tileset.postfix.lower().endswith(".png"):
            return response.body.startswith(PNG_SIGNATURE)
        return True

    @staticmethod
    def _store(path: Path, body: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temp.write_bytes(body)
        os.replace(temp, path)


# --------------------------------------------------------------------------- session


class RemoteSession:
    """One run: the loaded VikingXML, whether it matches the cache, and a source per tileset."""

    def __init__(
        self,
        volume: RemoteVolume,
        cache_root: str | os.PathLike[str],
        *,
        http_get: HttpGet | None = None,
        revalidate: Revalidate | None = None,
        threads: int = DEFAULT_FETCH_THREADS,
    ) -> None:
        self.volume = volume
        self.cache_root = Path(cache_root)
        self._http = http_get
        self._policy = revalidate or Revalidate()
        self._threads = threads
        self.volume_unchanged = self._matches_saved_state()
        self.sources: list[RemoteTileSource] = []

    def _matches_saved_state(self) -> bool:
        """Whether the saved volume state names this volume's source and its first known checksum, etag, or lastModified."""
        saved = read_json_object(self.cache_root / VOLUME_STATE_NAME)
        if saved is None:
            return False
        mine = self.volume.fingerprint()
        if saved.get("source") != mine["source"]:
            return False
        for key in ("checksum", "etag", "lastModified"):
            if mine[key]:
                return saved.get(key) == mine[key]
        return False

    def source_for(self, tileset: RemoteTileset) -> RemoteTileSource:
        source = RemoteTileSource(
            self.volume,
            tileset,
            self.cache_root,
            http_get=self._http,
            revalidate=self._policy,
            volume_unchanged=self.volume_unchanged,
            threads=self._threads,
        )
        self.sources.append(source)
        return source

    def finish(self) -> None:
        """Remember this VikingXML, so the next run can trust the cache if it is unchanged.

        Written only after a clean run: an interrupted run leaves the old state, so the next one
        revalidates what the interrupted one did not get to.
        """
        self.cache_root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.cache_root / VOLUME_STATE_NAME, json.dumps(self.volume.fingerprint()))

    def summary(self) -> str:
        requests = sum(item.requests for item in self.sources)
        downloaded = sum(item.downloaded for item in self.sources)
        current = sum(item.not_modified for item in self.sources)
        return (
            f"remote tiles: {requests} request(s), {downloaded} downloaded, {current} unchanged (304)"
            + (", volume unchanged" if self.volume_unchanged else "")
        )
