"""DM4 scale units must convert to nanometres, or stop the import (review #155).

``ChannelObj.SetScale`` takes nanometres. The conversion multiplied by 1000 for ``'µm'`` or
``'um'`` and by 1 for everything else, with no else branch, so ``'nm'`` was correct only
because the fallback happened to match it. Measured against the true factors:

    units   true nm/unit   old scalar   error factor
    'm'            1e+09            1        1e-09
    'mm'           1e+06            1        1e-06
    'µm'            1000         1000            1     (U+00B5 MICRO SIGN)
    'μm'            1000            1        0.001     (U+03BC GREEK SMALL LETTER MU)
    'um'            1000         1000            1
    'nm'               1            1            1
    'pm'           0.001            1         1000
    'Å'              0.1            1           10

The `'μm'` row is the one worth dwelling on, and the issue did not mention it. Micro has two
codepoints, and the literal in the old comparison was MICRO SIGN. A file writing GREEK SMALL
LETTER MU produced a *visually identical* string that fell through to the fallback and came
out 1000x too small -- the failure would have been invisible in any log, diff, or code review
that reads the two as the same character. NFKC normalisation folds them together, which also
picks up ANGSTROM SIGN (U+212B) against U+00C5 for free.

An unrecognised unit now raises rather than assuming nanometres. A scale silently wrong by a
factor of a million propagates into registration and volume geometry, where it costs far more
to discover than a failed import does.

Bare ``'A'`` deliberately raises rather than being read as Angstrom: it is ambiguous with
Ampere, and guessing is precisely what produced this defect. Reciprocal-space units such as
``'1/nm'``, which DigitalMicrograph writes for diffraction images, raise for the same reason.

Checked against the real fixture: `Glumi1_3VBSED_stack_00_slice_0476.dm4` reports MICRO SIGN
on both axes and converts to 5.020462442189455 nm/px, identical before and after, so files
that worked are unaffected.

Also fixed here: ``YDim`` was read and then discarded, so an anisotropic pixel was recorded as
square using the X scale. ``Scale`` carries both axes.
"""

from __future__ import annotations

import sys
import types

import pytest

from nornir_buildmanager.exceptions import NornirUserException

# The third-party `dm4` reader is an undeclared dependency and absent from the default venv.
if 'dm4' not in sys.modules:
    try:
        import dm4 as _real_dm4  # noqa: F401
    except ImportError:
        _stub = types.ModuleType('dm4')
        _stub_file = types.ModuleType('dm4.dm4file')
        _stub_file.DM4File = object  # type: ignore[attr-defined]
        _stub.dm4file = _stub_file  # type: ignore[attr-defined]
        sys.modules['dm4'] = _stub
        sys.modules['dm4.dm4file'] = _stub_file

from nornir_buildmanager.importers import dm4  # noqa: E402

MICRO_SIGN = '\u00b5'          # U+00B5, what the Neitz files write
GREEK_MU = '\u03bc'            # U+03BC, visually identical
ANGSTROM_LETTER = '\u00c5'     # U+00C5 LATIN CAPITAL LETTER A WITH RING ABOVE
ANGSTROM_SIGN = '\u212b'       # U+212B ANGSTROM SIGN


def scale(units, units_per_pixel=2.0):
    return dm4.DimensionScale(units_per_pixel, units)


class TestEveryUnitConvertsCorrectly:

    @pytest.mark.parametrize('units,nm_per_unit', [
        ('m', 1e9),
        ('mm', 1e6),
        (MICRO_SIGN + 'm', 1e3),
        (GREEK_MU + 'm', 1e3),
        ('um', 1e3),
        ('nm', 1.0),
        ('pm', 1e-3),
        (ANGSTROM_LETTER, 0.1),
        (ANGSTROM_SIGN, 0.1),
        ('Angstrom', 0.1),
    ])
    def test_the_factor_is_right(self, units, nm_per_unit):
        assert dm4._nm_per_pixel(scale(units)) == pytest.approx(2.0 * nm_per_unit)

    def test_nanometres_pass_through_unchanged(self):
        """'nm' used to be correct only because the fallback happened to be 1."""
        assert dm4._nm_per_pixel(scale('nm', 5.5)) == 5.5


