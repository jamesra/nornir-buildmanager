"""QA overlays tint TEM with Viking HCL hue while keeping TEM luma."""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from hypothesis.extra import numpy as hnp
from PIL import Image

from nornir_buildmanager.operations.segmentationtraining.sam2 import (
    write as write_module,
)
from nornir_buildmanager.operations.segmentationtraining.sam2.write import (
    hcl_to_rgb,
    max_chroma_at_luma,
    perceptual_luma,
    rgb_to_hcl,
    write_overlay,
)

# Midpoint hue of each of the six hextants, plus the boundary hues between them.
_HEXTANT_HUES = tuple((index + 0.5) / 6.0 for index in range(6)) + tuple(index / 6.0 for index in range(6))
# Luma edges max_chroma_at_luma and CorrectLuma special-case (0, 1, and the 0.001 / 0.999 cutoffs).
_EDGE_LUMAS = (0.0, 1e-12, 0.001, 0.0010000001, 0.5, 0.999, 0.9989999999, 1.0 - 1e-12, 1.0)


def _correct_luma_reference(hextant: int, components: np.ndarray, luma: np.ndarray) -> np.ndarray:
    """Pre-optimization ``_correct_luma`` body (all three passes over every row)."""
    weights = np.array(write_module._COMPONENT_LUMA_WEIGHTS[hextant], dtype=np.float64)
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


def _hcl_to_rgb_reference(hue: float, chroma: np.ndarray, luma: np.ndarray) -> np.ndarray:
    """Pre-optimization ``hcl_to_rgb`` body."""
    chroma = np.asarray(chroma, dtype=np.float64)
    luma = np.asarray(luma, dtype=np.float64)
    _, hextant, slope_coeff = write_module._hue_hextant(hue)
    slope = chroma * slope_coeff
    components = np.stack((chroma, slope, np.zeros_like(chroma)), axis=-1)
    components = _correct_luma_reference(hextant, components, luma)
    red_i, green_i, blue_i = write_module._RGB_INDEX_MAP[hextant]
    rgb = np.empty(components.shape, dtype=np.float64)
    rgb[..., 0] = components[..., red_i]
    rgb[..., 1] = components[..., green_i]
    rgb[..., 2] = components[..., blue_i]
    return np.clip(rgb, 0.0, 1.0)


def _load_tem_rgb_reference(tem_path: Path | None, width: int, height: int) -> np.ndarray:
    """Pre-optimization ``_load_tem_rgb`` body."""
    if tem_path is None or not Path(tem_path).is_file():
        return np.zeros((height, width, 3), dtype=np.float64)
    image = Image.open(tem_path).convert("RGB")
    if image.size != (width, height):
        image = image.resize((width, height), Image.Resampling.BILINEAR)
    return np.asarray(image, dtype=np.float64) / 255.0


def _write_overlay_reference(
    path: Path, *, width: int, height: int, mask_paths: Iterable[str], tem_path: Path | None = None
) -> None:
    """Pre-optimization ``write_overlay`` body."""
    canvas = _load_tem_rgb_reference(tem_path, width, height)
    tem_luma = perceptual_luma(canvas)
    colors = write_module._OVERLAY_COLORS
    input_alpha = write_module._INPUT_LUMA_ALPHA
    color_alpha = write_module._OVERLAY_COLOR_ALPHA
    for index, mask_path in enumerate(mask_paths):
        if not Path(mask_path).is_file():
            continue
        mask = np.array(Image.open(mask_path).convert("L").resize((width, height), Image.Resampling.NEAREST)) > 0
        if not np.any(mask):
            continue
        hue, chroma, color_luma = rgb_to_hcl(colors[index % len(colors)])
        luma = (1.0 - input_alpha) * tem_luma[mask] + input_alpha * color_luma
        limited = np.minimum(chroma, max_chroma_at_luma(hue, luma))
        tinted = _hcl_to_rgb_reference(hue, limited, luma)
        canvas[mask] = (1.0 - color_alpha) * canvas[mask] + color_alpha * tinted
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(np.rint(canvas * 255.0), 0, 255).astype(np.uint8), mode="RGB").save(path)


def _assert_bits_equal(new: np.ndarray, old: np.ndarray) -> None:
    """Same shape, dtype, and float bits (distinguishes -0.0 from 0.0)."""
    assert new.shape == old.shape
    assert new.dtype == old.dtype
    assert new.tobytes() == old.tobytes()


_unit_floats = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False, allow_subnormal=False)
_luma_arrays = hnp.arrays(
    np.float64,
    st.integers(min_value=1, max_value=300),
    elements=st.one_of(st.sampled_from(_EDGE_LUMAS), _unit_floats),
)
_hues = st.one_of(st.sampled_from(_HEXTANT_HUES), _unit_floats)


