"""Unit tests for importers.filenameparser.ParseFilename."""

from __future__ import annotations

import os
import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

from nornir_buildmanager.importers.filenameparser import (
    FilenameInfo,
    ParseFilename,
    mapping,
)
from nornir_buildmanager.importers.pmg import pmgMappings
from nornir_buildmanager.importers.sectionimage import imageNameMappings


class TestMappingHelper(unittest.TestCase):
    def test_mapping_str_includes_default_and_type(self) -> None:
        m = mapping("Section", typefunc=int, default=None)
        self.assertIn("Section", str(m))
        self.assertIn("None", str(m))
        self.assertIn("int", str(m))

    def test_mapping_str_shows_attribute_and_type(self) -> None:
        m_int = mapping("Slide", typefunc=int)
        self.assertIn("Slide", str(m_int))
        self.assertIn("int", str(m_int))


class TestParseFilenameExamples(unittest.TestCase):
    def test_pmg_full_path_parses_all_fields(self) -> None:
        path = os.path.join("ignored", "dir", "1234_5432_7_JRA_40X_01_Glutamate.pmg")
        info = ParseFilename(path, pmgMappings)
        self.assertEqual(info.Slide, 1234)
        self.assertEqual(info.Block, "5432")
        self.assertEqual(info.Section, 7)
        self.assertEqual(info.Initials, "JRA")
        self.assertEqual(info.Mag, "40X")
        self.assertEqual(info.Spot, 1)
        self.assertEqual(info.Probe, "Glutamate")

    def test_pmg_without_section_token_uses_default_on_failed_int(self) -> None:
        path = "1234_5432_JRA_40X_01_Glutamate.pmg"
        info = ParseFilename(path, pmgMappings)
        self.assertEqual(info.Slide, 1234)
        self.assertEqual(info.Block, "5432")
        self.assertIsNone(info.Section)
        self.assertEqual(info.Initials, "JRA")
        self.assertEqual(info.Spot, 1)

    def test_section_image_applies_optional_defaults(self) -> None:
        path = "/vol/tiles/1001_TEM.tif"
        info = ParseFilename(path, imageNameMappings)
        self.assertEqual(info.Section, 1001)
        self.assertEqual(info.Channel, "TEM")
        self.assertIsNone(info.Filter)
        self.assertEqual(info.Downsample, 1)

    def test_section_image_full_mapping(self) -> None:
        path = "2002_ChannelA_FilterB_4.png"
        info = ParseFilename(path, imageNameMappings)
        self.assertEqual(info.Section, 2002)
        self.assertEqual(info.Channel, "ChannelA")
        self.assertEqual(info.Filter, "FilterB")
        self.assertEqual(info.Downsample, 4)

    def test_insufficient_underscore_segments_raises(self) -> None:
        with self.assertRaises(Exception) as ctx:
            ParseFilename("only_one.pmg", pmgMappings)
        self.assertIn("Insufficient arguments", str(ctx.exception))

    def test_too_many_segments_raises(self) -> None:
        extra = "_".join(["1"] * (len(pmgMappings) + 2))
        with self.assertRaises(Exception) as ctx:
            ParseFilename(f"{extra}.pmg", pmgMappings)
        self.assertIn("Too many underscores", str(ctx.exception))

    def test_non_convertible_required_field_raises(self) -> None:
        path = "notint_5432_JRA_40X_01_Glutamate.pmg"
        with self.assertRaises(Exception) as ctx:
            ParseFilename(path, pmgMappings)
        self.assertIn("Cannot parse parameter", str(ctx.exception))
        self.assertIn("Slide", str(ctx.exception))

    def test_callable_default_invoked(self) -> None:
        calls: list[str] = []

        def factory() -> str:
            calls.append("ok")
            return "generated"

        mappings = [
            mapping("A", typefunc=int),
            mapping("B", typefunc=int, default=factory),
        ]
        info = ParseFilename("1_notint", mappings)
        self.assertEqual(info.A, 1)
        self.assertEqual(info.B, "generated")
        self.assertEqual(calls, ["ok"])


class TestParseFilenameProperty(unittest.TestCase):
    @given(
        slide=st.integers(min_value=0, max_value=9999),
        block=st.from_regex(r"[A-Za-z0-9]{1,8}", fullmatch=True),
        initials=st.from_regex(r"[A-Za-z]{1,4}", fullmatch=True),
        mag=st.sampled_from(["20X", "40X", "60X"]),
        spot=st.integers(min_value=0, max_value=99),
        probe=st.from_regex(r"[A-Za-z0-9]{1,12}", fullmatch=True),
    )
    @settings(max_examples=50, deadline=None)
    def test_pmg_round_trip_without_section_field(
        self,
        slide: int,
        block: str,
        initials: str,
        mag: str,
        spot: int,
        probe: str,
    ) -> None:
        base = f"{slide}_{block}_{initials}_{mag}_{spot:02d}_{probe}"
        info = ParseFilename(f"/tmp/{base}.pmg", pmgMappings)
        self.assertEqual(info.Slide, slide)
        self.assertEqual(info.Block, block)
        self.assertIsNone(info.Section)
        self.assertEqual(info.Initials, initials)
        self.assertEqual(info.Mag, mag)
        self.assertEqual(info.Spot, spot)
        self.assertEqual(info.Probe, probe)

    @given(
        section=st.integers(min_value=1, max_value=9999),
        channel=st.from_regex(r"[A-Za-z][A-Za-z0-9]{0,9}", fullmatch=True),
    )
    @settings(max_examples=40, deadline=None)
    def test_section_image_two_part_names_get_filter_default(
        self, section: int, channel: str
    ) -> None:
        base = f"{section}_{channel}"
        info = ParseFilename(base + ".png", imageNameMappings)
        self.assertEqual(info.Section, section)
        self.assertEqual(info.Channel, channel)
        self.assertIsNone(info.Filter)
        self.assertEqual(info.Downsample, 1)


class TestFilenameInfo(unittest.TestCase):
    def test_kwargs_update_attributes(self) -> None:
        info = FilenameInfo(Slide=1, Block="b")
        self.assertEqual(info.Slide, 1)
        self.assertEqual(info.Block, "b")
