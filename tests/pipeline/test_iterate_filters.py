"""Tests for Require*-aware iterate candidate resolution."""

from __future__ import annotations

import os
import unittest
import xml.etree.ElementTree as etree
from unittest import mock

from hypothesis import example, given, settings
from hypothesis import strategies as st

import nornir_buildmanager.pipelinemanager_iterate_filters as iterate_filters
from nornir_buildmanager.volumemanager import XElementWrapper


class CompileMatchPatternTests(unittest.TestCase):
    """Tests for RequireMatch pattern compilation."""

    def test_wildcard(self) -> None:
        pattern = iterate_filters.compile_match_pattern('*')
        self.assertEqual(pattern.kind, iterate_filters.MatchPatternKind.WILDCARD)

    def test_plain_literal(self) -> None:
        pattern = iterate_filters.compile_match_pattern('TEM')
        self.assertEqual(pattern.kind, iterate_filters.MatchPatternKind.LITERAL)
        self.assertEqual(pattern.literal, 'TEM')
        self.assertFalse(pattern.exact, 'a bare literal keeps re.match prefix semantics')

    def test_anchored_literal(self) -> None:
        pattern = iterate_filters.compile_match_pattern('^TEM$')
        self.assertEqual(pattern.kind, iterate_filters.MatchPatternKind.LITERAL)
        self.assertEqual(pattern.literal, 'TEM')
        self.assertTrue(pattern.exact)

    def test_regex(self) -> None:
        pattern = iterate_filters.compile_match_pattern('(?![M|m]ask)')
        self.assertEqual(pattern.kind, iterate_filters.MatchPatternKind.REGEX)


class FilterEvaluationTests(unittest.TestCase):
    """Tests for shared Require* evaluation helpers."""

    def test_set_membership_none_allows_all(self) -> None:
        element = XElementWrapper(tag='Section')
        element.attrib['Number'] = '691'
        self.assertTrue(iterate_filters.element_passes_set_membership(element, 'Number', None))

    def test_set_membership_filters_values(self) -> None:
        element = XElementWrapper(tag='Section')
        element.attrib['Number'] = '691'
        allowed = frozenset([691, 693])
        self.assertTrue(iterate_filters.element_passes_set_membership(element, 'Number', allowed))
        self.assertFalse(iterate_filters.element_passes_set_membership(element, 'Number', frozenset([692])))


