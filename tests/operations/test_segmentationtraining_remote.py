"""Remote (VikingXML + HTTP) tile source for ExportTrainingData."""

from __future__ import annotations

import functools
import http.server
import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest
from PIL import Image

from nornir_buildmanager.operations.segmentationtraining.workdir import work_root
from nornir_buildmanager.operations.segmentationtraining.ingest import (
    ingest_to_section_files,
)
from nornir_buildmanager.operations.segmentationtraining.pipeline import (
    ExportRemoteSections,
    ExportSectionCrops,
)
from nornir_buildmanager.operations.segmentationtraining.remote_tiles import (
    HttpResponse,
    RemoteSession,
    RemoteTileSource,
    Revalidate,
    parse_listing,
    parse_revalidate,
    parse_viking_xml,
)
from nornir_buildmanager.operations.segmentationtraining.stitch import tile_filename

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 24

VIKING_XML = """<?xml version="1.0" encoding="utf-8"?>
<Volume Version="2" Name="Remote Lab" InputChecksum="abc123" num_sections="2" path="{base}">
  <Scale UnitsOfMeasure="nm" UnitsPerPixel="2.18" />
  <Sections>
    <Section Number="1" Path="sec1" Name="1">
      <Tileset path="TEM/Leveled/Tileset" name="TEM.Leveled" TileXDim="8" TileYDim="8"
               FilePrefix="" FilePostfix=".png" CoordFormat="03d">
        <Level path="001" Downsample="1" GridDimX="4" GridDimY="4" />
        <Level path="002" Downsample="2" GridDimX="2" GridDimY="2" />
      </Tileset>
      <Tileset path="TEM/Raw/Tileset" name="TEM.Raw" TileXDim="8" TileYDim="8"
               FilePrefix="" FilePostfix=".png" CoordFormat="03d">
        <Level path="001" Downsample="1" GridDimX="4" GridDimY="4" />
      </Tileset>
    </Section>
    <Section Number="2" Path="sec2" Name="2" />
  </Sections>
</Volume>
"""


def _volume(base: str = "http://lab.example/vol", **kwargs):
    return parse_viking_xml(VIKING_XML.format(base=base), source="http://lab.example/vol/x.vikingxml", **kwargs)


# ---------------------------------------------------------------------- parsing


def test_parse_viking_xml_reads_sections_tilesets_and_levels() -> None:
    volume = _volume()
    assert volume.name == "Remote Lab"
    assert volume.checksum == "abc123"
    assert volume.sections() == [1]  # section 2 has no tileset
    leveled, raw = volume.tilesets[1]
    assert (leveled.name, leveled.channel, leveled.filter) == ("TEM.Leveled", "TEM", "Leveled")
    assert raw.filter == "Raw"
    assert (leveled.tile_x, leveled.tile_y, leveled.prefix, leveled.postfix) == (8, 8, "", ".png")
    assert sorted(leveled.levels) == [1, 2]
    assert (leveled.levels[2].grid_x, leveled.levels[2].grid_y) == (2, 2)
    assert leveled.level_url(volume.base_url, 2) == "http://lab.example/vol/sec1/TEM/Leveled/Tileset/002"


def test_tile_host_replaces_the_base_and_a_missing_path_falls_back_to_the_xml_folder() -> None:
    assert _volume(host="http://tiles.example/data/").base_url == "http://tiles.example/data/".strip()
    bare = parse_viking_xml('<Volume Version="2" Name="V"/>', source="http://lab.example/v/vol.vikingxml")
    assert bare.base_url == "http://lab.example/v"


def test_parse_rejects_non_viking_xml() -> None:
    with pytest.raises(ValueError):
        parse_viking_xml("<Other/>", source="x")
    with pytest.raises(ValueError):
        parse_viking_xml("not xml", source="x")


def test_parse_revalidate() -> None:
    assert parse_revalidate(None) == Revalidate("once")
    assert parse_revalidate("Always") == Revalidate("always")
    assert parse_revalidate("never") == Revalidate("never")
    assert parse_revalidate("6") == Revalidate("hours", 6.0)
    with pytest.raises(ValueError):
        parse_revalidate("soon")
    with pytest.raises(ValueError):
        parse_revalidate("-1")


