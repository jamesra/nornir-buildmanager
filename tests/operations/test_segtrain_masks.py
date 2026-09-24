"""Tests for SA-1B COCO RLE encoding."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings, strategies as st
from hypothesis.extra.numpy import arrays

from nornir_buildmanager.operations.segmentationtraining.masks import encode_coco_rle


def _rle_oracle(mask: np.ndarray) -> dict[str, object]:
    """Original Python run-length loop; kept as the uncompressed COCO spec oracle."""
    fortran = np.asfortranarray(mask.astype(np.uint8))
    pixels = fortran.ravel(order="F")
    height, width = mask.shape
    counts: list[int] = []
    if pixels.size == 0:
        return {"counts": counts, "size": [height, width]}
    current = int(pixels[0])
    if current == 1:
        counts.append(0)
    run = 1
    for value in pixels[1:]:
        if int(value) == current:
            run += 1
        else:
            counts.append(run)
            current = int(value)
            run = 1
    counts.append(run)
    return {"counts": counts, "size": [int(height), int(width)]}


@pytest.mark.parametrize(
    "mask",
    [
        np.zeros((0, 0), dtype=np.uint8),
        np.zeros((1, 1), dtype=np.uint8),
        np.ones((1, 1), dtype=np.uint8),
        np.zeros((4, 6), dtype=np.uint8),
        np.ones((3, 5), dtype=np.uint8),
        np.array([[1, 0], [1, 0]], dtype=np.uint8),
        np.array([[0, 1, 1], [0, 0, 1]], dtype=np.uint8),
    ],
)
def test_encode_coco_rle_matches_oracle_examples(mask: np.ndarray) -> None:
    assert encode_coco_rle(mask) == _rle_oracle(mask)


@given(
    arrays(
        dtype=np.uint8,
        shape=st.tuples(st.integers(1, 24), st.integers(1, 24)),
        elements=st.integers(0, 1),
    )
)
@settings(max_examples=80, deadline=None)
def test_encode_coco_rle_matches_oracle_random(mask: np.ndarray) -> None:
    assert encode_coco_rle(mask) == _rle_oracle(mask)