class TestTheTwoMicroSignsAgree:
    """The defect that would have been invisible on inspection."""

    def test_both_codepoints_give_the_same_scale(self):
        micro = dm4._nm_per_pixel(scale(MICRO_SIGN + 'm'))
        greek = dm4._nm_per_pixel(scale(GREEK_MU + 'm'))
        assert micro == greek

    def test_they_really_are_different_strings(self):
        """Guards the premise; if these ever compare equal the test above proves nothing."""
        assert MICRO_SIGN != GREEK_MU
        assert MICRO_SIGN + 'm' != GREEK_MU + 'm'

    def test_the_greek_mu_is_not_treated_as_nanometres(self):
        """It fell through the old comparison and came out 1000x too small."""
        assert dm4._nm_per_pixel(scale(GREEK_MU + 'm', 1.0)) == pytest.approx(1000.0)

    def test_both_angstrom_codepoints_agree(self):
        assert (dm4._nm_per_pixel(scale(ANGSTROM_LETTER))
                == dm4._nm_per_pixel(scale(ANGSTROM_SIGN)))


class TestAnUnknownUnitStopsTheImport:
    """Previously it was assumed to be nanometres and the scale was silently wrong."""

    @pytest.mark.parametrize('units', ['furlong', '', 'deg', 'counts', 'e-'])
    def test_it_raises(self, units):
        with pytest.raises(NornirUserException):
            dm4._nm_per_pixel(scale(units))

    def test_reciprocal_space_units_raise(self):
        """DigitalMicrograph writes these for diffraction images; they are not a length."""
        for units in ('1/nm', '1/' + MICRO_SIGN + 'm', 'nm-1'):
            with pytest.raises(NornirUserException):
                dm4._nm_per_pixel(scale(units))

    def test_bare_a_raises_rather_than_guessing_angstrom(self):
        """Ambiguous with Ampere. Guessing is what produced this defect."""
        with pytest.raises(NornirUserException):
            dm4._nm_per_pixel(scale('A'))

    def test_the_message_names_the_unit_and_the_alternatives(self):
        with pytest.raises(NornirUserException) as caught:
            dm4._nm_per_pixel(scale('furlong'))
        message = str(caught.value)
        assert 'furlong' in message
        assert 'nm' in message
        assert 'nanometre' in message or 'nanometres' in message


class TestTolerantParsing:

    @pytest.mark.parametrize('units', ['nm', ' nm', 'nm ', '  nm  ', '\tnm\n'])
    def test_surrounding_whitespace_is_ignored(self, units):
        assert dm4._nm_per_pixel(scale(units)) == pytest.approx(2.0)

    @pytest.mark.parametrize('units', ['NM', 'Nm', 'MM', 'PM', 'ANGSTROM'])
    def test_case_is_ignored_where_it_is_unambiguous(self, units):
        assert dm4._nm_per_pixel(scale(units)) > 0

    def test_case_folding_does_not_confuse_metre_and_milli(self):
        """'m' and 'mm' differ by 1000; case folding must not collapse them."""
        assert (dm4._nm_per_pixel(scale('m'))
                == pytest.approx(1000 * dm4._nm_per_pixel(scale('mm'))))


class TestTheRealFixtureIsUnaffected:
    """Files that imported correctly must produce byte-identical scales."""

    def test_the_neitz_scale_is_unchanged(self):
        measured = dm4._nm_per_pixel(scale(MICRO_SIGN + 'm', 0.005020462442189455))
        assert measured == 5.020462442189455

    def test_the_old_conversion_agreed_on_this_one(self):
        """So the change is a strict extension for this file, not a correction."""
        old = 0.005020462442189455 * 1000.0
        assert dm4._nm_per_pixel(scale(MICRO_SIGN + 'm', 0.005020462442189455)) == old


