"""A volume is either all crushed or all legacy: an import of the other kind is refused (RC3, 2026-11-10)."""

from __future__ import annotations

import types
import unittest

import pytest

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.importers import shared
from nornir_imageregistration import noise_crush

CRUSHED = noise_crush.CrushParams(step=40, offset=0, bits=8, sigma=16.0, k=2.5, source_dtype="uint16").to_attributes()


def _volume(*others: tuple[int, dict | None]):
    """Channel of section 5 in a block that also holds the given (number, Raw8 attributes or None) sections."""
    def section(number, attrib):
        channel = types.SimpleNamespace(
            GetFilter=lambda name, a=attrib: None if a is None or name != 'Raw8' else types.SimpleNamespace(attrib=a))
        return types.SimpleNamespace(Number=number, GetChannel=lambda name, c=channel: c)

    mine = types.SimpleNamespace(Number=5)
    block = types.SimpleNamespace(Sections=[mine] + [section(n, a) for n, a in others])
    mine.Parent = block
    channel = types.SimpleNamespace(Name='TEM', Parent=mine)
    return channel


class TestRequireSameCrushKind(unittest.TestCase):

    def test_crush_import_into_a_crushed_volume_is_allowed(self) -> None:
        shared.RequireSameCrushKind(_volume((4, CRUSHED), (6, CRUSHED)), will_crush=True)

    def test_legacy_import_into_a_legacy_volume_is_allowed(self) -> None:
        shared.RequireSameCrushKind(_volume((4, {'MinIntensityCutoff': '1'})), will_crush=False)

    def test_a_volume_with_no_other_filters_accepts_either(self) -> None:
        for will_crush in (True, False):
            shared.RequireSameCrushKind(_volume((4, None)), will_crush=will_crush)

    def test_crush_import_into_a_legacy_volume_is_refused(self) -> None:
        with pytest.raises(NornirUserException, match='section 4 of this volume is legacy'):
            shared.RequireSameCrushKind(_volume((4, {'MinIntensityCutoff': '1'})), will_crush=True)

    def test_legacy_import_into_a_crushed_volume_is_refused(self) -> None:
        with pytest.raises(NornirUserException, match='section 4 of this volume is crushed'):
            shared.RequireSameCrushKind(_volume((4, CRUSHED), (6, CRUSHED)), will_crush=False)

    def test_the_section_itself_is_not_checked(self) -> None:
        """Its own existing filter keeps its kind (ResolveCrushMode), so it is not a mix."""
        channel = _volume()
        shared.RequireSameCrushKind(channel, will_crush=True)


if __name__ == '__main__':
    unittest.main()
