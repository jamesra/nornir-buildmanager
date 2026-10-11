"""StosMapNode.IsValid: pin duplicate-control merges, NonStos removal, and duplicate mapped removal.

The reference below is the pre-change body (RemoveDuplicateControlEntries called for every
Mapping, list membership for seen and NonStos sections). The property checks the current
method against it on generated maps under a Block, comparing the return value and the
serialized Block, so a change in which Mapping elements are merged, removed, or rewritten
shows up.
"""

from __future__ import annotations

from xml.etree import ElementTree

from hypothesis import example, given, settings
from hypothesis import strategies as st
from nornir_buildmanager.volumemanager import MappingNode, XElementWrapper
from nornir_buildmanager.volumemanager.stosmapnode import StosMapNode
from nornir_shared import misc

_section = st.integers(min_value=0, max_value=30)


def _reference_is_valid(stos_map: StosMapNode) -> tuple[bool, str]:
    """Pre-change IsValid body for a StosMap whose parent holds NonStosSectionNumbers."""
    NonStosSectionsNode = stos_map.Parent.find('NonStosSectionNumbers')  # type: ignore[union-attr]
    AlreadyMappedSections = []
    if NonStosSectionsNode is None:
        return XElementWrapper.IsValid(stos_map)

    NonStosSections = misc.ListFromAttribute(NonStosSectionsNode.text)
    MappingNodes = list(stos_map.findall('Mapping'))
    for i in range(len(MappingNodes) - 1, -1, -1):
        stos_map.RemoveDuplicateControlEntries(MappingNodes[i].Control)

    MappingNodes = list(stos_map.findall('Mapping'))
    for i in range(len(MappingNodes) - 1, -1, -1):
        mapping_node = MappingNodes[i]
        if mapping_node.Control in NonStosSections:
            mapping_node.Clean()
        else:
            mapped_sections = list(mapping_node.Mapped)
            for isection in range(len(mapped_sections) - 1, -1, -1):
                mapped_section = mapped_sections[isection]
                if mapped_section in AlreadyMappedSections and not stos_map.AllowDuplicates:
                    del mapped_sections[isection]
                else:
                    AlreadyMappedSections.append(mapped_section)

            if len(mapped_sections) == 0:
                mapping_node.Clean()
            elif len(mapped_sections) != len(mapping_node.Mapped):
                mapping_node.Mapped = mapped_sections

    return XElementWrapper.IsValid(stos_map)


def _build_block(mappings: list[tuple[int, list[int]]], non_stos: list[int] | None,
                 allow_duplicates: str | None) -> tuple[XElementWrapper, StosMapNode]:
    """Build a Block holding an optional NonStosSectionNumbers element and one StosMap."""
    block = XElementWrapper(tag='Block')
    if non_stos is not None:
        non_stos_node = XElementWrapper(tag='NonStosSectionNumbers')
        non_stos_node.text = ','.join(str(n) for n in non_stos)
        block.append(non_stos_node)

    stos_map = StosMapNode.Create(Name='PotentialRegistrationChain')
    if allow_duplicates is not None:
        stos_map.attrib['AllowDuplicates'] = allow_duplicates
    for control, mapped in mappings:
        stos_map.append(MappingNode.Create(control, mapped))
    block.append(stos_map)
    return block, stos_map


def _xml(element: XElementWrapper) -> bytes:
    """Serialized element with CreationDate blanked (stamped per construction at 1 s resolution)."""
    copy = ElementTree.fromstring(ElementTree.tostring(element))
    for child in copy.iter():
        if 'CreationDate' in child.attrib:
            child.attrib['CreationDate'] = ''
    return ElementTree.tostring(copy)


_mappings = st.lists(st.tuples(_section, st.lists(_section, min_size=1, max_size=4, unique=True)), max_size=20)


@given(mappings=_mappings,
       non_stos=st.none() | st.lists(_section, max_size=6),
       allow_duplicates=st.sampled_from([None, '0', '1', 'False', 'true']))
@example(mappings=[(1, [2]), (3, [2, 4]), (1, [5])], non_stos=[], allow_duplicates='0')
@example(mappings=[(1, [2]), (3, [4]), (3, [2])], non_stos=[3], allow_duplicates='1')
@example(mappings=[(1, [2]), (2, [2])], non_stos=[], allow_duplicates='0')
@settings(max_examples=400, deadline=None)
def test_matches_reference(mappings: list[tuple[int, list[int]]], non_stos: list[int] | None,
                           allow_duplicates: str | None) -> None:
    """Same return value and Block XML as the pre-change reference."""
    expected_block, expected_map = _build_block(mappings, non_stos, allow_duplicates)
    actual_block, actual_map = _build_block(mappings, non_stos, allow_duplicates)

    expected = _reference_is_valid(expected_map)
    actual = actual_map.IsValid()

    assert actual == expected
    assert _xml(actual_block) == _xml(expected_block)


def test_duplicate_controls_merge_into_first_mapping() -> None:
    """Two Mapping elements with one control merge into the first, which keeps both mapped sections."""
    _, stos_map = _build_block([(1, [2]), (3, [4]), (1, [5])], [], None)

    stos_map.IsValid()

    assert [(m.Control, m.Mapped) for m in stos_map.Mappings] == [(1, frozenset({2, 5})), (3, frozenset({4}))]


def test_non_stos_control_removed() -> None:
    """A Mapping whose control is listed in NonStosSectionNumbers is removed."""
    _, stos_map = _build_block([(1, [2]), (3, [4])], [3, 9], None)

    stos_map.IsValid()

    assert [m.Control for m in stos_map.Mappings] == [1]


def test_duplicate_mapped_removed_when_duplicates_disallowed() -> None:
    """With AllowDuplicates=0 a section mapped by two controls stays only on the later Mapping."""
    _, stos_map = _build_block([(1, [2, 5]), (3, [2])], [], '0')

    stos_map.IsValid()

    assert [(m.Control, m.Mapped) for m in stos_map.Mappings] == [(1, frozenset({5})), (3, frozenset({2}))]
