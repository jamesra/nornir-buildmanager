"""
DM4 import must not silently ignore FlipList and ContrastMap.

``Import`` reads FlipList.txt and the histogram cutoffs file and forwards both to
``DigitalMicrograph4Import.ToMosaic``, whose signature and docstring both advertise
them. The body never read either. A DM4 section listed in FlipList.txt imported
unflipped, and a contrast override was dropped -- in both cases producing output
that looks fine and is not. The ``ImportDM4`` pipeline in Pipelines.xml makes this
reachable, so it is a live defect rather than a latent one.

Wiring the arguments through is not the fix. idoc flips by flopping the image *and*
writing the mosaic with the opposite coordinate convention
(``MosaicFile.Write(..., Flip=not Flip)``). DM4 has no equivalent: it composes
RigidTranslation positions from the montage grid and calls SaveToMosaicFile, so the
paired convention would have to be invented, and choosing it wrong mis-registers
every section -- strictly worse than importing unflipped. Contrast overrides have
nowhere to go at all, since ConvertDM4ToPng writes the tile without levelling.

So an affected section now stops the build with an actionable message, and sections
that ask for neither option are untouched.
"""

from __future__ import annotations

import sys
import types

import pytest

from nornir_buildmanager.exceptions import NornirUserException

# The third-party `dm4` reader is an undeclared dependency and is not installed in
# the default venv, so importing the importer module fails outright. The guard under
# test never touches it, so stub it rather than skipping and verifying nothing.
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


def _check(section_number, flip_list=None, contrast_map=None):
    return dm4._raise_if_unsupported_section_options(
        section_number, flip_list, contrast_map, dm4FileFullPath='0042/tile_001.dm4')


# --- sections that ask for nothing unsupported --------------------------------

@pytest.mark.parametrize('flip_list, contrast_map', [
    (None, None),
    ([], {}),
    ([7, 8], {9: (0.1, 0.9, 1.0)}),      # lists exist but do not mention this section
])
def test_unaffected_sections_import_normally(flip_list, contrast_map):
    assert _check(42, flip_list, contrast_map) is None


# --- sections that do ---------------------------------------------------------

def test_a_section_in_the_flip_list_aborts():
    with pytest.raises(NornirUserException) as caught:
        _check(42, flip_list=[41, 42, 43])

    assert 'FlipList.txt' in str(caught.value)
    assert 'Section 42' in str(caught.value)


def test_a_section_with_a_contrast_override_aborts():
    with pytest.raises(NornirUserException) as caught:
        _check(42, contrast_map={42: (0.1, 0.9, 1.0)})

    assert 'contrast override' in str(caught.value)


def test_both_reasons_are_reported_together():
    with pytest.raises(NornirUserException) as caught:
        _check(42, flip_list=[42], contrast_map={42: (0.1, 0.9, 1.0)})

    text = str(caught.value)
    assert 'FlipList.txt' in text
    assert 'contrast override' in text


def test_the_message_says_what_to_do_about_it():
    with pytest.raises(NornirUserException) as caught:
        _check(42, flip_list=[42])

    text = str(caught.value)
    assert '0042/tile_001.dm4' in text
    assert 'idoc' in text, 'the message should point at the importer that implements this'


# --- the contract the finding is really about ---------------------------------

def test_tomosaic_still_accepts_both_arguments():
    """Import forwards them, so the signature must keep taking them."""
    import inspect

    params = inspect.signature(dm4.DigitalMicrograph4Import.ToMosaic).parameters

    assert 'FlipList' in params
    assert 'ContrastMap' in params


def test_the_docstring_no_longer_claims_they_are_applied():
    doc = dm4.DigitalMicrograph4Import.ToMosaic.__doc__ or ''

    assert 'Not implemented' in doc, 'the docstring advertised support that did not exist'
