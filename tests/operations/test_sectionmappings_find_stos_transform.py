"""Tests for SectionMappingsNode.FindStosTransform caller sanity checks."""

from __future__ import annotations

import unittest

from nornir_buildmanager.volumemanager.sectionmappingsnode import SectionMappingsNode
from nornir_buildmanager.volumemanager.transformnode import TransformNode


class TestFindStosTransformMappedSection(unittest.TestCase):
    def test_mismatched_mapped_section_number_raises_value_error(self) -> None:
        section_mapping = SectionMappingsNode.Create(MappedSectionNumber=4)
        transform = TransformNode.Create(Name="stos", Type="Grid", Path="4-5.stos")
        transform.ControlSectionNumber = 5
        transform.MappedSectionNumber = 4
        section_mapping.append(transform)

        with self.assertRaises(ValueError):
            section_mapping.FindStosTransform(
                ControlSectionNumber=5,
                ControlChannelName="",
                ControlFilterName="",
                MappedSectionNumber=99,
                MappedChannelName="",
                MappedFilterName="",
            )