def _old_scalar(units):
    """The conversion as it stood, for comparison. Only two spellings of micrometre."""
    scalar = 1
    if units == MICRO_SIGN + 'm':
        scalar = 1000.0
    elif units == 'um':
        scalar = 1000.0
    return scalar


class TestTheOldRuleReallyWasWrong:
    """Pins the size of each error, so a revert cannot pass quietly."""

    @pytest.mark.parametrize('units,error_factor', [
        ('m', 1e-9),
        ('mm', 1e-6),
        (GREEK_MU + 'm', 1e-3),
        ('pm', 1e3),
        (ANGSTROM_LETTER, 1e1),
    ])
    def test_the_old_scalar_was_off_by_this_much(self, units, error_factor):
        old = 2.0 * _old_scalar(units)
        new = dm4._nm_per_pixel(scale(units))
        assert old / new == pytest.approx(error_factor)

    @pytest.mark.parametrize('units', ['nm', MICRO_SIGN + 'm', 'um'])
    def test_the_units_that_worked_still_agree(self, units):
        assert dm4._nm_per_pixel(scale(units)) == pytest.approx(2.0 * _old_scalar(units))


class TestBothAxesAreUsed:
    """YDim was read and discarded, so an anisotropic pixel was recorded as square."""

    def test_the_axes_convert_independently(self):
        x = dm4._nm_per_pixel(scale('nm', 4.0))
        y = dm4._nm_per_pixel(scale(MICRO_SIGN + 'm', 4.0))
        assert x == 4.0
        assert y == pytest.approx(4000.0)
        assert x != y, 'an anisotropic pair must not collapse to one value'

    def test_differing_units_per_axis_are_each_honoured(self):
        assert dm4._nm_per_pixel(scale('mm', 1.0)) == pytest.approx(1e6)
        assert dm4._nm_per_pixel(scale('pm', 1.0)) == pytest.approx(1e-3)


class TestBothSetScaleBranchesWork:
    """The anisotropic branch is only reached by an anisotropic file, of which there is no
    fixture, so exercise it directly. My first version passed `Scale()` with no arguments and
    would have raised TypeError on the first such file -- a branch reachable only by data
    nobody has needs its own test, not a reading."""

    @staticmethod
    def channel(tmp_path):
        from nornir_buildmanager.volumemanager import (BlockNode, ChannelNode,
                                                       VolumeManager)

        volume = VolumeManager.Load(str(tmp_path / 'volume'), Create=True)
        [_a, block] = volume.UpdateOrAddChildByAttrib(BlockNode.Create('SEM'), 'Name')
        [_b, section] = block.GetOrCreateSection(9)
        [_c, channel] = section.UpdateOrAddChildByAttrib(ChannelNode.Create('SEM'), 'Name')
        return channel

    def test_the_isotropic_branch_records_one_value(self, tmp_path):
        channel = self.channel(tmp_path)
        channel.SetScale(dm4._nm_per_pixel(scale(MICRO_SIGN + 'm', 0.005020462442189455)))

        assert channel.Scale.X.UnitsPerPixel == pytest.approx(5.020462442189455)
        assert channel.Scale.Y.UnitsPerPixel == pytest.approx(5.020462442189455)

    def test_the_anisotropic_branch_records_both_axes(self, tmp_path):
        from nornir_buildmanager.volumemanager import Scale

        channel = self.channel(tmp_path)
        x_nm = dm4._nm_per_pixel(scale('nm', 4.0))
        y_nm = dm4._nm_per_pixel(scale('nm', 8.0))
        channel.SetScale(Scale(x_nm, y_nm))

        assert channel.Scale.X.UnitsPerPixel == pytest.approx(4.0)
        assert channel.Scale.Y.UnitsPerPixel == pytest.approx(8.0)

    def test_scale_needs_x_positionally(self):
        """Why the branch above is written the way it is."""
        from nornir_buildmanager.volumemanager import Scale

        with pytest.raises(TypeError):
            Scale()
