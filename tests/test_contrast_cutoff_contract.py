"""
ContrastCutoffs are 0-1 fractions, and passing percentages must not be silent.

Levelling computes ``AutoLevel(cutoffs[0], 1.0 - cutoffs[1])``. The mdoc importer
passed ``(0.0, 100.0)`` positionally into ``SerialEMIDocImport.ToMosaic``, so it
asked for ``AutoLevel(0.0, -99.0)``.

That does not merely widen the range. On a representative tile histogram it returns
``min 3.0, max 2.0`` -- a max *below* its min -- rather than the correct
``min 3.0, max 253.0``. Levelling against an inverted range destroys the tile.

The ``Import`` entry point validated 0-1, but ``ToMosaic`` did not, which is exactly
how the mdoc call slipped past. Validation now lives on ``ToMosaic`` too, shared with
``Import`` so the two cannot drift.
"""

from __future__ import annotations

from typing import cast

import numpy as np
import pytest
from nornir_shared import histogram as hist_mod

from nornir_buildmanager.volumemanager import VolumeNode

from nornir_buildmanager.importers import idoc


def _validate_contrast_cutoffs(cutoffs):
    """Reached through the module so this file still imports against older code,
    where the ToMosaic and mdoc tests below can then show the gap."""
    return idoc._validate_contrast_cutoffs(cutoffs)


def _tile_histogram():
    """A representative EM tile: bulk near mid-grey, with dark and bright outliers."""
    rng = np.random.default_rng(7)
    samples = np.clip(rng.normal(140, 25, 200000), 0, 255).astype(int)
    samples = np.concatenate([samples, np.full(200, 3), np.full(200, 252)])
    counts, _ = np.histogram(samples, bins=256, range=(0, 256))
    return hist_mod.Histogram.Init(minVal=0.0, maxVal=255.0,
                                   binVals=[int(c) for c in counts])


def _level(cutoffs):
    """Mirror how shared.py applies the cutoffs."""
    return _tile_histogram().AutoLevel(cutoffs[0], 1.0 - cutoffs[1])


# --- what the bad value actually did ------------------------------------------

def test_percentage_cutoffs_invert_the_levelled_range():
    """The damage: max lands below min, so the tile is destroyed rather than levelled."""
    low, high = _level((0.0, 100.0))

    assert high < low, 'expected the inverted range the percentage cutoffs produce'


def test_fraction_cutoffs_give_the_full_data_range():
    low, high = _level((0.0, 1.0))

    assert low < high
    assert (low, high) == (3.0, 253.0)


def test_trimming_fractions_pull_both_ends_in():
    full_low, full_high = _level((0.0, 1.0))
    trimmed_low, trimmed_high = _level((0.001, 0.999))

    assert trimmed_low > full_low
    assert trimmed_high < full_high


# --- the guard ----------------------------------------------------------------

@pytest.mark.parametrize('cutoffs', [(0.0, 1.0), (0.1, 0.9), (0.0, 0.5), (0.5, 1.0)])
def test_valid_fractions_are_accepted(cutoffs):
    assert _validate_contrast_cutoffs(cutoffs) is None


@pytest.mark.parametrize('cutoffs', [(0.0, 100.0), (0.0, 1.5), (-0.1, 0.9), (2.0, 3.0)])
def test_values_outside_zero_to_one_are_rejected(cutoffs):
    with pytest.raises(ValueError):
        _validate_contrast_cutoffs(cutoffs)


def test_the_exact_mdoc_value_is_rejected():
    with pytest.raises(ValueError, match='between 0 and 1'):
        _validate_contrast_cutoffs((0.0, 100.0))


@pytest.mark.parametrize('cutoffs', [(0.9, 0.1), (0.5, 0.5)])
def test_min_must_be_below_max(cutoffs):
    with pytest.raises(ValueError, match='greater than'):
        _validate_contrast_cutoffs(cutoffs)


def test_tomosaic_validates_its_own_cutoffs():
    """Import validated, ToMosaic did not -- which is how mdoc bypassed the check."""
    with pytest.raises(ValueError, match='between 0 and 1'):
        # Fails before touching the volume or the filesystem, so a stand-in is fine.
        list(idoc.SerialEMIDocImport.ToMosaic(
            cast(VolumeNode, object()), 'missing.idoc', (0.0, 100.0)))


def test_mdoc_no_longer_passes_percentages():
    """The call is keyword-based now, so a future signature change cannot re-misbind."""
    import inspect

    from nornir_buildmanager.importers import mdoc

    source = inspect.getsource(mdoc.SerialEMMDocImport.ToMosaic)
    # The comment above the call names the old value, so read code lines only.
    code = '\n'.join(line for line in source.splitlines()
                     if not line.lstrip().startswith('#'))

    assert '(0.0, 100.0)' not in code
    assert 'ContrastCutoffs=(0.0, 1.0)' in code