class ResolveIterateCandidatesTests(unittest.TestCase):
    """Tests for iterate candidate resolution strategies."""

    def _build_arg_set(self, sections: list[int] | None) -> mock.Mock:
        arg_set = mock.Mock()
        arg_set.SubstituteStringVariables = lambda value: value

        def try_get(value: str) -> tuple[bool, list[int] | None]:
            if value == '#Sections':
                return True, sections
            return False, None

        arg_set.TryGetSubstituteObject = try_get
        return arg_set

    def _build_iterate_node(self, with_sections_filter: bool = True) -> etree.Element:
        iterate_node = etree.Element('Iterate', XPath='Block/Section', VariableName='section_node')
        if with_sections_filter:
            etree.SubElement(
                iterate_node,
                'RequireSetMembership',
                {'Attribute': 'Number', 'List': '#Sections'},
            )
        return iterate_node

    def test_short_section_list_uses_point_lookup(self) -> None:
        block = mock.Mock()
        section_691 = XElementWrapper(tag='Section')
        section_691.attrib['Number'] = '691'
        block.IterSectionsByNumber.return_value = iter([section_691])

        root = mock.Mock()
        root.find.return_value = block
        root.findall = mock.Mock(side_effect=AssertionError('findall should not be called'))

        iterate_node = self._build_iterate_node()
        arg_set = self._build_arg_set([691])

        candidates = list(iterate_filters.resolve_iterate_candidates(
            root,
            'Block/Section',
            iterate_node,
            arg_set,
            root,
            lambda *args, **kwargs: root,
        ))

        self.assertEqual(candidates, [section_691])
        block.IterSectionsByNumber.assert_called_once()
        requested_numbers = block.IterSectionsByNumber.call_args[0][0]
        self.assertIn(691, list(requested_numbers))

    def test_long_section_list_uses_findall_and_filter(self) -> None:
        section_numbers = list(range(1000, 1050))
        sections = []
        for number in section_numbers[:3]:
            section = XElementWrapper(tag='Section')
            section.attrib['Number'] = str(number)
            sections.append(section)

        root = mock.Mock()
        root.findall.return_value = sections

        iterate_node = self._build_iterate_node()
        arg_set = self._build_arg_set(section_numbers)

        candidates = list(iterate_filters.resolve_iterate_candidates(
            root,
            'Block/Section',
            iterate_node,
            arg_set,
            root,
            lambda *args, **kwargs: root,
        ))

        root.findall.assert_called_once_with('Block/Section')
        self.assertEqual(len(candidates), 3)

    def test_sections_omitted_processes_findall_results(self) -> None:
        sections = [XElementWrapper(tag='Section'), XElementWrapper(tag='Section')]
        root = mock.Mock()
        root.findall.return_value = sections

        iterate_node = self._build_iterate_node(with_sections_filter=False)
        arg_set = self._build_arg_set(None)

        candidates = list(iterate_filters.resolve_iterate_candidates(
            root,
            'Block/Section',
            iterate_node,
            arg_set,
            root,
            lambda *args, **kwargs: root,
        ))

        self.assertEqual(candidates, sections)

    def _channel_candidates(self, regex: str) -> list[str]:
        """Resolve Iterate XPath='Channel' under a section with three channels."""
        section = XElementWrapper(tag='Section')
        for name in ('TEM', 'TEM2', 'Registered_TEM'):
            channel = XElementWrapper(tag='Channel')
            channel.attrib['Name'] = name
            section.append(channel)
        iterate_node = etree.Element('Iterate', XPath='Channel', VariableName='ChannelNode')
        etree.SubElement(iterate_node, 'RequireMatch', {'Attribute': 'Name', 'RegEx': regex})
        arg_set = mock.Mock()
        arg_set.SubstituteStringVariables = lambda value: value
        arg_set.TryGetSubstituteObject = lambda value: (False, None)
        candidates = iterate_filters.resolve_iterate_candidates(
            section, 'Channel', iterate_node, arg_set, section, lambda *args, **kwargs: section)
        return sorted(c.attrib['Name'] for c in candidates)

    def test_bare_literal_is_a_prefix_match_like_re_match(self) -> None:
        """-Channels Registered must select Registered_TEM (ExportImages exported nothing, RC3 2026-11-02)."""
        self.assertEqual(self._channel_candidates('Registered'), ['Registered_TEM'])
        self.assertEqual(self._channel_candidates('TEM'), ['TEM', 'TEM2'])

    def test_anchored_literal_is_exact(self) -> None:
        self.assertEqual(self._channel_candidates('^TEM$'), ['TEM'])

    def test_prefix_lookup_loads_only_matching_children(self) -> None:
        section = XElementWrapper(tag='Section')
        for name in ('TEM', 'Registered_TEM'):
            link = XElementWrapper(tag='Channel_Link')
            link.attrib['Name'] = name
            section.append(link)
        looked_up: list[str] = []

        def lookup(element_name, attribute, value):
            looked_up.append(value)
            return None

        section.GetChildByAttrib = lookup  # type: ignore[method-assign]
        iterate_node = etree.Element('Iterate', XPath='Channel', VariableName='ChannelNode')
        etree.SubElement(iterate_node, 'RequireMatch', {'Attribute': 'Name', 'RegEx': 'Registered'})
        arg_set = mock.Mock()
        arg_set.SubstituteStringVariables = lambda value: value
        arg_set.TryGetSubstituteObject = lambda value: (False, None)
        list(iterate_filters.resolve_iterate_candidates(
            section, 'Channel', iterate_node, arg_set, section, lambda *args, **kwargs: section))

        self.assertEqual(looked_up, ['Registered_TEM'])

    def test_empty_section_list_yields_no_candidates(self) -> None:
        block = mock.Mock()
        root = mock.Mock()
        root.find.return_value = block
        root.findall = mock.Mock(side_effect=AssertionError('findall should not be called'))

        iterate_node = self._build_iterate_node()
        arg_set = self._build_arg_set([])

        candidates = list(iterate_filters.resolve_iterate_candidates(
            root,
            'Block/Section',
            iterate_node,
            arg_set,
            root,
            lambda *args, **kwargs: root,
        ))

        self.assertEqual(candidates, [])

    def test_legacy_and_optimized_parity_for_filtered_sections(self) -> None:
        sections = []
        for number in (690, 691, 693):
            section = XElementWrapper(tag='Section')
            section.attrib['Number'] = str(number)
            sections.append(section)

        root = mock.Mock()
        root.findall.return_value = list(sections)

        iterate_node = self._build_iterate_node()
        arg_set = self._build_arg_set([691])

        with mock.patch.dict(os.environ, {'NORNIR_ITERATE_LEGACY_FETCH': '1'}):
            legacy = list(iterate_filters.resolve_iterate_candidates(
                root,
                'Block/Section',
                iterate_node,
                arg_set,
                root,
                lambda *args, **kwargs: root,
            ))

        block = mock.Mock()
        block.IterSectionsByNumber.return_value = iter([sections[1]])
        root.find.return_value = block
        root.findall = mock.Mock(return_value=list(sections))

        if 'NORNIR_ITERATE_LEGACY_FETCH' in os.environ:
            del os.environ['NORNIR_ITERATE_LEGACY_FETCH']

        optimized = list(iterate_filters.resolve_iterate_candidates(
            root,
            'Block/Section',
            iterate_node,
            arg_set,
            root,
            lambda *args, **kwargs: root,
        ))

        self.assertEqual(len(legacy), 3)
        self.assertEqual([candidate.attrib['Number'] for candidate in optimized], ['691'])


