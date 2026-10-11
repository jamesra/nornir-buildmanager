"""StosMapNode.ClearMissingSections: pin removals, return value, and serialized XML.

The reference below is the pre-change body (list membership). The property checks the
current method against it on generated maps, so a change in which Mapping elements or
mapped numbers are removed, in the returned flag, or in the saved XML text shows up.
"""

from __future__ import annotations

from collections.abc import Collection
from xml.etree import ElementTree

from hypothesis import example, given, settings
from hypothesis import strategies as st
from nornir_buildmanager.volumemanager import MappingNode
from nornir_buildmanager.volumemanager.stosmapnode import StosMapNode

_section = st.integers(min_value=0, max_value=5000)


def _reference_clear_missing_sections(stos_map: StosMapNode, existing_section_numbers: Collection[int]) -> bool:
    """Pre-change ClearMissingSections body, membership tested against the argument as given."""
    missing_controls = [m for m in stos_map.Mappings if m.Control not in existing_section_numbers]

    missing_section_numbers = frozenset(mc.Control for mc in missing_controls)
    for control_mapping in missing_controls:
        stos_map.remove(control_mapping)

    missing_mappings = [m for m in stos_map.Mappings if m.Mapped & missing_section_numbers]
    found_missing_mappings = False
    for missing_mapping in missing_mappings:
        for missing_section in missing_mapping.Mapped & missing_section_numbers:
            missing_mapping.RemoveMapping(missing_section)
            found_missing_mappings = True

    return bool(missing_section_numbers) or found_missing_mappings


def _build_map(mappings: list[tuple[int, list[int]]]) -> StosMapNode:
    """Build a standalone StosMap with one Mapping element per (control, mapped) pair."""
    stos_map = StosMapNode.Create(Name='PotentialRegistrationChain')
    for control, mapped in mappings:
        stos_map.append(MappingNode.Create(control, mapped))
    return stos_map


def _xml(stos_map: StosMapNode) -> bytes:
    """Serialized form of the map as it would be written to VolumeData.xml.

    CreationDate is stamped at construction with one-second resolution, so two maps built
    from the same input can straddle a second; it is blanked so only content is compared.
    """
    text = ElementTree.tostring(stos_map)
    copy = ElementTree.fromstring(text)
    for element in copy.iter():
        if 'CreationDate' in element.attrib:
            element.attrib['CreationDate'] = ''
    return ElementTree.tostring(copy)


_mappings = st.lists(st.tuples(_section, st.lists(_section, min_size=1, max_size=4, unique=True)), max_size=25)


@given(mappings=_mappings, existing=st.lists(_section, max_size=40))
@example(mappings=[(1, [2]), (3, [2, 4]), (4, [5])], existing=[1, 2, 4, 5])
@example(mappings=[(1, [2]), (1, [3])], existing=[])
@example(mappings=[], existing=[1, 2])
@settings(max_examples=300, deadline=None)
def test_matches_reference(mappings: list[tuple[int, list[int]]], existing: list[int]) -> None:
    """Same removals, return value, and XML bytes as the list-membership reference."""
    expected_map = _build_map(mappings)
    actual_map = _build_map(mappings)

    expected = _reference_clear_missing_sections(expected_map, existing)
    actual = actual_map.ClearMissingSections(existing)

    assert actual == expected
    assert _xml(actual_map) == _xml(expected_map)


def test_removes_missing_control_and_mapped_references() -> None:
    """A control not in the section list is dropped, and other Mappings stop mapping it."""
    stos_map = _build_map([(1, [2]), (3, [2, 4]), (4, [3, 5])])

    assert stos_map.ClearMissingSections([1, 2, 4, 5]) is True

    remaining = {m.Control: m.Mapped for m in stos_map.Mappings}
    assert remaining == {1: frozenset({2}), 4: frozenset({5})}


def test_nothing_missing_returns_false_and_leaves_map_unchanged() -> None:
    """When every control exists the map is untouched and the call reports no change."""
    stos_map = _build_map([(1, [2]), (2, [3])])
    before = _xml(stos_map)

    assert stos_map.ClearMissingSections([1, 2, 3]) is False
    assert _xml(stos_map) == before


def test_accepts_single_use_iterable() -> None:
    """A generator argument is read once, so every Mapping is tested against all sections."""
    stos_map = _build_map([(3, [4]), (2, [3]), (1, [2])])

    assert stos_map.ClearMissingSections(n for n in (1, 2, 3, 4)) is False
    assert [m.Control for m in stos_map.Mappings] == [3, 2, 1]
