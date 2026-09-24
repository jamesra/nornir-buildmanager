"""Require*-aware iterate candidate resolution for PipelineManager."""

from __future__ import annotations

import enum
import os
import re
from collections.abc import Generator, Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree

from nornir_buildmanager.pipeline_exceptions import (
    PipelineArgumentNotFound,
    PipelineListIntersectionFailed,
    PipelineRegExSearchFailed,
)
from nornir_buildmanager.volumemanager import XElementWrapper

if TYPE_CHECKING:
    from nornir_buildmanager.pipelinemanager import ArgumentSet, PipelineManager

POINT_LOOKUP_MAX = 32
OR_PREDICATE_MAX = 8

_STOS_TRANSFORM_XPATH = 'Block/StosGroup/SectionMappings/Transform'
_DIRECT_XPATH_BASES = frozenset({
    'Block/Section',
    'Section',
    'Channel',
    'Filter',
    'Block',
    'Transform',
    _STOS_TRANSFORM_XPATH,
})
_REGEX_METACHARACTERS = frozenset('.^$*+?{}[]\\|()')


class MatchPatternKind(enum.Enum):
    """Classification of a RequireMatch RegEx value."""

    WILDCARD = enum.auto()
    LITERAL = enum.auto()
    REGEX = enum.auto()


@dataclass(frozen=True)
class MatchPattern:
    """Compiled RequireMatch pattern."""

    kind: MatchPatternKind
    literal: str | None = None
    regex: re.Pattern[str] | None = None
    original: str = ''


@dataclass(frozen=True)
class SetMembershipFilter:
    """RequireSetMembership filter on the iterated element."""

    pipeline_node: ElementTree.Element
    attribute: str
    list_ref: str
    allowed: frozenset[Any] | None
    list_resolved: bool


@dataclass(frozen=True)
class RequireMatchFilter:
    """RequireMatch filter on the iterated element."""

    pipeline_node: ElementTree.Element
    attribute: str
    regex_ref: str
    pattern: MatchPattern


IterateFilter = SetMembershipFilter | RequireMatchFilter


def use_legacy_iterate_fetch() -> bool:
    """Return True when optimized iterate fetching is disabled."""
    return os.environ.get('NORNIR_ITERATE_LEGACY_FETCH', '').lower() in ('1', 'true', 'yes')


def normalize_xpath_base(xpath: str) -> str:
    """Return the element path without predicate suffix."""
    xp = xpath.strip().strip('/')
    if '[' in xp:
        return xp.split('[', 1)[0]
    return xp


def is_direct_iterate_xpath(xpath: str) -> bool:
    """Return True when xpath is a v1 direct iterate path."""
    return normalize_xpath_base(xpath) in _DIRECT_XPATH_BASES


def _has_regex_metacharacters(value: str) -> bool:
    return any(character in _REGEX_METACHARACTERS for character in value)


def compile_match_pattern(regex_str: str) -> MatchPattern:
    """Classify and compile a RequireMatch RegEx string."""
    if regex_str == '*':
        return MatchPattern(kind=MatchPatternKind.WILDCARD, original=regex_str)

    anchored = re.match(r'\^(.+)\$$', regex_str)
    if anchored is not None:
        inner = anchored.group(1)
        if not _has_regex_metacharacters(inner):
            return MatchPattern(kind=MatchPatternKind.LITERAL, literal=inner, original=regex_str)

    if not _has_regex_metacharacters(regex_str):
        return MatchPattern(kind=MatchPatternKind.LITERAL, literal=regex_str, original=regex_str)

    return MatchPattern(kind=MatchPatternKind.REGEX, regex=re.compile(regex_str), original=regex_str)


def _normalize_allowed_values(list_of_valid: Any) -> frozenset[Any] | None:
    if list_of_valid is None:
        return None
    if isinstance(list_of_valid, frozenset):
        return list_of_valid
    if isinstance(list_of_valid, (list, tuple, set)):
        return frozenset(list_of_valid)
    if isinstance(list_of_valid, int):
        return frozenset([list_of_valid])
    return frozenset(list(list_of_valid))


