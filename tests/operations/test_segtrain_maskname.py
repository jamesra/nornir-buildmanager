"""``annotation_crops.maskname.MaskName``: ``{image_key}_{location_id}.png`` format and parse."""

from __future__ import annotations

import dataclasses

import pytest
from annotation_crops.maskname import MaskName
from hypothesis import given, settings
from hypothesis import strategies as st

_image_keys = st.text(
    alphabet=st.characters(
        codec="ascii", exclude_characters="/\\:\x00", categories=("L", "N", "P", "S")
    ),
    min_size=0,
    max_size=20,
)
_location_ids = st.integers(min_value=0, max_value=10**12)


def test_format_joins_key_and_id() -> None:
    """The filename is the key, an underscore, the decimal id, then ``.png``."""
    assert MaskName("sec0012_tile3", 77).format() == "sec0012_tile3_77.png"


def test_parse_simple_name() -> None:
    """A plain ``key_id.png`` parses into its two parts."""
    assert MaskName.parse("abc_12.png") == MaskName("abc", 12)


def test_parse_uses_last_underscore() -> None:
    """Underscores inside the image key stay in the key (``rpartition``)."""
    assert MaskName.parse("a_b_c_9.png") == MaskName("a_b_c", 9)


def test_parse_strips_directories() -> None:
    """Only the final path component is considered, for either separator."""
    assert MaskName.parse("some/dir/key_5.png") == MaskName("key", 5)
    assert MaskName.parse("/abs/key_6.png") == MaskName("key", 6)


def test_parse_leading_zeros_become_int() -> None:
    """Leading zeros are accepted and lost: ``key_007`` parses as id 7."""
    parsed = MaskName.parse("key_007.png")
    assert parsed == MaskName("key", 7)
    assert parsed is not None and parsed.format() == "key_7.png"


@pytest.mark.parametrize(
    "name",
    [
        "key_12.jpg",  # not a png
        "key_12",  # no extension
        "key_12.PNG",  # extension match is case sensitive
        "key12.png",  # no underscore
        "12.png",  # no underscore, all digits
        "key_.png",  # empty tail
        "key_ab.png",  # non-digit tail
        "key_1a.png",  # mixed tail
        "key_-3.png",  # sign is not a digit
        "key_1.5.png",  # dot in tail
        "",
        ".png",
        "dir_5/key.png",  # underscore only in the directory part
    ],
)
def test_parse_rejects_non_mask_names(name: str) -> None:
    """Names that are not ``{key}_{digits}.png`` return None."""
    assert MaskName.parse(name) is None


def test_parse_empty_image_key() -> None:
    """``_5.png`` has an empty key; the parser accepts it (call sites filter keys)."""
    assert MaskName.parse("_5.png") == MaskName("", 5)


def test_frozen_equality_and_hash() -> None:
    """Instances are immutable, compare by value, and work as dict keys."""
    a = MaskName("k", 1)
    assert a == MaskName("k", 1)
    assert a != MaskName("k", 2)
    assert a != MaskName("j", 1)
    assert {a: "x"}[MaskName("k", 1)] == "x"
    assert len({a, MaskName("k", 1)}) == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.location_id = 3  # type: ignore[misc]


@settings(max_examples=200)
@given(image_key=_image_keys, location_id=_location_ids)
def test_format_parse_round_trip(image_key: str, location_id: int) -> None:
    """``parse(format(x)) == x`` for any key without path separators or drive colons (Path treats "1:" as a Windows drive) and any non-negative id."""
    name = MaskName(image_key, location_id)
    assert MaskName.parse(name.format()) == name


@settings(max_examples=200)
@given(image_key=_image_keys, location_id=_location_ids)
def test_format_matches_legacy_hand_formatted_name(image_key: str, location_id: int) -> None:
    """``format()`` equals the f-string that segmentationtraining sites used before they called it."""
    assert MaskName(image_key, location_id).format() == f"{image_key}_{location_id}.png"
    assert MaskName("k", 0).format() == "k_0.png"
