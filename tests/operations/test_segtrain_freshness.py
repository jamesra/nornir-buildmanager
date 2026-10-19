"""Characterization tests for ``freshness``: skip / mask-only / full decisions and watermark round trips."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nornir_buildmanager.operations.segmentationtraining.freshness import (
    ExportParams,
    ImageWatermark,
    SectionWatermark,
    _mtime_equal,
    geometry_version_for,
    id_set,
    image_output_fresh,
    image_rebuild_mode,
    load_section_watermark,
    max_last_modified,
    save_section_watermark,
    seconds_significantly_newer,
    section_is_fresh,
    section_meta_path,
)
from nornir_buildmanager.operations.segmentationtraining.product_index import (
    IMAGES_FOLDER,
    CropProductIndex,
)
from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord


def _record(record_id: int, last_modified: datetime) -> LocationRecord:
    return LocationRecord(
        id=record_id,
        z=1,
        wkt="POLYGON((0 0,1 0,1 1,0 0))",
        parent_id=None,
        off_edge=False,
        last_modified=last_modified,
        type_id=None,
        type_name=None,
        structure_label=None,
    )


def _params(**overrides: object) -> ExportParams:
    values: dict[str, object] = {
        "pad": 0.1,
        "downsample": 1,
        "max_texture": 512,
        "min_process_pixels": 16,
        "include_off_edge": False,
        "channel": "c",
        "filter_name": "f",
        "volume": "v",
    }
    values.update(overrides)
    return ExportParams(**values)  # type: ignore[arg-type]


def _mark(**overrides: object) -> ImageWatermark:
    values: dict[str, object] = {
        "key": "k",
        "downsample": 2,
        "ix0": 1,
        "ix1": 2,
        "iy0": 3,
        "iy1": 4,
        "member_ids": [5, 6],
        "max_last_modified": "t0",
    }
    values.update(overrides)
    return ImageWatermark(**values)  # type: ignore[arg-type]


def test_geometry_version_for_known_blank_and_unknown() -> None:
    assert geometry_version_for("tiled") == "fixed-d-halfstep-v1"
    assert geometry_version_for("  LEGACY ") == "coarsen-to-fit-v1"
    assert geometry_version_for("") == "fixed-d-halfstep-v1"
    assert geometry_version_for(None) == "fixed-d-halfstep-v1"  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Exporter must be"):
        geometry_version_for("bogus")


def test_export_params_hash_tracks_every_field() -> None:
    base = _params()
    assert base.hash() == _params().hash()
    assert len(base.hash()) == 16
    for name, value in (
        ("pad", 0.2),
        ("downsample", 2),
        ("max_texture", 1024),
        ("min_process_pixels", 32),
        ("include_off_edge", True),
        ("channel", "other"),
        ("filter_name", "other"),
        ("volume", "other"),
        ("geometry_version", "coarsen-to-fit-v1"),
        ("crop_format", "jpg"),
    ):
        assert _params(**{name: value}).hash() != base.hash(), name


def test_export_params_hash_tracks_tile_shape() -> None:
    from nornir_imageregistration.type_info import Shape

    assert _params(tile_shape=Shape(y=1, x=2)).hash() != _params(tile_shape=Shape(y=2, x=1)).hash()
    assert _params(tile_shape=Shape(y=1, x=2)).hash() == _params(tile_shape=Shape(y=1, x=2)).hash()


def test_max_last_modified_empty_is_datetime_min_and_otherwise_newest() -> None:
    assert max_last_modified([]) == datetime.min
    base = datetime(2020, 1, 1, tzinfo=UTC)
    records = [_record(1, base), _record(2, base + timedelta(days=3)), _record(3, base + timedelta(days=1))]
    assert max_last_modified(records) == base + timedelta(days=3)
    assert max_last_modified(iter(records[:1])) == base
    assert id_set(records) == {1, 2, 3}


def test_mtime_equal_none_handling_and_skew_tolerance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NORNIR_MTIME_SKEW_TOLERANCE_SEC", raising=False)
    assert _mtime_equal(None, None)
    assert not _mtime_equal(None, 1.0)
    assert not _mtime_equal(1.0, None)
    assert _mtime_equal(100.0, 100.0)
    assert _mtime_equal(100.0, 101.5)  # inside the 2 s default deadband
    assert not _mtime_equal(100.0, 103.0)


def test_seconds_significantly_newer_is_directional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NORNIR_MTIME_SKEW_TOLERANCE_SEC", raising=False)
    assert seconds_significantly_newer(110.0, 100.0)
    assert not seconds_significantly_newer(101.0, 100.0)
    assert not seconds_significantly_newer(100.0, 110.0)


def test_skew_tolerance_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NORNIR_MTIME_SKEW_TOLERANCE_SEC", "0")
    assert seconds_significantly_newer(100.5, 100.0)
    assert not _mtime_equal(100.0, 100.5)


def test_image_output_fresh_path_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NORNIR_MTIME_SKEW_TOLERANCE_SEC", raising=False)
    image = tmp_path / "k.png"
    assert not image_output_fresh(image, None)
    image.write_bytes(b"x")
    stamp = image.stat().st_mtime
    assert image_output_fresh(image, None)
    assert image_output_fresh(image, stamp)
    assert image_output_fresh(image, stamp - 100.0)
    # The watermark is newer than the file beyond the tolerance: the file is stale.
    assert not image_output_fresh(image, stamp + 100.0)


def test_image_output_fresh_products_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NORNIR_MTIME_SKEW_TOLERANCE_SEC", raising=False)
    images = tmp_path / IMAGES_FOLDER
    images.mkdir()
    (images / "k.png").write_bytes(b"x")
    products = CropProductIndex(tmp_path)
    stamp = products.mtime(IMAGES_FOLDER, "k.png")
    assert stamp is not None
    # The index answers, not the filesystem: a path that does not exist is still fresh when indexed.
    missing_on_disk = tmp_path / "elsewhere" / "k.png"
    assert image_output_fresh(missing_on_disk, None, products=products)
    assert image_output_fresh(missing_on_disk, stamp - 100.0, products=products)
    assert not image_output_fresh(missing_on_disk, stamp + 100.0, products=products)
    # Not indexed: never fresh, even for a path that exists on disk.
    assert not image_output_fresh(images / "other.png", None, products=products)
    assert not image_output_fresh(images / "k.png", None, products=products, folder="masks")


def test_image_watermark_json_round_trip_and_legacy_defaults() -> None:
    mark = _mark(origin_x=7, origin_y=8, width=9, height=10)
    assert ImageWatermark.from_json(json.loads(json.dumps(mark.to_json()))) == mark
    legacy = ImageWatermark.from_json({"key": "old"})
    assert legacy == ImageWatermark(
        key="old", downsample=1, ix0=0, ix1=0, iy0=0, iy1=0, member_ids=[], max_last_modified=""
    )


def test_same_tiles_pixel_extent_rules() -> None:
    assert _mark().same_tiles(_mark())
    for name in ("ix0", "ix1", "iy0", "iy1"):
        assert not _mark().same_tiles(_mark(**{name: 99})), name
    assert not _mark().same_tiles(_mark(downsample=4))
    # A legacy mark (no pixel extent on either side) matches on tile indices alone.
    assert _mark(width=0, height=0).same_tiles(_mark(width=8, height=8))
    assert _mark(width=8, height=8).same_tiles(_mark(width=0, height=0))
    assert _mark(width=8, height=0).same_tiles(_mark(width=8, height=8))
    # Both extents known: they must agree.
    assert _mark(width=8, height=8).same_tiles(_mark(width=8, height=8))
    assert not _mark(width=8, height=8, origin_x=1).same_tiles(_mark(width=8, height=8))
    assert not _mark(width=8, height=8, origin_y=1).same_tiles(_mark(width=8, height=8))
    assert not _mark(width=8, height=8).same_tiles(_mark(width=9, height=8))
    assert not _mark(width=8, height=8).same_tiles(_mark(width=8, height=9))


def test_same_members_ignores_order_and_checks_last_modified() -> None:
    assert _mark(member_ids=[5, 6]).same_members(_mark(member_ids=[6, 5]))
    assert not _mark(member_ids=[5, 6]).same_members(_mark(member_ids=[5]))
    assert not _mark().same_members(_mark(max_last_modified="t1"))


def test_section_watermark_round_trip_and_defaults(tmp_path: Path) -> None:
    mark = _mark(width=4, height=4)
    watermark = SectionWatermark(
        ids=[1, 2],
        max_last_modified="t0",
        tileset_mtime=12.5,
        params_hash="abc",
        downsample=2,
        images=[mark],
    )
    payload = watermark.to_json()
    assert payload["imageKeys"] == ["k"]
    restored = SectionWatermark.from_json(json.loads(json.dumps(payload)))
    assert restored.images == [mark]
    assert restored.image_keys == ["k"]
    assert (restored.ids, restored.tileset_mtime, restored.params_hash, restored.downsample) == ([1, 2], 12.5, "abc", 2)

    empty = SectionWatermark.from_json({})
    assert (empty.ids, empty.max_last_modified, empty.tileset_mtime, empty.params_hash) == ([], "", None, "")
    assert empty.images == [] and empty.image_keys == []
    assert SectionWatermark(ids=[], max_last_modified="", tileset_mtime=None, params_hash="").to_json()["imageKeys"] == []


def test_save_and_load_section_watermark(tmp_path: Path) -> None:
    output = tmp_path / "out" / "CPED"
    assert load_section_watermark(output, 3) is None
    watermark = SectionWatermark(ids=[9], max_last_modified="t", tileset_mtime=None, params_hash="h")
    save_section_watermark(output, 3, watermark)
    path = section_meta_path(output, 3)
    assert path.name == "section_3.meta.json"
    assert path.is_file()
    assert not str(path).startswith(str(output))  # lives in the sibling work cache, not the output
    loaded = load_section_watermark(output, 3)
    assert loaded is not None and loaded.ids == [9] and loaded.tileset_mtime is None
    assert load_section_watermark(output, 4) is None


def _fresh_inputs() -> tuple[list[LocationRecord], ExportParams, SectionWatermark]:
    records = [_record(1, datetime(2020, 1, 1)), _record(2, datetime(2020, 2, 1))]
    params = _params()
    watermark = SectionWatermark(
        ids=[1, 2],
        max_last_modified=datetime(2020, 2, 1).isoformat(),
        tileset_mtime=10.0,
        params_hash=params.hash(),
    )
    return records, params, watermark


def test_section_is_fresh_every_invalidator() -> None:
    records, params, watermark = _fresh_inputs()

    def fresh(**overrides: object) -> bool:
        arguments: dict[str, object] = {
            "watermark": watermark, "params": params, "tileset_mtime": 10.0, "force": False
        }
        arguments.update(overrides)
        return section_is_fresh(records, **arguments)  # type: ignore[arg-type]

    assert fresh()
    assert not fresh(refresh_odata=True)
    assert not fresh(watermark=None)
    assert not fresh(params=_params(pad=0.5))
    assert not fresh(tileset_mtime=999.0)
    assert not fresh(tileset_mtime=None)
    # Empty section with an empty watermark: nothing to rebuild, mtimes both None.
    empty = SectionWatermark(ids=[], max_last_modified=datetime.min.isoformat(), tileset_mtime=None, params_hash=params.hash())
    assert section_is_fresh([], watermark=empty, params=params, tileset_mtime=None, force=False)


def _rebuild(tmp_path: Path, **overrides: object) -> str:
    image = tmp_path / "k.png"
    meta = tmp_path / "k.json"
    arguments: dict[str, object] = {
        "image_path": image,
        "json_path": meta,
        "params_hash": "p",
        "previous_params_hash": "p",
        "tileset_mtime": 5.0,
        "previous_tileset_mtime": 5.0,
        "force": False,
    }
    arguments.update(overrides)
    return image_rebuild_mode(_mark(), arguments.pop("previous", _mark()), **arguments)  # type: ignore[arg-type]


def test_image_rebuild_mode_every_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NORNIR_MTIME_SKEW_TOLERANCE_SEC", raising=False)
    # Nothing on disk yet: the crop image is missing, so rebuild it.
    assert _rebuild(tmp_path) == "full"
    (tmp_path / "k.png").write_bytes(b"x")
    # Image present, JSON missing: masks must be rewritten but not the image.
    assert _rebuild(tmp_path) == "mask_only"
    (tmp_path / "k.json").write_text("{}", encoding="utf-8")
    assert _rebuild(tmp_path) == "skip"

    assert _rebuild(tmp_path, force=True) == "full"
    assert _rebuild(tmp_path, previous=None) == "full"
    assert _rebuild(tmp_path, previous_params_hash="other") == "full"
    assert _rebuild(tmp_path, previous_params_hash=None) == "full"
    assert _rebuild(tmp_path, previous_tileset_mtime=500.0) == "full"
    assert _rebuild(tmp_path, previous_tileset_mtime=None) == "full"
    assert _rebuild(tmp_path, previous=_mark(ix0=99)) == "full"
    # Changed LastModified keeps the image but regenerates masks.
    assert _rebuild(tmp_path, previous=_mark(max_last_modified="older")) == "mask_only"
    assert _rebuild(tmp_path, previous=_mark(member_ids=[5])) == "mask_only"
    # refresh_odata is handled at section level and does not change the per-crop result.
    assert _rebuild(tmp_path, refresh_odata=True) == "skip"


def test_image_rebuild_mode_stale_image_is_mask_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A crop older than the tileset watermark is not skipped even when members match."""
    monkeypatch.delenv("NORNIR_MTIME_SKEW_TOLERANCE_SEC", raising=False)
    (tmp_path / "k.png").write_bytes(b"x")
    (tmp_path / "k.json").write_text("{}", encoding="utf-8")
    stamp = (tmp_path / "k.png").stat().st_mtime
    assert _rebuild(tmp_path, tileset_mtime=stamp + 1000.0, previous_tileset_mtime=stamp + 1000.0) == "mask_only"


def test_image_rebuild_mode_uses_product_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NORNIR_MTIME_SKEW_TOLERANCE_SEC", raising=False)
    images = tmp_path / IMAGES_FOLDER
    images.mkdir()
    (images / "k.png").write_bytes(b"x")
    products = CropProductIndex(tmp_path)
    # The paths do not exist on disk; only the index says the PNG is there.
    ghost_image = tmp_path / "ghost" / "k.png"
    ghost_json = tmp_path / "ghost" / "k.json"
    assert _rebuild(tmp_path, image_path=ghost_image, json_path=ghost_json, products=products) == "mask_only"
    (images / "k.json").write_text("{}", encoding="utf-8")
    products = CropProductIndex(tmp_path)
    assert (
        _rebuild(tmp_path, image_path=ghost_image, json_path=ghost_json, products=products, tileset_mtime=None, previous_tileset_mtime=None)
        == "skip"
    )
    empty = CropProductIndex(tmp_path / "nothing")
    assert _rebuild(tmp_path, image_path=ghost_image, json_path=ghost_json, products=empty) == "full"