def test_parse_listing_iis_nginx_apache_and_json() -> None:
    iis = (
        b'<pre><A HREF="/vol/">[To Parent Directory]</A><br><br>'
        b' 5/21/2023  3:13 PM        12345 <A HREF="/vol/X000_Y000.png">X000_Y000.png</A><br>'
        b' 5/22/2023  4:00 PM          999 <A HREF="/vol/X001_Y000.png">X001_Y000.png</A><br></pre>'
    )
    listed = parse_listing(iis, "text/html", ".png")
    assert listed["X000_Y000.png"].size == 12345 and listed["X000_Y000.png"].stamp == "5/21/2023  3:13 PM"
    assert listed["X001_Y000.png"].size == 999
    nginx = (
        b'<a href="X000_Y000.png">X000_Y000.png</a>              01-May-2023 12:00     777\n'
        b'<a href="../">../</a>\n'
    )
    assert parse_listing(nginx, "text/html", ".png")["X000_Y000.png"].size == 777
    apache = b'<a href="X000_Y000.png">X000_Y000.png</a>   2023-05-01 12:00   12K\n'
    entry = parse_listing(apache, "text/html", ".png")["X000_Y000.png"]
    assert entry.size is None and entry.stamp == "2023-05-01 12:00"
    as_json = json.dumps([{"name": "X000_Y000.png", "size": 5}, {"name": "readme.txt", "size": 1}]).encode()
    assert parse_listing(as_json, "application/json", ".png") == {"X000_Y000.png": parse_listing(as_json, "", ".png")["X000_Y000.png"]}
    assert parse_listing(b"<html>nothing here</html>", "text/html", ".png") == {}


# ---------------------------------------------------------------------- cache logic


class FakeServer:
    """In-memory tile server that records requests. ``listing`` and ``etags`` switch features on."""

    def __init__(self, volume, tiles: set[tuple[int, int]], *, listing: bool = False, etags: bool = True) -> None:
        self.level_url = volume.tilesets[1][0].level_url(volume.base_url, 1)
        self.tiles = {tile_filename("", ".png", ix, iy): PNG + bytes([ix, iy]) for ix, iy in tiles}
        self.listing = listing
        self.etags = etags
        self.version = 1
        self.requests: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers=None) -> HttpResponse:
        headers = dict(headers or {})
        self.requests.append((url, headers))
        if url == self.level_url + "/":
            if not self.listing:
                return HttpResponse(404, {})
            rows = "".join(f'<a href="{name}">{name}</a>   01-May-2023 12:00   {len(body)}\n' for name, body in self.tiles.items())
            return HttpResponse(200, {"content-type": "text/html"}, rows.encode())
        name = url.rsplit("/", 1)[-1]
        if not url.startswith(self.level_url + "/") or name not in self.tiles:
            return HttpResponse(404, {})
        etag = f'"{name}-v{self.version}"'
        if self.etags and headers.get("If-None-Match") == etag:
            return HttpResponse(304, {"etag": etag})
        return HttpResponse(200, {"etag": etag} if self.etags else {}, self.tiles[name])

    def tile_requests(self) -> list[str]:
        return [url for url, _ in self.requests if url.endswith(".png")]


def _source(tmp_path: Path, server: FakeServer, volume, **kwargs) -> RemoteTileSource:
    return RemoteTileSource(volume, volume.tilesets[1][0], tmp_path / "cache", http_get=server, threads=1, **kwargs)


GRID = {(ix, iy) for ix in range(4) for iy in range(4)}


