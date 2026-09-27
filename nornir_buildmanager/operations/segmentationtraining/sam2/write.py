"""SA-1B image sidecars, COCO RLE JSON, optional overlay and gallery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image

from nornir_buildmanager.operations.segmentationtraining.records import LocationRecord


def write_sa1b_json(
    path: str | Path,
    *,
    file_name: str,
    width: int,
    height: int,
    annotations: list[dict[str, Any]],
    downsample: int | None = None,
    volume: str | None = None,
    odata: str | None = None,
) -> None:
    """Write one SA-1B ground-truth JSON next to a trainer crop."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    image: dict[str, Any] = {"file_name": file_name, "width": width, "height": height}
    if downsample is not None:
        image["downsample"] = downsample
    if volume:
        image["volume"] = volume
    if odata:
        text = str(odata).strip()
        if text:
            image["odata"] = text
    payload = {
        "image": image,
        "annotations": annotations,
    }
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


def ensure_sa1b_odata(path: str | Path, odata: str | None) -> bool:
    """Set ``image.odata`` on an existing crop JSON. Returns True when rewritten."""
    if not odata:
        return False
    url = str(odata).strip()
    if not url:
        return False
    json_path = Path(path)
    if not json_path.is_file():
        return False
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    image = payload.get("image")
    if not isinstance(image, dict):
        return False
    if image.get("odata") == url:
        return False
    image["odata"] = url
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    return True


def sa1b_annotation(
    record: LocationRecord,
    *,
    rle: dict[str, Any],
    bbox: list[int],
    area: int,
    window_index: int | None = None,
    window_count: int | None = None,
    origin_x: int | None = None,
    origin_y: int | None = None,
) -> dict[str, Any]:
    """One COCO-style annotation. SAM2 ignores category fields."""
    item: dict[str, Any] = {
        "id": record.id,
        "segmentation": rle,
        "bbox": bbox,
        "area": area,
        "category_id": record.type_id or 0,
        "category_name": record.category_name(),
        "structure_label": record.structure_label,
        "type_name": record.type_name,
        "last_modified": record.last_modified.isoformat(),
    }
    if window_index is not None:
        item["windowIndex"] = window_index
        item["windowCount"] = window_count
    if origin_x is not None and origin_y is not None:
        item["originX"] = int(origin_x)
        item["originY"] = int(origin_y)
    return item


# ITU-R BT.601, same as Viking ConvertToHCL / HSLRGBLib.fx LumaWeights.
_LUMA_WEIGHTS = np.array((0.30, 0.59, 0.11), dtype=np.float64)
_RGB_INDEX_MAP = (
    (0, 1, 2),
    (1, 0, 2),
    (2, 0, 1),
    (2, 1, 0),
    (1, 2, 0),
    (0, 2, 1),
)
_COMPONENT_LUMA_WEIGHTS = (
    (0.30, 0.59, 0.11),
    (0.59, 0.30, 0.11),
    (0.59, 0.11, 0.30),
    (0.11, 0.59, 0.30),
    (0.11, 0.30, 0.59),
    (0.30, 0.11, 0.59),
)
# Distinct annotation tints; hue/chroma are applied, TEM luma is kept.
_OVERLAY_COLORS = (
    (255, 0, 0),
    (0, 255, 0),
    (0, 128, 255),
    (255, 255, 0),
    (255, 0, 255),
    (0, 255, 255),
)
# Viking MeshView default for polygon luma overlay: keep 100% background luma.
_INPUT_LUMA_ALPHA = 0.0
# Blend HCL tint over TEM RGB. 0.25 is 4x as transparent as a full wash.
_OVERLAY_COLOR_ALPHA = 0.25


def write_overlay(
    path: str | Path,
    *,
    width: int,
    height: int,
    mask_paths: Iterable[str],
    tem_path: str | Path | None = None,
) -> None:
    """QA composite: Viking HCL hue/chroma over TEM luma. SAM2 does not read this."""
    canvas = _load_tem_rgb(tem_path, width, height)
    tem_luma = perceptual_luma(canvas)
    for index, mask_path in enumerate(mask_paths):
        if not Path(mask_path).is_file():
            continue
        mask = np.array(Image.open(mask_path).convert("L").resize((width, height), Image.NEAREST)) > 0
        if not np.any(mask):
            continue
        hue, chroma, color_luma = rgb_to_hcl(_OVERLAY_COLORS[index % len(_OVERLAY_COLORS)])
        luma = (1.0 - _INPUT_LUMA_ALPHA) * tem_luma[mask] + _INPUT_LUMA_ALPHA * color_luma
        limited = np.minimum(chroma, max_chroma_at_luma(hue, luma))
        tinted = hcl_to_rgb(hue, limited, luma)
        canvas[mask] = (1.0 - _OVERLAY_COLOR_ALPHA) * canvas[mask] + _OVERLAY_COLOR_ALPHA * tinted
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(np.rint(canvas * 255.0), 0, 255).astype(np.uint8), mode="RGB").save(path)


def _load_tem_rgb(tem_path: str | Path | None, width: int, height: int) -> np.ndarray:
    """Load the TEM crop as float RGB in 0..1, or black if the image is missing."""
    if tem_path is None or not Path(tem_path).is_file():
        return np.zeros((height, width, 3), dtype=np.float64)
    image = Image.open(tem_path).convert("RGB")
    if image.size != (width, height):
        image = image.resize((width, height), Image.BILINEAR)
    return np.asarray(image, dtype=np.float64) / 255.0


def perceptual_luma(rgb: np.ndarray) -> np.ndarray:
    """Viking ``CalculatePerceptualLumaFromRGB`` (0.3R + 0.59G + 0.11B)."""
    return np.matmul(rgb, _LUMA_WEIGHTS)