_STOS_XPATH = 'Block/StosGroup/SectionMappings/Transform'
_STOS_MAPPED_SECTIONS = range(1, 51)


def _build_stos_volume() -> XElementWrapper:
    """Volume with two StosGroups, each mapping section n onto n - 1 for n in 1..50."""
    volume = XElementWrapper(tag='Volume')
    block = XElementWrapper(tag='Block', attrib={'Name': 'TEM'})
    volume.append(block)
    for group_name in ('Grid16', 'Grid8'):
        group = XElementWrapper(tag='StosGroup', attrib={'Name': group_name})
        block.append(group)
        for mapped in _STOS_MAPPED_SECTIONS:
            mappings = XElementWrapper(tag='SectionMappings', attrib={'MappedSectionNumber': str(mapped)})
            group.append(mappings)
            mappings.append(XElementWrapper(tag='Transform', attrib={
                'Name': f'{group_name}:{mapped}-{mapped - 1}',
                'ControlSectionNumber': str(mapped - 1),
                'MappedSectionNumber': str(mapped),
            }))
    return volume


class StosTransformIterateTests(unittest.TestCase):
    """Cleanup iterates Block/StosGroup/SectionMappings/Transform with RequireSetMembership on -Sections."""

    @given(
        attribute=st.sampled_from(['ControlSectionNumber', 'MappedSectionNumber']),
        sections=st.sets(st.integers(min_value=-1, max_value=52),
                         max_size=iterate_filters.POINT_LOOKUP_MAX + 8),
    )
    @example(attribute='ControlSectionNumber', sections={2, 3})
    @example(attribute='MappedSectionNumber', sections={2, 3})
    @settings(max_examples=60, deadline=None)
    def test_candidates_are_every_transform_whose_section_is_listed(
            self, attribute: str, sections: set[int]) -> None:
        """Short -Sections lists found no StosGroup transforms, so Cleanup -Sections skipped their validation."""
        volume = _build_stos_volume()
        iterate_node = etree.Element('Iterate', XPath=_STOS_XPATH, VariableName='TransformNode')
        etree.SubElement(iterate_node, 'RequireSetMembership', {'Attribute': attribute, 'List': '#Sections'})
        arg_set = mock.Mock()
        arg_set.SubstituteStringVariables = lambda value: value
        arg_set.TryGetSubstituteObject = (
            lambda value: (True, sorted(sections)) if value == '#Sections' else (False, None))

        candidates = iterate_filters.resolve_iterate_candidates(
            volume, _STOS_XPATH, iterate_node, arg_set, volume, lambda *args, **kwargs: volume)

        expected = [
            transform.attrib['Name'] for transform in volume.findall(_STOS_XPATH)
            if int(transform.attrib[attribute]) in sections
        ]
        self.assertEqual([candidate.attrib['Name'] for candidate in candidates], expected)


class SortIterateCandidatesTests(unittest.TestCase):
    """Section iterate order is ascending number, independent of XML order."""

    def test_high_z_first_xml_sorts_ascending(self) -> None:
        elements = [
            XElementWrapper(tag='Section', attrib={'Number': str(number)})
            for number in (1297, 1296, 324, 1000)
        ]
        ordered = iterate_filters.sort_iterate_candidates(elements, 'Number')
        self.assertEqual(
            [element.attrib['Number'] for element in ordered],
            ['324', '1000', '1296', '1297'],
        )


if __name__ == '__main__':
    unittest.main()
