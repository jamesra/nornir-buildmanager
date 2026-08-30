"""
DM4 tiles were converted to PIL mode I before being written as PNG.

``ReadImageAsPIL`` built the tile as ``I;16`` and then did ``im.convert(mode='I')``,
and ``ConvertDM4ToPng`` saved the result under ``TileExtension = 'png'``.

The finding expected Pillow to refuse that. It does not -- mode I saves as PNG and
reopens as ``I;16`` uint16 with the full value range intact, so no tile has been
silently corrupted. What Pillow does emit is::

    DeprecationWarning: Saving I mode images as PNG is deprecated and will be
    removed in Pillow 13 (2026-10-15)

So this is a dated time bomb rather than existing data loss. Saving the ``I;16``
image directly produces the same uint16 PNG with no deprecation, which is what the
importer now does.

The comment on TileExtension was wrong twice over: it claimed Pillow cannot write
16-bit PNG, which it can, and that "we use the npy extension", which we do not.

Also pinned here: only 16 bpp works at all. PIL has no ``I;8`` or ``I;32`` raw mode,
so other depths died in frombytes with "unrecognized image mode"; they now say so.
"""

from __future__ import annotations

import sys
import types
import warnings

import numpy as np
import PIL.Image
import pytest

from nornir_buildmanager.exceptions import NornirUserException

# The third-party `dm4` reader is an undeclared dependency and is absent from the
# default venv. Nothing under test here touches it.
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

W = H = 16


def _raw16():
    return (np.linspace(0, 60000, W * H).astype(np.uint16)).reshape(H, W)


class _TagData:
    def __init__(self, array):
        self._array = array

    def tobytes(self):
        return self._array.tobytes()


class _Dm4File:
    def __init__(self, array):
        self._array = array

    def read_tag_data(self, _tag):
        return _TagData(self._array)


class _Handler:
    """Just enough of DM4FileHandler to drive ReadImageAsPIL."""

    ReadImageAsPIL = dm4.DM4FileHandler.ReadImageAsPIL

    def __init__(self, array, bpp=16):
        self._array = array
        self.image_bpp = bpp
        self.ImageDataTag = object()
        self.dm4file = _Dm4File(array)

    def ReadImageShape(self):
        return np.asarray(self._array.shape, dtype=np.int64)


# --- the tile must survive the round trip -------------------------------------

def test_the_tile_round_trips_through_png_without_loss(tmp_path):
    raw = _raw16()
    im = _Handler(raw).ReadImageAsPIL()

    path = tmp_path / f'tile.{dm4.TileExtension}'
    im.save(path)

    assert np.array_equal(np.asarray(PIL.Image.open(path)), raw)


def test_the_saved_tile_is_16_bit(tmp_path):
    im = _Handler(_raw16()).ReadImageAsPIL()

    path = tmp_path / 'tile.png'
    im.save(path)

    reopened = PIL.Image.open(path)
    assert reopened.mode == 'I;16'
    assert np.asarray(reopened).dtype == np.uint16


# --- the actual defect: the deprecation -------------------------------------

def test_saving_a_tile_emits_no_deprecation_warning(tmp_path):
    """Saving mode I as PNG is removed in Pillow 13 (2026-10-15)."""
    im = _Handler(_raw16()).ReadImageAsPIL()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        im.save(tmp_path / 'tile.png')

    deprecations = [str(w.message) for w in caught
                    if issubclass(w.category, DeprecationWarning)]
    assert deprecations == []


def test_the_tile_is_not_widened_to_32_bit():
    im = _Handler(_raw16()).ReadImageAsPIL()

    assert im.mode == 'I;16', 'mode I is 32-bit and is what triggers the deprecation'


# --- only 16 bpp ever worked --------------------------------------------------

@pytest.mark.parametrize('bpp', [8, 32])
def test_an_unsupported_bit_depth_says_so(bpp):
    with pytest.raises(NornirUserException) as caught:
        _Handler(_raw16(), bpp=bpp).ReadImageAsPIL()

    assert str(bpp) in str(caught.value)


def test_pil_really_has_no_raw_mode_for_those_depths():
    """Why the guard exists rather than attempting the read."""
    for bpp in (8, 32):
        with pytest.raises(ValueError, match='unrecognized image mode'):
            PIL.Image.frombytes(data=_raw16().tobytes(), mode='I;%d' % bpp, size=(W, H))


# --- the comment that pointed the wrong way -----------------------------------

def test_the_tile_extension_comment_is_not_stale():
    import inspect
    import re

    source = inspect.getsource(dm4)
    line = next(ln for ln in source.splitlines() if re.match(r'^TileExtension\s*=', ln))

    assert 'npy' not in line, 'the comment claimed we use the npy extension; we use png'
    assert dm4.TileExtension == 'png'