@settings(max_examples=300, deadline=None)
@given(hue=_hues, luma=_luma_arrays, data=st.data())
@example(hue=0.0, luma=np.array(_EDGE_LUMAS), data=None)
def test_hcl_to_rgb_matches_reference_bits(hue: float, luma: np.ndarray, data: st.DataObject | None) -> None:
    """Limited chroma (the overlay path) and unclamped chroma (CorrectLuma fallback passes) match bit for bit."""
    limited = np.minimum(1.0, max_chroma_at_luma(hue, luma))
    _assert_bits_equal(hcl_to_rgb(hue, limited, luma), _hcl_to_rgb_reference(hue, limited, luma))
    if data is None:
        chroma = np.full(luma.shape, 1.0)
    else:
        chroma = data.draw(hnp.arrays(np.float64, luma.shape, elements=_unit_floats), label="chroma")
    _assert_bits_equal(hcl_to_rgb(hue, chroma, luma), _hcl_to_rgb_reference(hue, chroma, luma))


@pytest.mark.parametrize("hextant", range(6))
def test_correct_luma_matches_reference_all_branches(hextant: int) -> None:
    """Rows taking each of the three CorrectLuma passes, in every hextant, match bit for bit."""
    rng = np.random.default_rng(hextant)
    chroma = rng.uniform(0.0, 1.0, 5000)
    slope = chroma * rng.uniform(0.0, 1.0, 5000)
    components = np.stack((chroma, slope, np.zeros_like(chroma)), axis=-1)
    luma = np.concatenate((rng.uniform(0.0, 1.0, 5000 - len(_EDGE_LUMAS)), _EDGE_LUMAS))
    weights = np.array(write_module._COMPONENT_LUMA_WEIGHTS[hextant])
    first = components + (luma - components @ weights)[..., None]
    ok_first = (first[..., 0] >= 0.0) & (first[..., 0] <= 1.0)
    assert ok_first.any() and not ok_first.all()
    _assert_bits_equal(
        write_module._correct_luma(hextant, components.copy(), luma),
        _correct_luma_reference(hextant, components.copy(), luma),
    )


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4, 5, 7, 8, 17])
def test_hcl_to_rgb_matches_reference_small_sizes(count: int) -> None:
    """Short inputs (BLAS tail and single-row paths) match bit for bit, including 0-d and empty."""
    rng = np.random.default_rng(count)
    for hue in _HEXTANT_HUES:
        luma = rng.uniform(0.0, 1.0, count)
        chroma = rng.uniform(0.0, 1.0, count)
        _assert_bits_equal(hcl_to_rgb(hue, chroma, luma), _hcl_to_rgb_reference(hue, chroma, luma))
        _assert_bits_equal(
            hcl_to_rgb(hue, np.array(0.9), np.array(0.95)), _hcl_to_rgb_reference(hue, np.array(0.9), np.array(0.95))
        )


