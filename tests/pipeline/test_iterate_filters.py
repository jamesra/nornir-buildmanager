"""Tests for Require*-aware iterate candidate resolution."""

from __future__ import annotations

import os
import unittest
import xml.etree.ElementTree as etree
from unittest import mock

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

    def test_anchored_literal(self) -> None:
        pattern = iterate_filters.compile_match_pattern('^TEM$')
        self.assertEqual(pattern.kind, iterate_filters.MatchPatternKind.LITERAL)
        self.assertEqual(pattern.literal, 'TEM')

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

    def test_literal_channel_match_uses_get_child_by_attrib(self) -> None:
        channel = XElementWrapper(tag='Channel')
        channel.attrib['Name'] = 'TEM'

        root = mock.Mock()
        root.GetChildByAttrib.return_value = channel
        root.findall = mock.Mock(side_effect=AssertionError('findall should not be called'))

        iterate_node = etree.Element('Iterate', XPath='Channel', VariableName='ChannelNode')
        etree.SubElement(iterate_node, 'RequireMatch', {'Attribute': 'Name', 'RegEx': 'TEM'})

        arg_set = mock.Mock()
        arg_set.SubstituteStringVariables = lambda value: value
        arg_set.TryGetSubstituteObject = lambda value: (False, None)

        candidates = list(iterate_filters.resolve_iterate_candidates(
            root,
            'Channel',
            iterate_node,
            arg_set,
            root,
            lambda *args, **kwargs: root,
        ))

        self.assertEqual(candidates, [channel])
        root.GetChildByAttrib.assert_called_once_with('Channel', 'Name', 'TEM')

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


if __name__ == '__main__':
    unittest.main()
