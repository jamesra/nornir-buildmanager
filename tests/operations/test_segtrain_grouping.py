"""group_snaps equivalence against the original list-then-min host selection."""

from __future__ import annotations

import random

from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect
from nornir_buildmanager.operations.segmentationtraining.grouping import group_snaps


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
