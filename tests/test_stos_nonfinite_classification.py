"""
Only genuine NaN/Inf reports may be converted into the hard-abort user error.

``_reraise_stos_nonfinite`` classified failures with ``'nan' in text or 'inf' in
text`` over the lowercased message. Any unrelated failure whose message merely
contained those letters was reported to the user as a corrupt STOS transform and
hard-aborted, telling them to open a file in Pyre and fix a registration that was
never wrong.

The finding's own examples were partly off: "insufficient" does not contain
``inf`` (i-n-s). The realistic false positives measured were "info",
"information", any path with an ``inf`` component, and -- the dangerous one in a
pipeline whose scales are all in nanometres -- **"nanometer"**, which contains
``nan``.

Classification is now a token match rather than a substring test. The lookarounds
exclude word characters, dots, dashes and both slash kinds, so ``nan`` in
"nanometer" and ``inf`` in ``C:\\data\\inf\\0001.stos`` no longer match, while
standalone ``nan``, ``inf``, ``-inf`` and ``infinity`` still do.
"""

from __future__ import annotations

import pytest

from nornir_buildmanager import operations
from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.block import _reraise_stos_nonfinite


def _message_reports_nonfinite(text: str) -> bool:
    """Reached through the module so this file still imports against older code,
    where the behaviour tests below can then demonstrate the misclassification."""
    return operations.block._message_reports_nonfinite(text)

# Messages that really are reporting a non-finite value.
GENUINE = [
    'Transform contains nan values',
    'NaN detected in control points',
    'value is inf',
    'array([nan, 1.0])',
    'result was -inf',
    'got +inf while composing',
    'Infinity encountered while composing',
    'nan',
    'inf',
]

# Unrelated failures that must not be reported as corrupt transforms.
UNRELATED = [
    'No information available for section 42',
    'info: could not open file',
    'Scale is 2.18 nanometers per pixel',
    'nanometer scale mismatch between sections',
    r'FileNotFoundError: C:\data\inf\0001.stos',
    r'Cannot open D:\volumes\Info\grid.stos',
    'Cannot open /mnt/data/inf/grid.stos',
    'insufficient overlap between tiles',
    'Transform file is empty',
    'could not convert string to float',
    'infrastructure error',
    'confirm the input',
]


@pytest.mark.parametrize('message', GENUINE)
def test_genuine_nonfinite_is_detected(message):
    assert _message_reports_nonfinite(message) is True


@pytest.mark.parametrize('message', UNRELATED)
def test_unrelated_message_is_not_detected(message):
    assert _message_reports_nonfinite(message) is False


@pytest.mark.parametrize('message', ['Transform contains NAN values',
                                     'value is INF',
                                     'NaN'])
def test_detection_is_case_insensitive(message):
    assert _message_reports_nonfinite(message) is True


# --- the behaviour callers actually see ---------------------------------------

@pytest.mark.parametrize('message', GENUINE)
def test_genuine_failure_is_reraised_as_a_user_error(message):
    with pytest.raises(NornirUserException) as caught:
        _reraise_stos_nonfinite(ValueError(message),
                                introduced_in='out.stos',
                                files=['a.stos', 'b.stos'])

    text = str(caught.value)
    assert 'NaN/Inf values were introduced' in text
    assert 'a.stos' in text and 'b.stos' in text


@pytest.mark.parametrize('message', UNRELATED)
def test_unrelated_failure_is_left_for_the_caller(message):
    """Returning lets the original error propagate with its real cause."""
    assert _reraise_stos_nonfinite(ValueError(message),
                                   introduced_in='out.stos',
                                   files=['a.stos']) is None


def test_original_error_is_chained_as_the_cause():
    original = ValueError('Transform contains nan values')

    with pytest.raises(NornirUserException) as caught:
        _reraise_stos_nonfinite(original, introduced_in='out.stos', files=['a.stos'])

    assert caught.value.__cause__ is original


def test_a_nanometre_scale_failure_is_not_blamed_on_the_transform():
    """The regression that matters most here: this pipeline measures in nanometres."""
    err = ValueError('Scale is 2.18 nanometers per pixel but section expects 4.36')

    assert _reraise_stos_nonfinite(err, introduced_in='out.stos', files=['a.stos']) is None
