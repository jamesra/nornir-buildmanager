"""``-PixelSpacingOverrides`` corrects section pixel sizes for StosBrute's scale estimate only.

RC3's Gatan US4000 sections (2-776) record 2.176 nm/px but register at about 2.29 against the
OneView sections, so the brute's pre-scale put the true scale outside its search band. With
the per-period Gatan factors, 38 of 57 unregistered cross-camera pairs registered (Build2 RC3,
2026-11-03). Without the argument nothing changes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest
from nornir_buildmanager.operations import block
from nornir_buildmanager.operations.pixel_spacing_overrides import (
    PixelSpacingOverrides,
    load_pixel_spacing_overrides,
)

_PIPELINES = Path(block.__file__).resolve().parents[1] / 'config' / 'Pipelines.xml'


def _filter(section: int, units_per_pixel: float) -> SimpleNamespace:
    section_node = SimpleNamespace(Number=section)
    return SimpleNamespace(Scale=SimpleNamespace(X=units_per_pixel, Y=units_per_pixel),
                           FindParent=lambda name: section_node if name == 'Section' else None)


def test_parse_absolute_factor_ranges_and_comments() -> None:
    overrides = PixelSpacingOverrides.parse('# Gatan periods\n'
                                            '2-150\tx1.038\n'
                                            '\n'
                                            '151 - 300  *1.052  # second period\n'
                                            '259 6.528\n')
    assert len(overrides) == 3
    assert overrides.lookup(1) is None
    assert overrides.lookup(2).apply(2.176) == pytest.approx(2.176 * 1.038)
    assert overrides.lookup(200).apply(2.176) == pytest.approx(2.176 * 1.052)
    assert overrides.lookup(259).apply(2.176) == pytest.approx(6.528)  # last matching line wins


@pytest.mark.parametrize('line', ['abc 1.0', '5', '5 x', '10-2 1.0', '5 0', '5 -1.0', '5 1.0 extra'])
def test_malformed_lines_name_the_line(line: str) -> None:
    with pytest.raises(ValueError, match='line 2'):
        PixelSpacingOverrides.parse('1 2.0\n' + line, source='f.tsv')


def test_unset_argument_is_none_and_missing_file_fails(tmp_path: Path) -> None:
    for value in (None, '', '  ', 'None'):
        assert load_pixel_spacing_overrides(value) is None
    with pytest.raises(ValueError, match='not found'):
        load_pixel_spacing_overrides(str(tmp_path / 'missing.tsv'))
    path = tmp_path / 'o.tsv'
    path.write_text('3 x1.5\n', encoding='utf-8')
    assert load_pixel_spacing_overrides(str(path)).lookup(3).value == 1.5


def test_override_changes_only_the_brute_scale_factor() -> None:
    control = _filter(800, 2.143)   # OneView
    mapped = _filter(500, 2.176)    # Gatan, recorded too small
    assert block._IsotropicWarpedImageScaleFactor(control, mapped) == pytest.approx(2.176 / 2.143)

    overrides = PixelSpacingOverrides.parse('2-776 x1.052\n')
    factor = block._IsotropicWarpedImageScaleFactor(control, mapped, overrides, logging.getLogger(__name__))
    assert factor == pytest.approx(2.176 * 1.052 / 2.143)
    assert mapped.Scale.X == 2.176  # metadata untouched

    # The control side is corrected too, inverting the ratio's direction.
    assert block._IsotropicWarpedImageScaleFactor(mapped, control, overrides) == \
        pytest.approx(2.143 / (2.176 * 1.052))


def test_pairs_outside_the_overrides_keep_the_metadata_factor() -> None:
    overrides = PixelSpacingOverrides.parse('2-776 6.0\n')
    control, mapped = _filter(800, 2.143), _filter(801, 2.225)
    assert block._IsotropicWarpedImageScaleFactor(control, mapped, overrides) == pytest.approx(2.225 / 2.143)


def test_align_sections_passes_the_argument_to_stos_brute_all() -> None:
    root = ElementTree.parse(_PIPELINES).getroot()
    pipeline = next(p for p in root.iter('Pipeline') if p.get('Name') == 'AlignSections')
    arg = next(a for a in pipeline.iter('Argument') if a.get('flag') == '-PixelSpacingOverrides')
    assert (arg.get('dest'), arg.get('required'), arg.get('default')) == ('PixelSpacingOverrides', 'False', '')
    call = next(c for c in pipeline.iter('PythonCall') if c.get('Function') == 'block.StosBruteAll')
    assert call.get('PixelSpacingOverrides') == '#PixelSpacingOverrides'