def _get_element_attribute(element: XElementWrapper, attribute: str) -> Any:
    if hasattr(element, attribute):
        value = getattr(element, attribute)
        if value is not None:
            return value
    return element.attrib.get(attribute)


def _value_in_allowed_set(attribute_value: Any, allowed: frozenset[Any]) -> bool:
    if attribute_value in allowed:
        return True
    try:
        coerced_int = int(attribute_value)
        if coerced_int in allowed:
            return True
    except (TypeError, ValueError):
        pass
    attribute_text = str(attribute_value)
    if attribute_text in allowed:
        return True
    return False


def element_passes_set_membership(
        element: XElementWrapper,
        attribute: str,
        allowed: frozenset[Any] | None) -> bool:
    """Return True when membership filter passes or is unrestricted."""
    if allowed is None:
        return True
    attribute_value = _get_element_attribute(element, attribute)
    if attribute_value is None:
        return False
    return _value_in_allowed_set(attribute_value, allowed)


def element_passes_require_match(
        element: XElementWrapper,
        attribute: str,
        pattern: MatchPattern) -> bool:
    """Return True when RequireMatch filter passes."""
    if pattern.kind is MatchPatternKind.WILDCARD:
        return True

    attribute_value = element.attrib.get(attribute)
    if attribute_value is None:
        attribute_value = _get_element_attribute(element, attribute)
    if attribute_value is None:
        return False

    attribute_text = str(attribute_value)
    if pattern.kind is MatchPatternKind.LITERAL:
        assert pattern.literal is not None
        return attribute_text == pattern.literal

    assert pattern.regex is not None
    return pattern.regex.match(attribute_text) is not None


def element_passes_iterate_filters(
        element: XElementWrapper,
        filters: list[IterateFilter]) -> bool:
    """Return True when the element satisfies all iterate filters."""
    for iterate_filter in filters:
        if isinstance(iterate_filter, SetMembershipFilter):
            if not element_passes_set_membership(element, iterate_filter.attribute, iterate_filter.allowed):
                return False
        elif isinstance(iterate_filter, RequireMatchFilter):
            if not element_passes_require_match(element, iterate_filter.attribute, iterate_filter.pattern):
                return False
    return True


def collect_iterate_filters(
        iterate_node: ElementTree.Element,
        arg_set: ArgumentSet,
        volume_elem: XElementWrapper,
        get_search_root: Any) -> list[IterateFilter]:
    """Parse direct Require* children under an Iterate node."""
    del volume_elem, get_search_root
    filters: list[IterateFilter] = []

    for child in iterate_node:
        if not child.tag.startswith('Require'):
            continue

        if child.tag == 'RequireSetMembership':
            attribute = child.attrib.get('Attribute', 'Name')
            attribute = arg_set.SubstituteStringVariables(attribute)
            list_variable = child.attrib.get('List')
            if list_variable is None:
                continue

            found, list_of_valid = arg_set.TryGetSubstituteObject(list_variable)
            allowed = None
            if found:
                allowed = _normalize_allowed_values(list_of_valid)
            filters.append(SetMembershipFilter(
                pipeline_node=child,
                attribute=attribute,
                list_ref=list_variable,
                allowed=allowed,
                list_resolved=found,
            ))
        elif child.tag == 'RequireMatch':
            attribute = child.attrib.get('Attribute', 'Name')
            attribute = arg_set.SubstituteStringVariables(attribute)
            regex_ref = child.attrib.get('RegEx')
            if regex_ref is None:
                continue
            regex_str = arg_set.SubstituteStringVariables(regex_ref)
            filters.append(RequireMatchFilter(
                pipeline_node=child,
                attribute=attribute,
                regex_ref=regex_ref,
                pattern=compile_match_pattern(regex_str),
            ))

    return filters


def require_set_membership_or_raise(
        root_for_match: XElementWrapper,
        attribute: str,
        list_of_valid: Any,
        volume_elem: XElementWrapper,
        pipeline_node: ElementTree.Element) -> None:
    """Enforce RequireSetMembership, raising the same exceptions as the pipeline stage."""
    if list_of_valid is None:
        return

    allowed = _normalize_allowed_values(list_of_valid)
    attribute_value = _get_element_attribute(root_for_match, attribute)
    if attribute_value is None:
        raise PipelineArgumentNotFound(
            VolumeElem=volume_elem,
            PipelineNode=pipeline_node,
            argname=attribute,
        )

    if allowed is not None and not _value_in_allowed_set(attribute_value, allowed):
        raise PipelineListIntersectionFailed(
            VolumeElem=volume_elem,
            PipelineNode=pipeline_node,
            listOfValid=list_of_valid,
            attribValue=attribute_value,
        )