def rgb_to_hcl(color: tuple[int, int, int]) -> tuple[float, float, float]:
    """Port of ``ConvertToHCL``: hue, chroma, BT.601 luma in 0..1."""
    red, green, blue = (channel / 255.0 for channel in color)
    maximum = max(red, green, blue)
    minimum = min(red, green, blue)
    chroma = maximum - minimum
    luma = 0.3 * red + 0.59 * green + 0.11 * blue
    hue = 0.0
    if chroma > 0.0:
        if maximum == red:
            hue = ((green - blue) / chroma) / 6.0
            if hue < 0.0:
                hue += 1.0
        elif maximum == green:
            hue = ((blue - red) / chroma + 2.0) / 6.0
        else:
            hue = ((red - green) / chroma + 4.0) / 6.0
    return hue, chroma, luma


def max_chroma_at_luma(hue: float, luma: np.ndarray) -> np.ndarray:
    """Port of ``GetMaxChromaAtLuma`` so CorrectLuma does not crush saturation."""
    values = np.asarray(luma, dtype=np.float64)
    h_prime, hextant, slope_coeff = _hue_hextant(hue)
    del h_prime
    weights = _COMPONENT_LUMA_WEIGHTS[hextant]
    chroma_luma_coeff = weights[0] + weights[1] * slope_coeff
    upper = (1.0 - values) / max(1.0 - chroma_luma_coeff, 1e-6)
    lower = values / max(chroma_luma_coeff, 1e-6)
    limited = np.minimum(upper, lower)
    limited = np.where((values <= 0.001) | (values >= 0.999), 0.0, limited)
    return np.maximum(limited, 0.0)


def hcl_to_rgb(hue: float, chroma: np.ndarray, luma: np.ndarray) -> np.ndarray:
    """Port of ``HCLToRGB`` after chroma has been clamped for *luma*."""
    chroma = np.asarray(chroma, dtype=np.float64)
    luma = np.asarray(luma, dtype=np.float64)
    h_prime, hextant, slope_coeff = _hue_hextant(hue)
    del h_prime
    slope = chroma * slope_coeff
    components = np.stack((chroma, slope, np.zeros_like(chroma)), axis=-1)
    components = _correct_luma(hextant, components, luma)
    red_i, green_i, blue_i = _RGB_INDEX_MAP[hextant]
    rgb = np.empty(components.shape, dtype=np.float64)
    rgb[..., 0] = components[..., red_i]
    rgb[..., 1] = components[..., green_i]
    rgb[..., 2] = components[..., blue_i]
    return np.clip(rgb, 0.0, 1.0)


def _hue_hextant(hue: float) -> tuple[float, int, float]:
    h_prime = (float(hue) % 1.0) * 6.0
    hextant = int(h_prime) % 6
    f_descend = h_prime % 2.0
    slope_coeff = 1.0 - abs(f_descend - 1.0)
    return h_prime, hextant, slope_coeff


def _correct_luma(hextant: int, components: np.ndarray, luma: np.ndarray) -> np.ndarray:
    """Port of ``CorrectLuma``: shift {chroma, slope, 0} so luma matches the TEM."""
    weights = np.array(_COMPONENT_LUMA_WEIGHTS[hextant], dtype=np.float64)
    inv_rg = 1.0 / (weights[0] + weights[1])
    luma = np.asarray(luma, dtype=np.float64)
    first = components + (luma - components @ weights)[..., None]
    ok_first = (first[..., 0] >= 0.0) & (first[..., 0] <= 1.0)
    second = first.copy()
    second[..., 0] = np.clip(second[..., 0], 0.0, 1.0)
    second[..., 1] = np.clip(second[..., 1], 0.0, 1.0)
    spill = (luma - second @ weights) * inv_rg
    second[..., 1] = second[..., 1] + spill
    second[..., 2] = second[..., 2] + spill
    ok_second = (second[..., 1] >= 0.0) & (second[..., 1] <= 1.0)
    third = second.copy()
    third[..., 1] = np.clip(third[..., 1], 0.0, 1.0)
    third[..., 2] = np.clip(third[..., 2], 0.0, 1.0)
    third[..., 2] = third[..., 2] + (luma - third @ weights) * inv_rg
    result = np.where(ok_first[..., None], first, second)
    return np.where((~ok_first & ~ok_second)[..., None], third, result)


def write_gallery(output_path: str | Path, image_keys: list[str]) -> None:
    """Minimal index.html pointing at overlay images."""
    root = Path(output_path)
    lines = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>ExportAnnotationCrops</title></head><body>",
        "<h1>SegmentationTraining overlays</h1>",
        "<ul>",
    ]
    for key in image_keys:
        rel = f"overlays/{key}.png"
        if (root / rel).is_file():
            lines.append(f"<li><a href='{rel}'>{key}</a></li>")
    lines.append("</ul></body></html>")
    (root / "index.html").write_text("\n".join(lines), encoding="utf-8")


def append_manifest(output_path: str | Path, row: dict[str, Any]) -> None:
    """Append one JSONL manifest row."""
    path = Path(output_path) / "manifest.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def replace_manifest_rows(output_path: str | Path, z: int, rows: list[dict[str, Any]]) -> None:
    """Replace all manifest rows for one Z so rebuilds do not duplicate keys."""
    path = Path(output_path) / "manifest.jsonl"
    kept: list[dict[str, Any]] = []
    if path.is_file():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if int(item.get("z", -1)) != int(z):
                    kept.append(item)
    kept.extend(rows)
    with path.open("w", encoding="utf-8") as handle:
        for item in kept:
            handle.write(json.dumps(item, separators=(",", ":")) + "\n")
