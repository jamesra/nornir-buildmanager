"""Audit and repair AnnotationCrops products without ingest or stitch."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nornir_shared import prettyoutput
from PIL import Image, UnidentifiedImageError

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.segmentationtraining.freshness import (
    load_section_watermark,
    save_section_watermark,
    section_meta_path,
)
from nornir_buildmanager.operations.segmentationtraining.stitch import resolve_crop_image

_MEMBER_GLOBS = (
    ("masks", "*.png"),
    ("ignored", "*.png"),
    ("_work/rle", "*.json"),
)


@dataclass
class CleanupSummary:
    """Counts of files removed or rewritten by one cleanup pass."""

    deleted_keys: int = 0
    deleted_members: int = 0
    rewritten_json: int = 0
    deleted_overlays: int = 0
    rewritten_watermarks: int = 0
    deleted_watermarks: int = 0


def remove_image_key_products(output: Path, key: str) -> int:
    """Delete crop, JSON, overlay, masks, ignored masks, and RLE for one image key."""
    removed = 0
    for path in (
        output / "images" / f"{key}.png",
        output / "images" / f"{key}.jpg",
        output / "images" / f"{key}.json",
        output / "overlays" / f"{key}.png",
    ):
        if path.is_file():
            path.unlink()
            removed += 1
    removed += _unlink_member_files(output, key, keep_ids=None)
    return removed


def remove_member_files_not_in(output: Path, key: str, keep_ids: set[int]) -> int:
    """Delete mask, ignored, and RLE files for *key* whose location id is not kept."""
    return _unlink_member_files(output, key, keep_ids=keep_ids)


def cleanup_annotation_crops(
    output_path: str | Path,
    *,
    sections: list[int] | None = None,
) -> CleanupSummary:
    """Report and repair crop/metadata mismatches. Does not read OData or tiles."""
    output = Path(output_path)
    wanted = set(sections) if sections is not None else None
    summary = CleanupSummary()
    for key in _candidate_keys(output, wanted):
        _repair_key(output, key, summary)
    _repair_watermarks(output, wanted, summary)
    prettyoutput.Log(
        "CleanupAnnotationCrops: "
        f"deleted {summary.deleted_keys} incomplete image(s), "
        f"removed {summary.deleted_members} orphan member file(s), "
        f"rewrote {summary.rewritten_json} JSON file(s), "
        f"deleted {summary.deleted_overlays} overlay(s), "
        f"updated {summary.rewritten_watermarks} watermark(s), "
        f"removed {summary.deleted_watermarks} empty watermark(s)"
    )
    return summary


def CleanupAnnotationCrops(
    OutputPath: str | None = None,
    Sections: list[int] | None = None,
    Cleanup: bool = False,
    **kwargs: Any,
) -> None:
    """Pipeline entry. No-op unless ``Cleanup`` is true. Returns None."""
    del kwargs
    if not Cleanup:
        return None
    if not OutputPath:
        raise NornirUserException("ExportAnnotationCrops requires -Output")
    cleanup_annotation_crops(OutputPath, sections=_as_int_list(Sections))
    return None


def _repair_key(output: Path, key: str, summary: CleanupSummary) -> None:
    image_path = resolve_crop_image(output / "images", key)
    json_path = output / "images" / f"{key}.json"
    image_ok = image_path.is_file()
    json_ok = json_path.is_file()
    if image_ok != json_ok:
        prettyoutput.Log(
            f"CleanupAnnotationCrops: delete incomplete image key {key}"
        )
        remove_image_key_products(output, key)
        summary.deleted_keys += 1
        return
    if not image_ok:
        overlay = output / "overlays" / f"{key}.png"
        if overlay.is_file():
            prettyoutput.Log(
                f"CleanupAnnotationCrops: delete overlay with no image {key}"
            )
            overlay.unlink()
            summary.deleted_overlays += 1
        removed = remove_member_files_not_in(output, key, set())
        if removed:
            prettyoutput.Log(
                f"CleanupAnnotationCrops: delete {removed} member file(s) with no image {key}"
            )
            summary.deleted_members += removed
        return

    payload = _load_json(json_path)
    image_meta = payload.get("image")
    if not isinstance(image_meta, dict):
        raise NornirUserException(f"Annotation crop JSON is missing image: {json_path}")
    changed = _align_image_meta(image_path, image_meta, key)
    annotations = payload.get("annotations")
    if annotations is None:
        annotations = []
    if not isinstance(annotations, list):
        raise NornirUserException(f"Annotation crop JSON annotations are not a list: {json_path}")
    kept: list[Any] = []
    for item in annotations:
        if not isinstance(item, dict) or "id" not in item:
            raise NornirUserException(f"Annotation crop JSON has an annotation without id: {json_path}")
        location_id = int(item["id"])
        if _mask_exists(output, key, location_id):
            kept.append(item)
            continue
        prettyoutput.Log(
            f"CleanupAnnotationCrops: drop annotation {location_id} with no mask from {key}"
        )
        changed = True
    if changed:
        payload["annotations"] = kept
        json_path.write_text(json.dumps(payload), encoding="utf-8")
        summary.rewritten_json += 1
    keep_ids = {int(item["id"]) for item in kept}
    removed = remove_member_files_not_in(output, key, keep_ids)
    if removed:
        prettyoutput.Log(
            f"CleanupAnnotationCrops: delete {removed} orphan member file(s) for {key}"
        )
        summary.deleted_members += removed


def _align_image_meta(image_path: Path, image_meta: dict[str, Any], key: str) -> bool:
    try:
        with Image.open(image_path) as handle:
            width, height = handle.size
    except (OSError, UnidentifiedImageError) as exc:
        raise NornirUserException(f"Annotation crop image is unreadable: {image_path}") from exc
    changed = False
    if image_meta.get("file_name") != image_path.name:
        prettyoutput.Log(
            f"CleanupAnnotationCrops: rewrite file_name for {key} to {image_path.name}"
        )
        image_meta["file_name"] = image_path.name
        changed = True
    if _as_int(image_meta.get("width")) != width or _as_int(image_meta.get("height")) != height:
        prettyoutput.Log(
            f"CleanupAnnotationCrops: rewrite size for {key} to {width}x{height}"
        )
        image_meta["width"] = width
        image_meta["height"] = height
        changed = True
    return changed


def _repair_watermarks(output: Path, wanted: set[int] | None, summary: CleanupSummary) -> None:
    work = output / "_work"
    if not work.is_dir():
        return
    for path in sorted(work.glob("section_*.meta.json")):
        token = path.name[len("section_") : -len(".meta.json")]
        if not token.isdigit():
            continue
        z = int(token)
        if wanted is not None and z not in wanted:
            continue
        watermark = load_section_watermark(output, z)
        if watermark is None:
            continue
        images = [item for item in (watermark.images or []) if _has_crop_image(output, item.key)]
        keys = [key for key in (watermark.image_keys or []) if _has_crop_image(output, key)]
        if not keys and not images:
            prettyoutput.Log(
                f"CleanupAnnotationCrops: delete empty watermark for section {z}"
            )
            section_meta_path(output, z).unlink(missing_ok=True)
            summary.deleted_watermarks += 1
            continue
        prior_keys = list(watermark.image_keys or [])
        prior_image_keys = [item.key for item in (watermark.images or [])]
        if prior_keys == keys and prior_image_keys == [item.key for item in images]:
            continue
        prettyoutput.Log(
            f"CleanupAnnotationCrops: drop missing image keys from section {z} watermark"
        )
        watermark.image_keys = keys
        watermark.images = images
        save_section_watermark(output, z, watermark)
        summary.rewritten_watermarks += 1


def _candidate_keys(output: Path, wanted: set[int] | None) -> list[str]:
    keys: set[str] = set()
    images = output / "images"
    if images.is_dir():
        for pattern in ("*.png", "*.jpg", "*.json"):
            for path in images.glob(pattern):
                keys.add(path.stem)
    overlays = output / "overlays"
    if overlays.is_dir():
        for path in overlays.glob("*.png"):
            keys.add(path.stem)
    for folder, pattern in _MEMBER_GLOBS:
        root = output.joinpath(*folder.split("/"))
        if not root.is_dir():
            continue
        for path in root.glob(pattern):
            key, _location_id = _split_member_stem(path.stem)
            if key:
                keys.add(key)
    ordered = sorted(keys)
    if wanted is None:
        return ordered
    return [key for key in ordered if _image_key_matches_sections(key, wanted)]


def _unlink_member_files(output: Path, key: str, keep_ids: set[int] | None) -> int:
    removed = 0
    for folder, pattern in _MEMBER_GLOBS:
        root = output.joinpath(*folder.split("/"))
        if not root.is_dir():
            continue
        suffix = ".png" if pattern.endswith(".png") else ".json"
        for path in root.glob(f"{key}_*{suffix}"):
            member_key, location_id = _split_member_stem(path.stem)
            if member_key != key or location_id is None:
                continue
            if keep_ids is not None and location_id in keep_ids:
                continue
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def _split_member_stem(stem: str) -> tuple[str, int | None]:
    if "_" not in stem:
        return stem, None
    key, token = stem.rsplit("_", 1)
    if not token.isdigit():
        return stem, None
    return key, int(token)


def _mask_exists(output: Path, key: str, location_id: int) -> bool:
    name = f"{key}_{location_id}.png"
    return (output / "masks" / name).is_file() or (output / "ignored" / name).is_file()


def _has_crop_image(output: Path, key: str) -> bool:
    return resolve_crop_image(output / "images", key).is_file()


def _image_key_matches_sections(key: str, sections: set[int]) -> bool:
    """True when *key* looks like ``{volume}_{z}_D...`` for a requested Z."""
    for z in sections:
        if f"_{z}_D" in key:
            return True
    return False


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NornirUserException(f"Annotation crop JSON is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise NornirUserException(f"Annotation crop JSON is not an object: {path}")
    return payload


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_int_list(value: Any) -> list[int] | None:
    if value is None or value == "":
        return None
    if isinstance(value, list):
        return [int(item) for item in value]
    return [int(value)]