def require_match_or_raise(
        root_for_match: XElementWrapper,
        attribute: str,
        regex_str: str,
        volume_elem: XElementWrapper,
        pipeline_node: ElementTree.Element) -> None:
    """Enforce RequireMatch, raising the same exceptions as the pipeline stage."""
    pattern = compile_match_pattern(regex_str)
    if pattern.kind is MatchPatternKind.WILDCARD:
        return

    attribute_value = root_for_match.attrib.get(attribute)
    if attribute_value is None:
        raise PipelineArgumentNotFound(
            VolumeElem=volume_elem,
            PipelineNode=pipeline_node,
            argname=attribute,
        )

    if not element_passes_require_match(root_for_match, attribute, pattern):
        raise PipelineRegExSearchFailed(
            VolumeElem=volume_elem,
            PipelineNode=pipeline_node,
            regex=regex_str,
            attribValue=attribute_value,
        )


def _membership_filter_for_attribute(
        filters: list[IterateFilter],
        attribute: str) -> SetMembershipFilter | None:
    for iterate_filter in filters:
        if isinstance(iterate_filter, SetMembershipFilter) and iterate_filter.attribute == attribute:
            return iterate_filter
    return None


def _literal_match_filter_for_attribute(
        filters: list[IterateFilter],
        attribute: str) -> RequireMatchFilter | None:
    for iterate_filter in filters:
        if (isinstance(iterate_filter, RequireMatchFilter)
                and iterate_filter.attribute == attribute
                and iterate_filter.pattern.kind is MatchPatternKind.LITERAL):
            return iterate_filter
    return None


def _resolve_block_node(root_for_search: XElementWrapper) -> Any:
    from nornir_buildmanager.volumemanager.blocknode import BlockNode

    if isinstance(root_for_search, BlockNode):
        return root_for_search

    block_node = root_for_search.find('Block')
    return block_node


def _iter_sections_by_number(block_node: Any, numbers: Iterable[int]) -> Generator[XElementWrapper, None, None]:
    yield from block_node.IterSectionsByNumber(numbers)


def _findall_filtered(
        root_for_search: XElementWrapper,
        xpath: str,
        filters: list[IterateFilter]) -> list[XElementWrapper]:
    return [
        element for element in root_for_search.findall(xpath)
        if element_passes_iterate_filters(element, filters)
    ]