def _overlay_case(root: Path, seed: int, size: int, mask_count: int, tem_mode: str) -> tuple[list[str], Path | None]:
    """Write a TEM image and *mask_count* random masks under *root*; return mask paths and TEM path."""
    rng = np.random.default_rng(seed)
    tem_path: Path | None = root / "tem.png"
    if tem_mode == "gray":
        Image.fromarray(rng.integers(0, 256, (size, size), dtype=np.uint8), mode="L").save(tem_path)
    elif tem_mode == "rgb":
        Image.fromarray(rng.integers(0, 256, (size, size, 3), dtype=np.uint8), mode="RGB").save(tem_path)
    elif tem_mode == "resized":
        Image.fromarray(rng.integers(0, 256, (size // 2 + 1, size + 3), dtype=np.uint8), mode="L").save(tem_path)
    elif tem_mode == "missing":
        tem_path = root / "absent.png"
    else:
        tem_path = None
    mask_paths: list[str] = []
    for index in range(mask_count):
        mask_path = root / f"mask_{index}.png"
        kind = index % 4
        if kind == 3 and index:
            mask_paths.append(str(root / f"missing_{index}.png"))
            continue
        mask_size = size if kind else size // 2 + 1
        mask = (rng.uniform(0.0, 1.0, (mask_size, mask_size)) < rng.uniform(0.05, 0.9)).astype(np.uint8) * 255
        if kind == 2 and index == 2:
            mask[:] = 0
        Image.fromarray(mask, mode="L").save(mask_path)
        mask_paths.append(str(mask_path))
    return mask_paths, tem_path


@settings(max_examples=40, deadline=None)
@given(
    seed=st.integers(min_value=0, max_value=2**32 - 1),
    size=st.integers(min_value=1, max_value=64),
    mask_count=st.integers(min_value=0, max_value=9),
    tem_mode=st.sampled_from(("gray", "rgb", "resized", "missing", "none")),
)
@example(seed=1, size=48, mask_count=8, tem_mode="gray")
@example(seed=2, size=1, mask_count=3, tem_mode="rgb")
def test_write_overlay_matches_reference_bytes(seed: int, size: int, mask_count: int, tem_mode: str) -> None:
    """PNG bytes equal the pre-optimization body (overlapping masks, color wrap, empty/missing masks, resize)."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        mask_paths, tem_path = _overlay_case(root, seed, size, mask_count, tem_mode)
        write_overlay(root / "new" / "o.png", width=size, height=size, mask_paths=mask_paths, tem_path=tem_path)
        _write_overlay_reference(root / "old" / "o.png", width=size, height=size, mask_paths=mask_paths, tem_path=tem_path)
        assert (root / "new" / "o.png").read_bytes() == (root / "old" / "o.png").read_bytes()


def test_write_overlay_matches_reference_bytes_full_crop(tmp_path: Path) -> None:
    """A 1024 px crop with textured TEM and four large overlapping masks writes identical PNG bytes."""
    rng = np.random.default_rng(7)
    size = 1024
    tem = np.clip(rng.normal(128, 40, (size, size)), 0, 255).astype(np.uint8)
    Image.fromarray(tem, mode="L").save(tmp_path / "tem.png")
    yy, xx = np.mgrid[0:size, 0:size]
    mask_paths = []
    for index, (cx, cy, radius) in enumerate(((300, 300, 250), (700, 300, 200), (500, 700, 300), (800, 800, 150))):
        mask = (((xx - cx) ** 2 + (yy - cy) ** 2) <= radius**2).astype(np.uint8) * 255
        Image.fromarray(mask, mode="L").save(tmp_path / f"m{index}.png")
        mask_paths.append(str(tmp_path / f"m{index}.png"))
    write_overlay(tmp_path / "new.png", width=size, height=size, mask_paths=mask_paths, tem_path=tmp_path / "tem.png")
    _write_overlay_reference(
        tmp_path / "old.png", width=size, height=size, mask_paths=mask_paths, tem_path=tmp_path / "tem.png"
    )
    assert (tmp_path / "new.png").read_bytes() == (tmp_path / "old.png").read_bytes()


def test_rgb_to_hcl_red_matches_viking() -> None:
    hue, chroma, luma = rgb_to_hcl((255, 0, 0))
    assert abs(hue - 0.0) < 1e-9
    assert abs(chroma - 1.0) < 1e-9
    assert abs(luma - 0.3) < 1e-9


def test_hcl_keeps_tem_luma_and_applies_red_hue(tmp_path: Path) -> None:
    tem = np.zeros((8, 8), dtype=np.uint8)
    tem[:, :4] = 40
    tem[:, 4:] = 200
    Image.fromarray(tem, mode="L").save(tmp_path / "tem.png")
    mask = np.zeros((8, 8), dtype=np.uint8)
    mask[2:6, 2:6] = 255
    Image.fromarray(mask, mode="L").save(tmp_path / "mask.png")
    write_overlay(
        tmp_path / "overlay.png",
        width=8,
        height=8,
        mask_paths=[str(tmp_path / "mask.png")],
        tem_path=tmp_path / "tem.png",
    )
    overlay = np.asarray(Image.open(tmp_path / "overlay.png").convert("RGB"))
    tem_rgb = np.asarray(Image.open(tmp_path / "tem.png").convert("RGB"))
    inside = overlay[2:6, 2:6]
    outside = overlay[0, 0]
    assert np.array_equal(outside, tem_rgb[0, 0])
    dark = inside[:, 0]
    bright = inside[:, -1]
    assert perceptual_luma(dark.astype(np.float64) / 255.0).mean() < perceptual_luma(
        bright.astype(np.float64) / 255.0
    ).mean()
    assert inside[..., 0].mean() > inside[..., 1].mean()
    assert inside[..., 0].mean() > inside[..., 2].mean()


def test_hcl_roundtrip_at_own_luma() -> None:
    hue, chroma, luma = rgb_to_hcl((255, 0, 0))
    limited = min(chroma, float(max_chroma_at_luma(hue, np.array(luma))))
    rgb = hcl_to_rgb(hue, np.array(limited), np.array(luma))
    assert abs(float(rgb[0]) - 1.0) < 1e-6
    assert abs(float(rgb[1])) < 1e-6
    assert abs(float(rgb[2])) < 1e-6