def test_fetch_downloads_only_requested_tiles_inside_the_grid(tmp_path: Path) -> None:
    volume = _volume()
    server = FakeServer(volume, GRID)
    source = _source(tmp_path, server, volume)
    source.fetch(1, [(0, 0), (1, 0), (-1, 0), (0, -2), (4, 0), (0, 4), (1, 0)])
    level = Path(source.level_dirs[1])
    assert sorted(path.name for path in level.glob("*.png")) == ["X000_Y000.png", "X001_Y000.png"]
    assert len(server.tile_requests()) == 2  # negatives, off-grid and the repeat make no request
    assert source.downloaded == 2


def test_missing_tile_is_left_absent_and_not_asked_for_twice(tmp_path: Path) -> None:
    volume = _volume()
    server = FakeServer(volume, GRID - {(2, 2)})
    source = _source(tmp_path, server, volume)
    source.fetch(1, [(2, 2), (0, 0)])
    source.fetch(1, [(2, 2)])
    level = Path(source.level_dirs[1])
    assert not (level / "X002_Y002.png").exists()
    assert sum(url.endswith("X002_Y002.png") for url in server.tile_requests()) == 1


def test_non_png_body_is_not_stored(tmp_path: Path) -> None:
    volume = _volume()
    server = FakeServer(volume, GRID)
    server.tiles[tile_filename("", ".png", 0, 0)] = b"<html>error</html>"
    source = _source(tmp_path, server, volume)
    source.fetch(1, [(0, 0)])
    assert not (Path(source.level_dirs[1]) / "X000_Y000.png").exists()


def test_second_run_validates_with_conditional_gets_and_gets_304s(tmp_path: Path) -> None:
    volume = _volume()
    server = FakeServer(volume, GRID)
    _source(tmp_path, server, volume).fetch(1, [(0, 0), (1, 1)])
    server.requests.clear()
    again = _source(tmp_path, server, volume)  # a new run: policy "once", nothing validated yet
    again.fetch(1, [(0, 0), (1, 1)])
    assert len(server.tile_requests()) == 2
    assert all("If-None-Match" in headers for url, headers in server.requests if url.endswith(".png"))
    assert (again.downloaded, again.not_modified) == (0, 2)


def test_changed_tile_is_downloaded_again(tmp_path: Path) -> None:
    volume = _volume()
    server = FakeServer(volume, GRID)
    _source(tmp_path, server, volume).fetch(1, [(0, 0)])
    server.version = 2
    server.tiles["X000_Y000.png"] = PNG + b"new"
    again = _source(tmp_path, server, volume)
    again.fetch(1, [(0, 0)])
    assert again.downloaded == 1
    assert (Path(again.level_dirs[1]) / "X000_Y000.png").read_bytes().endswith(b"new")


def test_unchanged_volume_makes_no_tile_request(tmp_path: Path) -> None:
    volume = _volume()
    server = FakeServer(volume, GRID)
    first = RemoteSession(volume, tmp_path / "cache", http_get=server, revalidate=Revalidate("once"))
    first.source_for(volume.tilesets[1][0]).fetch(1, [(0, 0)])
    first.finish()
    server.requests.clear()
    second = RemoteSession(volume, tmp_path / "cache", http_get=server)
    assert second.volume_unchanged
    source = second.source_for(volume.tilesets[1][0])
    source.fetch(1, [(0, 0), (1, 0)])  # one cached, one new
    assert [url.rsplit("/", 1)[-1] for url in server.tile_requests()] == ["X001_Y000.png"]


def test_changed_volume_is_revalidated_and_interrupted_run_leaves_old_state(tmp_path: Path) -> None:
    volume = _volume()
    server = FakeServer(volume, GRID)
    first = RemoteSession(volume, tmp_path / "cache", http_get=server)
    first.source_for(volume.tilesets[1][0]).fetch(1, [(0, 0)])
    first.finish()
    changed = parse_viking_xml(VIKING_XML.replace("abc123", "zzz").format(base="http://lab.example/vol"), source=volume.source)
    second = RemoteSession(changed, tmp_path / "cache", http_get=server)
    assert not second.volume_unchanged
    assert RemoteSession(volume, tmp_path / "cache", http_get=server).volume_unchanged  # state untouched until finish()