def _try_point_lookup_candidates(
        root_for_search: XElementWrapper,
        xpath: str,
        base_xpath: str,
        filters: list[IterateFilter]) -> list[XElementWrapper] | None:
    """Return candidate elements when a point-lookup strategy applies, else None."""

    section_membership = _membership_filter_for_attribute(filters, 'Number')
    if section_membership is not None and section_membership.list_resolved:
        if section_membership.allowed is not None and len(section_membership.allowed) == 0:
            return []

        if base_xpath in ('Block/Section', 'Section'):
            if section_membership.allowed is None:
                return None

            block_node = _resolve_block_node(root_for_search)
            if block_node is None:
                return None

            allowed_numbers = section_membership.allowed
            assert allowed_numbers is not None
            if len(allowed_numbers) <= POINT_LOOKUP_MAX:
                candidates = [
                    element for element in _iter_sections_by_number(block_node, allowed_numbers)
                    if element_passes_iterate_filters(element, filters)
                ]
                return candidates
            return None

    transform_membership = None
    for attribute in ('ControlSectionNumber', 'MappedSectionNumber'):
        transform_membership = _membership_filter_for_attribute(filters, attribute)
        if transform_membership is not None:
            membership_attribute = attribute
            break

    if transform_membership is not None and base_xpath == 'Transform':
        if not transform_membership.list_resolved:
            return None
        if transform_membership.allowed is not None and len(transform_membership.allowed) == 0:
            return []

        if transform_membership.allowed is None:
            return None

        allowed_values = transform_membership.allowed
        assert allowed_values is not None
        if len(allowed_values) <= POINT_LOOKUP_MAX:
            candidates: list[XElementWrapper] = []
            for value in allowed_values:
                element = root_for_search.find(f"Transform[@{membership_attribute}='{value}']")
                if element is not None and element_passes_iterate_filters(element, filters):
                    candidates.append(element)
            return candidates
        return None

    literal_child_lookup = {
        'Channel': ('Channel', 'Name'),
        'Filter': ('Filter', 'Name'),
        'Block': ('Block', 'Name'),
    }
    if base_xpath in literal_child_lookup:
        element_name, attribute_name = literal_child_lookup[base_xpath]
        literal_filter = _literal_match_filter_for_attribute(filters, attribute_name)
        if literal_filter is None:
            return None
        assert literal_filter.pattern.literal is not None
        element = root_for_search.GetChildByAttrib(element_name, attribute_name, literal_filter.pattern.literal)
        if element is None:
            return []
        if element_passes_iterate_filters(element, filters):
            return [element]
        return []

    if base_xpath == _STOS_TRANSFORM_XPATH:
        for attribute in ('ControlSectionNumber', 'MappedSectionNumber'):
            membership = _membership_filter_for_attribute(filters, attribute)
            if membership is None or not membership.list_resolved or membership.allowed is None:
                continue
            if len(membership.allowed) == 0:
                return []
            if len(membership.allowed) <= POINT_LOOKUP_MAX:
                candidates = []
                for value in membership.allowed:
                    element = root_for_search.find(f"Transform[@{attribute}='{value}']")
                    if element is not None and element_passes_iterate_filters(element, filters):
                        candidates.append(element)
                return candidates

    return None


def _try_long_list_section_candidates(
        root_for_search: XElementWrapper,
        base_xpath: str,
        filters: list[IterateFilter]) -> list[XElementWrapper] | None:
    section_membership = _membership_filter_for_attribute(filters, 'Number')
    if section_membership is None or not section_membership.list_resolved:
        return None
    if section_membership.allowed is None:
        return None
    if len(section_membership.allowed) <= POINT_LOOKUP_MAX:
        return None
    if base_xpath not in ('Block/Section', 'Section'):
        return None

    if base_xpath == 'Block/Section':
        section_xpath = 'Block/Section'
    else:
        section_xpath = 'Section'

    return _findall_filtered(root_for_search, section_xpath, filters)


def resolve_iterate_candidates(
        root_for_search: XElementWrapper,
        xpath: str,
        iterate_node: ElementTree.Element,
        arg_set: ArgumentSet,
        volume_elem: XElementWrapper,
        get_search_root: Any) -> Iterator[XElementWrapper]:
    """Yield iterate candidates using Require*-aware fetch when possible."""
    if use_legacy_iterate_fetch():
        yield from root_for_search.findall(xpath)
        return

    filters = collect_iterate_filters(iterate_node, arg_set, volume_elem, get_search_root)
    base_xpath = normalize_xpath_base(xpath)

    for iterate_filter in filters:
        if isinstance(iterate_filter, SetMembershipFilter) and iterate_filter.list_resolved:
            if iterate_filter.allowed is not None and len(iterate_filter.allowed) == 0:
                return

    point_candidates = _try_point_lookup_candidates(root_for_search, xpath, base_xpath, filters)
    if point_candidates is not None:
        yield from point_candidates
        return

    long_list_candidates = _try_long_list_section_candidates(root_for_search, base_xpath, filters)
    if long_list_candidates is not None:
        yield from long_list_candidates
        return

    if is_direct_iterate_xpath(xpath) and filters:
        yield from _findall_filtered(root_for_search, xpath, filters)
        return

    if filters:
        yield from _findall_filtered(root_for_search, xpath, filters)
        return

    yield from root_for_search.findall(xpath)


def legacy_iterate_candidates(
        root_for_search: XElementWrapper,
        xpath: str) -> Iterator[XElementWrapper]:
    """Yield candidates using the legacy unconditional findall path."""
    yield from root_for_search.findall(xpath)
