"""Tests for SectionMappingsNode.FindStosTransform caller sanity checks."""

from __future__ import annotations

import subprocess
import sys
import textwrap
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

    def test_mismatched_mapped_section_raises_under_python_o(self) -> None:
        """ValueError must survive assert stripping (python -O); asserts would not."""
        script = textwrap.dedent(
            """
            from nornir_buildmanager.volumemanager.sectionmappingsnode import SectionMappingsNode

            section_mapping = SectionMappingsNode.Create(MappedSectionNumber=4)
            try:
                section_mapping.FindStosTransform(
                    ControlSectionNumber=5,
                    ControlChannelName="",
                    ControlFilterName="",
                    MappedSectionNumber=99,
                    MappedChannelName="",
                    MappedFilterName="",
                )
            except ValueError:
                raise SystemExit(0)
            raise SystemExit(1)
            """
        )
        completed = subprocess.run(
            [sys.executable, "-O", "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=(completed.stdout or "") + (completed.stderr or ""),
        )