def test_never_trusts_and_always_distrusts_the_cache(tmp_path: Path) -> None:
    volume = _volume()
    server = FakeServer(volume, GRID)
    _source(tmp_path, server, volume).fetch(1, [(0, 0)])
    server.requests.clear()
    _source(tmp_path, server, volume, revalidate=Revalidate("never")).fetch(1, [(0, 0)])
    assert server.tile_requests() == []
    always = _source(tmp_path, server, volume, revalidate=Revalidate("always"))
    always.fetch(1, [(0, 0)])
    always.fetch(1, [(0, 0)])
    assert len(server.tile_requests()) == 2


def test_hours_policy_skips_a_recently_validated_level(tmp_path: Path) -> None:
    volume = _volume()
    server = FakeServer(volume, GRID)
    now = [1_000_000.0]
    _source(tmp_path, server, volume, clock=lambda: now[0]).fetch(1, [(0, 0)])
    server.requests.clear()
    now[0] += 3600
    recent = _source(tmp_path, server, volume, revalidate=Revalidate("hours", 6.0), clock=lambda: now[0])
    recent.fetch(1, [(0, 0)])
    assert server.tile_requests() == []
    now[0] += 7 * 3600
    stale = _source(tmp_path, server, volume, revalidate=Revalidate("hours", 6.0), clock=lambda: now[0])
    stale.fetch(1, [(0, 0)])
    assert len(server.tile_requests()) == 1


def test_directory_listing_validates_a_level_without_per_tile_requests(tmp_path: Path) -> None:
    volume = _volume()
    server = FakeServer(volume, GRID, listing=True)
    _source(tmp_path, server, volume).fetch(1, [(0, 0), (1, 0), (2, 0)])
    server.requests.clear()
    again = _source(tmp_path, server, volume)
    again.fetch(1, [(0, 0), (1, 0), (2, 0)])
    assert server.tile_requests() == []  # the listing alone showed nothing changed
    assert len(server.requests) == 1
    # A tile that changed size on the server is the only one fetched again.
    server.tiles["X001_Y000.png"] = PNG + b"a-longer-body"
    third = _source(tmp_path, server, volume)
    third.fetch(1, [(0, 0), (1, 0), (2, 0)])
    assert [url.rsplit("/", 1)[-1] for url in server.tile_requests()] == ["X001_Y000.png"]
    # A tile removed from the server drops out of the cache.
    del server.tiles["X002_Y000.png"]
    fourth = _source(tmp_path, server, volume)
    fourth.fetch(1, [(2, 0)])
    assert not (Path(fourth.level_dirs[1]) / "X002_Y000.png").exists()


def test_server_without_validators_refetches_the_tiles_it_is_asked_for(tmp_path: Path) -> None:
    volume = _volume()
    server = FakeServer(volume, GRID, etags=False)
    _source(tmp_path, server, volume).fetch(1, [(0, 0)])
    server.requests.clear()
    again = _source(tmp_path, server, volume)
    again.fetch(1, [(0, 0)])
    assert again.downloaded == 1 and len(server.tile_requests()) == 1


def test_parallel_fetch_matches_serial(tmp_path: Path) -> None:
    volume = _volume()
    server = FakeServer(volume, GRID)
    source = RemoteTileSource(volume, volume.tilesets[1][0], tmp_path / "cache", http_get=server, threads=4)
    source.fetch(1, sorted(GRID))
    assert len(list(Path(source.level_dirs[1]).glob("*.png"))) == 16
    assert source.downloaded == 16


# ---------------------------------------------------------------------- end to end


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Static files with a request log; Python's handler supplies Last-Modified and 304s."""

    log: ClassVar[list[str]] = []

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        type(self).log.append(self.path)
        super().do_GET()


