"""StosBrute's warped-image scale factor is Mapped/Control units per pixel.

``_CalculateFilterToFilterBruteRegistrationScaleFactor`` returned Control/Mapped. Once
``_IsotropicWarpedImageScaleFactor`` started feeding it to ``stos_brute`` as
``WarpedImageScaleFactors`` (4582206, 2026-07-11), every RC3 pair that mixes the 2.143 and
2.225 nm/px captures was pre-scaled about 7.7% the wrong way, and LogPolar returned wrong
poses for ~1/3 of RC3 pairs (srv, 2026-11-01; 13 of 14 recovered with the corrected ratio,
controls unchanged). ``stos_brute``'s own tests use warped over fixed resolution.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from nornir_buildmanager.operations import block


def _filter(units_per_pixel_x: float, units_per_pixel_y: float | None = None) -> SimpleNamespace:
    y = units_per_pixel_x if units_per_pixel_y is None else units_per_pixel_y
    return SimpleNamespace(Scale=SimpleNamespace(X=units_per_pixel_x, Y=y))


def test_factor_is_mapped_over_control() -> None:
    control = _filter(2.143)
    mapped = _filter(2.225)
    x_scale, y_scale = block._CalculateFilterToFilterBruteRegistrationScaleFactor(control, mapped)
    # Mapped pixels are larger, so the mapped image must be enlarged to match the control.
    assert x_scale == pytest.approx(2.225 / 2.143)
    assert y_scale == pytest.approx(2.225 / 2.143)
    assert x_scale > 1


def test_isotropic_factor_follows_the_same_direction() -> None:
    control = _filter(2.225, 2.225)
    mapped = _filter(2.143, 2.0)
    factor = block._IsotropicWarpedImageScaleFactor(control, mapped)
    assert factor == pytest.approx(((2.143 / 2.225) * (2.0 / 2.225)) ** 0.5)
    assert factor < 1


def test_missing_scale_gives_none() -> None:
    assert block._IsotropicWarpedImageScaleFactor(SimpleNamespace(Scale=None), _filter(2.0)) is None
    assert block._IsotropicWarpedImageScaleFactor(_filter(2.0), SimpleNamespace(Scale=None)) is None


def test_equal_resolution_is_identity() -> None:
    assert block._IsotropicWarpedImageScaleFactor(_filter(2.143), _filter(2.143)) == pytest.approx(1.0)
