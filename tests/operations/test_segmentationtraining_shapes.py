"""shapes.jsonl and the manifest's structure/link fields (mask-refinement plan, Phase B)."""

from __future__ import annotations

import json
import os
from datetime import UTC
from pathlib import Path
from urllib.parse import unquote

import pytest

from nornir_buildmanager.operations.segmentationtraining.ingest import (
    ingest_to_section_files,
)
from nornir_buildmanager.operations.segmentationtraining.records import (
    LocationRecord,
    LocationType,
)
from nornir_buildmanager.operations.segmentationtraining.shapes import (
    LINK_MAX_DZ,
    SHAPES_FILENAME,
    WriteLocationShapes,
    location_shape,
    polygons_area_and_centroid,
    write_location_shapes,
)

ODATA = "http://lab.example/RC1/OData"


def _box(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def _entity(loc_id: int, z: int, structure: int, wkt: str, label: str | None = "BC") -> dict:
    return {
        "ID": loc_id,
        "Z": z,
        "ParentID": structure,
        "TypeCode": LocationType.POLYGON,
        "OffEdge": False,
        "LastModified": "2020-01-01T00:00:00Z",
        "MosaicShape": {"Geometry": {"WellKnownText": wkt}},
        "Parent": {"ID": structure, "TypeID": 1, "Label": label, "Type": {"ID": 1, "Name": "Cell", "ParentID": None}},
    }


class LinkServer:
    """``http_get`` for the export's Locations with their links expanded; counts requests.

    Like the Viking service it caps a page (``page_cap``) and sends no ``@odata.nextLink``.
    """

    def __init__(self, links: list[tuple[int, int]], page_cap: int = 2) -> None:
        self.links = links
        self.page_cap = page_cap
        self.calls: list[str] = []

    def __call__(self, url: str) -> dict:
        self.calls.append(unquote(url))
        query = dict(part.split("=", 1) for part in unquote(url).split("?", 1)[1].split("&") if "=" in part)
        skip = int(query.get("$skip", 0))
        top = min(int(query["$top"]), self.page_cap)
        ids = sorted({end for pair in self.links for end in pair})
        entities = [
            {
                "ID": loc,
                "LocationLinksA": [{"A": a, "B": b} for a, b in self.links if a == loc],
                "LocationLinksB": [{"A": a, "B": b} for a, b in self.links if b == loc],
            }
            for loc in ids
        ]
        return {"@odata.count": len(entities), "value": entities[skip : skip + top]}


def _volume(tmp_path: Path, *, odata: bool = True) -> Path:
    """Structure 77 on sections 1-3 (locations 11, 12, 13); structure 88 on section 2 (location 21)."""
    out = tmp_path / "AnnotationCrops"
    entities = [
        _entity(11, 1, 77, _box(100, 200, 110, 210)),
        _entity(12, 2, 77, _box(102, 202, 112, 214)),
        _entity(13, 3, 77, _box(104, 204, 110, 210)),
        _entity(14, 8, 77, _box(0, 0, 4, 4)),
        _entity(21, 2, 88, _box(300, 300, 320, 320), label=None),
    ]
    dump = tmp_path / "dump.jsonl"
    dump.write_text("\n".join(json.dumps(item) for item in entities) + "\n", encoding="utf-8")
    ingest_to_section_files(output_path=out, odata=None, geometries=dump)
    if odata:
        (out / "source.json").write_text(json.dumps({"kind": "odata", "odata": ODATA}), encoding="utf-8")
    rows = [
        {"z": 1, "volume": "V", "imageKey": "V_1_D1_X0-1024_Y0-1024", "downsample": 1, "locationIds": [11]},
        {"z": 2, "volume": "V", "imageKey": "V_2_D1_X0-1024_Y0-1024", "downsample": 1, "locationIds": [12, 21]},
    ]
    (out / "manifest.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return out


def _shapes(out: Path) -> dict[int, dict]:
    return {
        item["location_id"]: item
        for item in (json.loads(line) for line in (out / SHAPES_FILENAME).read_text(encoding="utf-8").splitlines())
    }


# ---------------------------------------------------------------------- geometry


def _record(wkt: str, type_code: int = LocationType.POLYGON) -> LocationRecord:
    from datetime import datetime

    return LocationRecord(
        id=1, z=1, wkt=wkt, parent_id=5, off_edge=False,
        last_modified=datetime(2020, 1, 1, tzinfo=UTC), type_id=1, type_name="Cell",
        structure_label="BC", type_code=type_code,
    )


def test_shape_is_relative_to_the_centroid_with_area() -> None:
    shape = location_shape(_record(_box(100, 200, 110, 210)))
    assert shape is not None
    assert shape["centroid"] == [105.0, 205.0] and shape["area"] == 100.0
    ring = shape["polygons"][0][0]
    assert sorted({tuple(point) for point in ring}) == [(-5.0, -5.0), (-5.0, 5.0), (5.0, -5.0), (5.0, 5.0)]


def test_a_hole_subtracts_from_the_area_and_moves_the_centroid() -> None:
    wkt = "POLYGON((0 0,10 0,10 10,0 10,0 0),(0 0,4 0,4 10,0 10,0 0))"
    shape = location_shape(_record(wkt))
    assert shape is not None
    assert shape["area"] == 60.0 and shape["centroid"] == [7.0, 5.0]
    assert len(shape["polygons"][0]) == 2  # exterior then hole


def test_c_shaped_polygon_centroid_may_lie_outside_but_outline_stays_consistent() -> None:
    wkt = "POLYGON((0 0,10 0,10 2,2 2,2 8,10 8,10 10,0 10,0 0))"
    area, cx, _cy = polygons_area_and_centroid([_ring_set(wkt)])
    assert area == pytest.approx(52.0)
    shape = location_shape(_record(wkt))
    xs = [point[0] for ring in shape["polygons"][0] for point in ring]
    assert min(xs) == pytest.approx(0 - cx, abs=0.01) and max(xs) == pytest.approx(10 - cx, abs=0.01)


def _ring_set(wkt: str):
    from nornir_buildmanager.operations.segmentationtraining.wkt import (
        parse_wkt_polygons,
    )

    return parse_wkt_polygons(wkt)[0]


def test_unparseable_or_empty_wkt_has_no_shape() -> None:
    assert location_shape(_record("POINT(1 2)")) is None


# ---------------------------------------------------------------------- the writer


def test_shapes_carry_structure_mask_flag_and_adjacent_links(tmp_path: Path) -> None:
    out = _volume(tmp_path)
    server = LinkServer([(11, 12), (12, 13), (11, 13), (11, 14), (12, 21), (13, 999)])
    assert write_location_shapes(out, http_get=server) == out / SHAPES_FILENAME
    shapes = _shapes(out)
    # Masked locations (11, 12, 21) and the location a target links to (13). 14 is neither, so it is left out.
    assert set(shapes) == {11, 12, 13, 21}
    assert shapes[11]["structure_id"] == 77 and shapes[11]["structure_label"] == "BC"
    assert shapes[21]["structure_label"] == "Cell"  # falls back to the type name, as the crop sidecars do
    assert shapes[11]["has_mask"] is True and shapes[13]["has_mask"] is False
    # 11-12 (dz 1) and 11-13 (dz 2) are kept. 11-14 is 7 sections apart, 12-21 is another structure and
    # 13-999 has an unknown end, so none of those links appear.
    assert shapes[11]["linked"] == [[12, 2], [13, 3]]
    assert shapes[12]["linked"] == [[11, 1], [13, 3]]
    assert shapes[21]["linked"] == []
    assert LINK_MAX_DZ == 2
    assert shapes[12]["polygons"] and shapes[12]["centroid"] == [107.0, 208.0]


def test_manifest_rows_gain_parallel_structure_and_link_lists(tmp_path: Path) -> None:
    out = _volume(tmp_path)
    write_location_shapes(out, http_get=LinkServer([(11, 12), (12, 13)]))
    rows = [json.loads(line) for line in (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["locationIds"] == [11] and rows[0]["structureIds"] == [77] and rows[0]["linkedIds"] == [[12]]
    assert rows[1]["locationIds"] == [12, 21] and rows[1]["structureIds"] == [77, 88]
    assert rows[1]["linkedIds"] == [[11, 13], []]
    assert rows[0]["imageKey"] == "V_1_D1_X0-1024_Y0-1024" and rows[0]["downsample"] == 1  # nothing else changed


def test_links_are_cached_and_refetched_only_when_needed(tmp_path: Path) -> None:
    out = _volume(tmp_path)
    server = LinkServer([(11, 12)])
    write_location_shapes(out, http_get=server)
    assert server.calls and server.calls[0].startswith(ODATA + "/Locations")
    calls = len(server.calls)
    write_location_shapes(out, http_get=server)
    assert len(server.calls) == calls  # cache hit
    write_location_shapes(out, http_get=server, refresh=True)  # -Update / -RefreshOData / -Force
    assert len(server.calls) == 2 * calls
    # A new location changes the known set, so the cache no longer covers it.
    spill = out / "_work" / "section_3.jsonl"
    extra = LocationRecord.from_json(json.loads(spill.read_text(encoding="utf-8").splitlines()[0]))
    new = json.loads(json.dumps(extra.to_json()))
    new["id"] = 500
    spill.write_text(spill.read_text(encoding="utf-8") + json.dumps(new) + "\n", encoding="utf-8")
    write_location_shapes(out, http_get=server)
    assert len(server.calls) == 3 * calls


def test_unchanged_outputs_are_not_rewritten(tmp_path: Path) -> None:
    out = _volume(tmp_path)
    write_location_shapes(out, http_get=LinkServer([(11, 12)]))
    shapes_stamp = (out / SHAPES_FILENAME).stat().st_mtime_ns
    manifest_stamp = (out / "manifest.jsonl").stat().st_mtime_ns
    write_location_shapes(out, http_get=LinkServer([(11, 12)]))
    assert (out / SHAPES_FILENAME).stat().st_mtime_ns == shapes_stamp
    assert (out / "manifest.jsonl").stat().st_mtime_ns == manifest_stamp


def test_a_geometries_export_without_an_odata_root_writes_shapes_without_links(tmp_path: Path) -> None:
    out = _volume(tmp_path, odata=False)
    server = LinkServer([(11, 12)])
    write_location_shapes(out, http_get=server)
    assert server.calls == []
    assert all(item["linked"] == [] for item in _shapes(out).values())
    assert json.loads((out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])["structureIds"] == [77]


def test_existing_crops_are_not_touched(tmp_path: Path) -> None:
    out = _volume(tmp_path)
    (out / "images").mkdir()
    (out / "images" / "V_1_D1_X0-1024_Y0-1024.png").write_bytes(b"crop")
    write_location_shapes(out, http_get=LinkServer([]))
    assert (out / "images" / "V_1_D1_X0-1024_Y0-1024.png").read_bytes() == b"crop"


def test_nothing_to_describe_writes_nothing(tmp_path: Path) -> None:
    assert write_location_shapes(tmp_path / "empty", http_get=LinkServer([])) is None


def test_pipeline_entry_and_wiring(tmp_path: Path) -> None:
    from xml.etree import ElementTree

    from nornir_buildmanager.exceptions import NornirUserException
    from nornir_buildmanager.operations.segmentationtraining import (
        WriteLocationShapes as exported,
    )

    assert exported is WriteLocationShapes
    with pytest.raises(NornirUserException):
        WriteLocationShapes(OutputPath=None)
    out = _volume(tmp_path)
    assert WriteLocationShapes(OutputPath=str(out), http_get=LinkServer([(11, 12)])) is None
    assert _shapes(out)[11]["linked"] == [[12, 2]]
    pipelines = Path(__file__).resolve().parents[2] / "nornir_buildmanager" / "config" / "Pipelines.xml"
    pipeline = ElementTree.parse(pipelines).find(".//Pipeline[@Name='ExportTrainingData']")
    call = pipeline.find(".//PythonCall[@Function='segmentationtraining.WriteLocationShapes']")
    assert call is not None and call.get("OutputPath") == "#OutputPath" and call.get("Update") == "#Update"
    assert os.path.isfile(str(pipelines))


def test_manifest_locations_without_an_ingest_record_are_tolerated(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    out = _volume(tmp_path)
    rows = (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    gone = json.loads(rows[0])
    gone["locationIds"] = [11, 9999]
    (out / "manifest.jsonl").write_text(json.dumps(gone) + chr(10) + rows[1] + chr(10), encoding="utf-8")
    with caplog.at_level("WARNING"):
        write_location_shapes(out, http_get=LinkServer([]))
    assert 9999 not in _shapes(out) and 11 in _shapes(out)
    assert "1 location(s) in the manifest have no current ingest record" in caplog.text
    first = json.loads((out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first["structureIds"] == [77, None]


def test_simplification_keeps_the_shape_within_the_tolerance() -> None:
    import math

    from nornir_buildmanager.operations.segmentationtraining.shapes import simplify_ring

    circle = tuple((100 * math.cos(t / 200 * 2 * math.pi), 100 * math.sin(t / 200 * 2 * math.pi)) for t in range(201))
    simple = simplify_ring(circle, 0.75)
    assert 8 <= len(simple) < len(circle) / 4 and simple[0] == simple[-1]
    assert all(abs(math.hypot(x, y) - 100) < 1.0 for x, y in simple)  # still on the circle
    square = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0))
    assert simplify_ring(square, 0.75) == square  # four corners cannot be reduced


def test_links_are_read_past_the_servers_page_cap(tmp_path: Path) -> None:
    """A page capped below $top with no nextLink must not end the links (the 16384-row cap)."""
    out = _volume(tmp_path)
    server = LinkServer([(11, 12), (12, 13), (11, 13)], page_cap=1)
    write_location_shapes(out, http_get=server)
    assert len(server.calls) > 1 and any("$skip=" in call for call in server.calls)
    assert _shapes(out)[11]["linked"] == [[12, 2], [13, 3]]
    assert _shapes(out)[13]["linked"] == [[11, 1], [12, 2]]


def test_odata_paging_continues_with_skip_when_a_full_page_has_no_next_link() -> None:
    from nornir_buildmanager.operations.segmentationtraining.ingest import _iter_odata_pages

    rows = [{"ID": i} for i in range(7)]

    def getter(url: str) -> dict:
        skip = int(url.split("$skip=")[1].split("&")[0]) if "$skip=" in url else 0
        return {"value": rows[skip : skip + 3]}  # the server caps every page at 3 rows

    assert [r["ID"] for r in _iter_odata_pages("http://x/Locations?$top=3&$count=true", getter)] == list(range(7))
    assert [r["ID"] for r in _iter_odata_pages("http://x/Locations?$top=10", getter)] == [0, 1, 2]  # not full: done