@pytest.fixture()
def lab(tmp_path: Path) -> Iterator[dict]:
    """A one-section remote volume (4x4 tiles of 8 px) served over HTTP, plus its annotation dump."""
    site = tmp_path / "site"
    level = site / "vol" / "sec1" / "TEM" / "Leveled" / "Tileset" / "001"
    level.mkdir(parents=True)
    rng = np.random.default_rng(3)
    for ix in range(4):
        for iy in range(4):
            Image.fromarray(rng.integers(20, 235, (8, 8), dtype=np.uint8), mode="L").save(level / tile_filename("", ".png", ix, iy))
    handler_log: list[str] = []
    handler = type("H", (_Handler,), {"log": handler_log})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(handler, directory=str(site)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/vol"
    (site / "vol" / "volume.vikingxml").write_text(
        VIKING_XML.format(base=base).replace('Name="Remote Lab"', 'Name="RemoteLab"'), encoding="utf-8"
    )
    entity = {
        "ID": 8,
        "Z": 1,
        "ParentID": 10,
        "TypeCode": 6,
        "OffEdge": False,
        "LastModified": "2020-01-01T00:00:00Z",
        "MosaicShape": {"Geometry": {"WellKnownText": "POLYGON((9 9,14 9,14 14,9 14,9 9))"}},
        "Parent": {"ID": 10, "TypeID": 1, "Label": None, "Type": {"ID": 1, "Name": "Cell", "ParentID": None}},
    }
    dump = tmp_path / "dump.jsonl"
    dump.write_text(json.dumps(entity) + "\n", encoding="utf-8")
    try:
        yield {
            "url": f"{base}/volume.vikingxml",
            "dump": dump,
            "level": level,
            "log": handler_log,
            "tmp": tmp_path,
        }
    finally:
        server.shutdown()
        server.server_close()


def _export_args(**extra):
    return dict(Pad=0.0, Downsample=1, MaxTexture=16, Channels="TEM", Filters="Leveled", Workers=1, MaskWorkers=1, **extra)


def _images(output: Path) -> list[bytes]:
    return sorted(path.read_bytes() for path in (output / "images").glob("*.png"))


def _tile_requests(log: list[str]) -> list[str]:
    return [path for path in log if path.endswith(".png")]


def test_remote_export_matches_a_local_volume_export(lab: dict) -> None:
    remote_out = lab["tmp"] / "remote_out"
    ingest_to_section_files(output_path=remote_out, odata=None, geometries=lab["dump"])
    ExportRemoteSections(OutputPath=str(remote_out), VikingXML=lab["url"], **_export_args())
    local_out = lab["tmp"] / "local_out"
    ingest_to_section_files(output_path=local_out, odata=None, geometries=lab["dump"])
    ExportSectionCrops(
        OutputPath=str(local_out), VolumeName="RemoteLab", Z=1, LevelDirs={1: str(lab["level"])}, TileXDim=8, TileYDim=8,
        FilePrefix="", FilePostfix=".png", AvailableDownsamples=[1], **_export_args(),
    )
    assert _images(remote_out), "the remote export wrote no crops"
    assert _images(remote_out) == _images(local_out)
    assert sorted(path.name[-6:] for path in (remote_out / "masks").glob("*.png")) == sorted(
        path.name[-6:] for path in (local_out / "masks").glob("*.png")
    )
    source = json.loads((remote_out / "source.json").read_text(encoding="utf-8"))
    assert source["tiles"]["vikingXml"] == lab["url"]
    # Only tiles under the annotation were fetched, not the whole 16-tile level.
    fetched = list((work_root(remote_out) / "remote_tiles").rglob("*.png"))
    assert 0 < len(fetched) < 16
    assert not (remote_out / "_work").exists()
    assert any("RemoteLab" in path.name for path in (remote_out / "images").glob("*.png"))


def test_second_remote_run_makes_no_tile_requests(lab: dict) -> None:
    out = lab["tmp"] / "out"
    ingest_to_section_files(output_path=out, odata=None, geometries=lab["dump"])
    ExportRemoteSections(OutputPath=str(out), VikingXML=lab["url"], **_export_args())
    assert _tile_requests(lab["log"])
    lab["log"].clear()
    ExportRemoteSections(OutputPath=str(out), VikingXML=lab["url"], **_export_args())
    assert _tile_requests(lab["log"]) == []
    # -Update, which walks the recorded crops' tiles again, is served from the same trusted cache.
    ExportRemoteSections(OutputPath=str(out), VikingXML=lab["url"], Update=True, **_export_args())
    assert _tile_requests(lab["log"]) == []


def test_missing_remote_tile_drops_the_crop_without_failing(lab: dict) -> None:
    for path in lab["level"].glob("*.png"):
        path.unlink()
    out = lab["tmp"] / "out"
    ingest_to_section_files(output_path=out, odata=None, geometries=lab["dump"])
    ExportRemoteSections(OutputPath=str(out), VikingXML=lab["url"], **_export_args())
    assert _images(out) == []


def test_sections_filter_keeps_only_the_named_sections(lab: dict) -> None:
    named, other, unfiltered = lab["tmp"] / "named", lab["tmp"] / "other", lab["tmp"] / "unfiltered"
    for out, sections in ((named, "1"), (other, "2,5-7"), (unfiltered, None)):
        ingest_to_section_files(output_path=out, odata=None, geometries=lab["dump"])
        extra = {} if sections is None else {"Sections": sections}
        ExportRemoteSections(OutputPath=str(out), VikingXML=lab["url"], **_export_args(**extra))
    assert _images(unfiltered), "the unfiltered export wrote no crops"
    assert _images(named) == _images(unfiltered)
    assert _images(other) == []


def test_no_viking_xml_is_a_no_op(tmp_path: Path) -> None:
    assert ExportRemoteSections(OutputPath=str(tmp_path / "x"), VikingXML=None) is None
    assert ExportRemoteSections(OutputPath=str(tmp_path / "x"), VikingXML="", Cleanup=True) is None
    assert not (tmp_path / "x").exists()


def test_unreadable_viking_xml_is_a_user_error(tmp_path: Path) -> None:
    from nornir_buildmanager.exceptions import NornirUserException

    with pytest.raises(NornirUserException):
        ExportRemoteSections(OutputPath=str(tmp_path / "x"), VikingXML=str(tmp_path / "missing.vikingxml"))
    (tmp_path / "bad.vikingxml").write_text("<Other/>", encoding="utf-8")
    with pytest.raises(NornirUserException):
        ExportRemoteSections(OutputPath=str(tmp_path / "x"), VikingXML=str(tmp_path / "bad.vikingxml"))


def test_pipeline_xml_declares_the_remote_arguments_and_call() -> None:
    from xml.etree import ElementTree

    pipelines = Path(__file__).resolve().parents[2] / "nornir_buildmanager" / "config" / "Pipelines.xml"
    pipeline = ElementTree.parse(pipelines).find(".//Pipeline[@Name='ExportTrainingData']")
    assert pipeline is not None
    flags = {argument.get("flag"): argument for argument in pipeline.findall(".//Argument")}
    for flag in ("-VikingXML", "-TileHost", "-TileCache", "-TileRevalidate"):
        assert flag in flags and flags[flag].get("required") == "False"
    call = pipeline.find(".//PythonCall[@Function='segmentationtraining.ExportRemoteSections']")
    assert call is not None and call.get("VikingXML") == "#VikingXML" and call.get("TileCache") == "#TileCache"
    from nornir_buildmanager.operations.segmentationtraining import (
        ExportRemoteSections as exported,
    )

    assert exported is ExportRemoteSections
    assert os.path.isfile(str(pipelines))


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("SEM.Leveled", "SEM\Leveled\Tileset"),
        ("Leveled", "SEM\Leveled\Tileset"),
        ("Leveled", "SEM/Leveled/Tileset"),
    ],
)
def test_tileset_channel_and_filter_from_name_or_path(name: str, path: str) -> None:
    """A tileset named just ``Leveled`` takes its channel and filter from its path."""
    from nornir_buildmanager.operations.segmentationtraining.remote_tiles import RemoteTileset

    tileset = RemoteTileset(1, "", name, path, 512, 512, "", ".png", {})
    assert (tileset.channel, tileset.filter) == ("SEM", "Leveled")
