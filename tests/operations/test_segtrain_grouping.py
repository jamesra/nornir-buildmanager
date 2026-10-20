"""Characterization tests for segmentationtraining.grouping.

group_snaps is checked against the original list-then-min host selection; group_windows,
CropGroup.image_key_for and volume_name_from_odata_url are pinned to their current outputs.
"""

from __future__ import annotations

import random

import pytest

from nornir_buildmanager.operations.segmentationtraining.geometry import (
    CropWindow,
    TileRect,
)
from nornir_buildmanager.operations.segmentationtraining.grouping import (
    CropGroup,
    group_snaps,
    group_windows,
    volume_name_from_odata_url,
)


def _key(rect: TileRect) -> tuple[int, int, int, int]:
    return rect.ix0, rect.iy0, rect.ix1, rect.iy1


def _reference_group_snaps(items: list[tuple[int, TileRect, TileRect, int]]) -> dict[TileRect, list[int]]:
    """The original implementation: every containing candidate, then the minimum by key."""
    candidates = sorted({item[2] for item in items}, key=_key)
    assigned: dict[TileRect, list[int]] = {}
    for location_id, tight, window, _ in items:
        hosts = [rect for rect in candidates if rect.contains(tight)]
        host = min(hosts, key=_key) if hosts else window
        assigned.setdefault(host, []).append(location_id)
    return assigned


def _random_items(rng: random.Random, count: int, span: int) -> list[tuple[int, TileRect, TileRect, int]]:
    items = []
    for location_id in range(count):
        x0, y0 = rng.randrange(span), rng.randrange(span)
        tight = TileRect(x0, x0 + rng.randint(1, 2), y0, y0 + rng.randint(1, 2))
        wx0, wy0 = max(0, x0 - rng.randint(0, 3)), max(0, y0 - rng.randint(0, 3))
        # Some windows deliberately fail to contain their own tight rect (fallback path).
        window = TileRect(wx0, wx0 + rng.randint(1, 4), wy0, wy0 + rng.randint(1, 4))
        items.append((location_id, tight, window, 1))
    return items


def test_group_snaps_matches_reference_on_random_inputs() -> None:
    rng = random.Random(20261019)
    for _ in range(300):
        items = _random_items(rng, rng.randint(1, 40), rng.randint(2, 12))
        groups = group_snaps(items)
        assert {group.snap: group.location_ids for group in groups} == _reference_group_snaps(items)
        assert [group.snap for group in groups] == list(_reference_group_snaps(items))


def test_group_snaps_prefers_smallest_key_host() -> None:
    small_key = TileRect(0, 4, 0, 4)
    other = TileRect(1, 4, 0, 4)
    tight = TileRect(2, 3, 1, 2)
    groups = group_snaps([(1, tight, other, 1), (2, tight, small_key, 1)])
    assert len(groups) == 1
    assert groups[0].snap == small_key
    assert groups[0].location_ids == [1, 2]


def test_group_snaps_falls_back_to_own_window_when_no_candidate_contains_tight() -> None:
    window = TileRect(0, 2, 0, 2)
    tight = TileRect(5, 6, 5, 6)
    groups = group_snaps([(7, tight, window, 1)])
    assert [(group.snap, group.location_ids) for group in groups] == [(window, [7])]


def test_group_windows_merges_identical_windows_and_sorts_ids() -> None:
    window = CropWindow(origin_x=100, origin_y=200, width=300, height=400)
    twin = CropWindow(origin_x=100, origin_y=200, width=300, height=400)
    groups = group_windows([(9, window), (3, twin), (5, window)], downsample=4, tile_x_dim=256, tile_y_dim=128)
    assert len(groups) == 1
    group = groups[0]
    assert group.location_ids == [3, 5, 9]
    assert group.downsample == 4
    assert group.window == window
    # floor(100/256)=0, ceil(400/256)=2, floor(200/128)=1, ceil(600/128)=5
    assert group.snap == TileRect(0, 2, 1, 5)


def test_group_windows_sorts_groups_by_origin_x_origin_y_width_height() -> None:
    windows = [
        CropWindow(10, 5, 8, 8),
        CropWindow(10, 5, 4, 9),
        CropWindow(10, 5, 4, 3),
        CropWindow(10, 2, 8, 8),
        CropWindow(0, 50, 8, 8),
    ]
    groups = group_windows(
        [(index, window) for index, window in enumerate(reversed(windows))],
        downsample=1,
        tile_x_dim=16,
        tile_y_dim=16,
    )
    assert [group.window for group in groups] == [
        CropWindow(0, 50, 8, 8),
        CropWindow(10, 2, 8, 8),
        CropWindow(10, 5, 4, 3),
        CropWindow(10, 5, 4, 9),
        CropWindow(10, 5, 8, 8),
    ]


def test_group_windows_empty_input() -> None:
    assert group_windows([], downsample=1, tile_x_dim=16, tile_y_dim=16) == []


def test_group_windows_rejects_non_positive_tile_dimensions() -> None:
    with pytest.raises(ValueError):
        group_windows([(1, CropWindow(0, 0, 8, 8))], downsample=1, tile_x_dim=0, tile_y_dim=16)


def test_image_key_without_window_uses_tile_snap() -> None:
    group = CropGroup(snap=TileRect(2, 5, 7, 9), downsample=2)
    assert group.image_key_for(31, "CPED") == "CPED_31_D2_X2-5_Y7-9"


def test_image_key_with_window_uses_pixel_extents() -> None:
    window = CropWindow(origin_x=1024, origin_y=2048, width=512, height=768)
    group = CropGroup(snap=TileRect(0, 1, 0, 1), downsample=4, window=window)
    # End is origin + size, not the tile snap.
    assert group.image_key_for(7, "CPED") == "CPED_7_D4_X1024-1536_Y2048-2816"


def test_image_key_sanitizes_volume_token() -> None:
    group = CropGroup(snap=TileRect(0, 1, 0, 1))
    assert group.image_key_for(1, " My Vol/ume! ") == "My_Vol_ume_1_D1_X0-1_Y0-1"
    assert group.image_key_for(1, "") == "volume_1_D1_X0-1_Y0-1"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://host/NeitzCPED/OData", "CPED"),
        ("https://host/NeitzTemporalMonkeyTokenTest/OData/Locations", "TemporalMonkey"),
        ("https://host/NeitzTemporalMonkeyTest/odata", "TemporalMonkey"),
        ("  https://host/a/b/RC1/OData  ", "RC1"),
        ("https://host/Neitz/OData", "Neitz"),
        ("https://host/Test/OData", "Test"),
        ("https://host/NeitzTest/OData", "Test"),
        ("https://host/Neitz_Test/OData", None),
        ("https://host/.../OData", None),
        ("https://host/Neitz CPED/OData", "CPED"),
        ("https://host/NeitzA/OData/NeitzB/OData", "A"),
        ("https://host/NeitzCPED", None),
        ("https://host/OData/NeitzCPED", None),
        ("https://host/", None),
        ("", None),
        (None, None),
    ],
)
def test_volume_name_from_odata_url(url: str | None, expected: str | None) -> None:
    assert volume_name_from_odata_url(url) == expected
