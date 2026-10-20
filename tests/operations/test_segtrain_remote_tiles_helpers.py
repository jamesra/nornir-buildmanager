"""Characterization tests for small ``remote_tiles`` helpers: URL joining, volume accessors, VikingXML loading, tile plausibility."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nornir_buildmanager.operations.segmentationtraining.remote_tiles import (
    PNG_SIGNATURE,
    HttpResponse,
    RemoteTileSource,
    _join_url,
    load_viking_xml,
    parse_viking_xml,
)

VIKING_XML = """<?xml version="1.0" encoding="utf-8"?>
<Volume Version="2" Name="Lab" InputChecksum="abc123" path="http://lab.example/vol">
  <Sections>
    <Section Number="7" Path="sec7" Name="7">
      <Tileset path="TEM/Leveled/Tileset" name="TEM.Leveled" TileXDim="8" TileYDim="8"
               FilePrefix="" FilePostfix="{postfix}" CoordFormat="03d">
        <Level path="001" Downsample="1" GridDimX="4" GridDimY="4" />
        <Level path="002" Downsample="2" GridDimX="2" GridDimY="2" />
      </Tileset>
    </Section>
    <Section Number="3" Path="sec3" Name="3">
      <Tileset path="TEM/Raw/Tileset" name="TEM.Raw" TileXDim="8" TileYDim="8"
               FilePrefix="" FilePostfix=".png" CoordFormat="03d">
        <Level path="001" Downsample="1" GridDimX="1" GridDimY="1" />
      </Tileset>
    </Section>
  </Sections>
</Volume>
"""


def _xml(postfix: str = ".png") -> str:
    return VIKING_XML.format(postfix=postfix)


# ---------------------------------------------------------------------- _join_url


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (("http://h/vol", "sec1", "a", "001"), "http://h/vol/sec1/a/001"),
        (("http://h/vol/", "/sec1/", "/001/"), "http://h/vol/sec1/001"),
        (("http://h/vol", "", "001"), "http://h/vol/001"),
        (("http://h/vol", "TEM\\Leveled", "001"), "http://h/vol/TEM/Leveled/001"),
        (("/abs/root/", "x"), "/abs/root/x"),
        (("only",), "only"),
        (("", ""), ""),
        ((), ""),
    ],
)
def test_join_url(parts: tuple[str, ...], expected: str) -> None:
    assert _join_url(*parts) == expected


def test_join_url_keeps_the_scheme_double_slash_of_the_first_part() -> None:
    assert _join_url("https://h", "a").startswith("https://h/")


# ---------------------------------------------------------------------- volume accessors


def test_sections_are_sorted_and_level_url_joins_base_section_tileset_and_level() -> None:
    volume = parse_viking_xml(_xml(), source="http://lab.example/vol/x.vikingxml")
    assert volume.sections() == [3, 7]
    tileset = volume.tilesets[7][0]
    assert tileset.level_url(volume.base_url, 1) == "http://lab.example/vol/sec7/TEM/Leveled/Tileset/001"
    assert tileset.level_url("http://other/", 2) == "http://other/sec7/TEM/Leveled/Tileset/002"
    with pytest.raises(KeyError):
        tileset.level_url(volume.base_url, 4)


def test_fingerprint_carries_source_checksum_and_validators() -> None:
    volume = parse_viking_xml(_xml(), source="src", etag='"e1"', last_modified="Mon, 01 Jan 2024 00:00:00 GMT")
    assert volume.fingerprint() == {
        "source": "src",
        "checksum": "abc123",
        "etag": '"e1"',
        "lastModified": "Mon, 01 Jan 2024 00:00:00 GMT",
    }


# ---------------------------------------------------------------------- load_viking_xml


def test_load_viking_xml_over_http_strips_bom_and_records_validators() -> None:
    seen: list[tuple[str, object]] = []

    def fake_get(url: str, headers=None) -> HttpResponse:
        seen.append((url, headers))
        return HttpResponse(
            200,
            {"etag": '"abc"', "last-modified": "Tue, 02 Jan 2024 00:00:00 GMT"},
            b"\xef\xbb\xbf" + _xml().encode("utf-8"),
        )

    volume = load_viking_xml("HTTP://lab.example/vol/Volume.VikingXML", http_get=fake_get)
    assert seen == [("HTTP://lab.example/vol/Volume.VikingXML", None)]
    assert volume.name == "Lab"
    assert volume.etag == '"abc"'
    assert volume.last_modified == "Tue, 02 Jan 2024 00:00:00 GMT"
    assert volume.source == "HTTP://lab.example/vol/Volume.VikingXML"


def test_load_viking_xml_host_overrides_the_volume_path() -> None:
    def fake_get(url: str, headers=None) -> HttpResponse:
        return HttpResponse(200, {}, _xml().encode("utf-8"))

    volume = load_viking_xml("http://lab.example/v.xml", host="http://mirror.example/x", http_get=fake_get)
    assert volume.base_url == "http://mirror.example/x"
    assert volume.etag == ""


@pytest.mark.parametrize("status", [404, 304, 403])
def test_load_viking_xml_non_200_raises_oserror(status: int) -> None:
    def fake_get(url: str, headers=None) -> HttpResponse:
        return HttpResponse(status, {}, b"")

    with pytest.raises(OSError, match=str(status)):
        load_viking_xml("http://lab.example/v.xml", http_get=fake_get)


def test_load_viking_xml_local_file_stamps_last_modified_from_mtime(tmp_path: Path) -> None:
    path = tmp_path / "Volume.VikingXML"
    path.write_text(_xml(), encoding="utf-8-sig")
    os.utime(path, (0, 86400 * 365))  # 1971-01-01 00:00:00 UTC
    volume = load_viking_xml(str(path))
    assert volume.last_modified == "Fri, 01 Jan 1971 00:00:00 GMT"
    assert volume.etag == ""
    assert volume.source == str(path)
    assert volume.sections() == [3, 7]


def test_load_viking_xml_missing_local_file_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        load_viking_xml(str(tmp_path / "absent.xml"))


# ---------------------------------------------------------------------- RemoteTileSource._plausible


def _source(tmp_path: Path, postfix: str) -> RemoteTileSource:
    volume = parse_viking_xml(_xml(postfix), source="http://lab.example/vol/x.vikingxml")
    return RemoteTileSource(volume, volume.tilesets[7][0], tmp_path, http_get=lambda url, headers=None: HttpResponse(404, {}))


@pytest.mark.parametrize(
    ("postfix", "body", "expected"),
    [
        (".png", b"", False),
        (".png", PNG_SIGNATURE + b"data", True),
        (".png", b"<html>404 Not Found</html>", False),
        (".PNG", PNG_SIGNATURE, True),
        (".PNG", b"<html>", False),
        (".jpg", b"<html>", True),
        (".jpg", b"", False),
    ],
)
def test_plausible(tmp_path: Path, postfix: str, body: bytes, expected: bool) -> None:
    source = _source(tmp_path, postfix)
    assert source._plausible(HttpResponse(200, {}, body)) is expected
