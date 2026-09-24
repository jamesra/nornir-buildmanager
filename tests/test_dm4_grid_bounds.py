"""
The DM4 montage grid bounds check compared the wrong index against the wrong extent.

``ReadMontageGridSize()`` returns ``(YDim, XDim)`` and the position is built as
``(tile_number // XDim, tile_number % XDim)``, i.e. ``(Y, X)``. The guard was::

    assert (grid_position[1] < YDim), 'Grid position is off the grid'

``grid_position[1]`` is the X index, and it is compared against the *Y* extent. Since
X is ``tile_number % XDim`` it is below XDim by construction, so the only index that
can actually leave the grid is the row -- which was never checked.

The result is backwards in both directions. On a 2x5 montage, six of the ten valid
tiles fail the assert, so any montage wider than it is tall dies partway through
import. Meanwhile a tile numbered past the end of the grid passes on every montage
shape tested.

Each index is now checked against its own extent, and it raises rather than asserts
so the check survives ``python -O``.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from nornir_buildmanager.exceptions import NornirUserException

# The third-party `dm4` reader is an undeclared dependency and is absent from the
# default venv. The grid arithmetic under test never touches it.
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


class _Dm4Data:
    def __init__(self, YDim, XDim, tile_px=1024, overlap=0.1):
        self._grid = np.asarray((YDim, XDim), dtype=np.uint64)
        self._shape = np.asarray((tile_px, tile_px), dtype=np.int64)
        self._overlap = np.asarray((overlap, overlap), dtype=np.float32)

    def ReadMontageGridSize(self):
        return self._grid

    def ReadImageShape(self):
        return self._shape

    def ReadMontageOverlap(self):
        return self._overlap


class _Transform:
    def __init__(self, name):
        self.FullPath = f'/nonexistent/{name}.mosaic'


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """AddTileToMosaic memoises mosaics by path in module-level dicts."""
    dm4.mosaics_loaded.clear()
    dm4.transforms_changed.clear()
    yield
    dm4.mosaics_loaded.clear()
    dm4.transforms_changed.clear()


def _add(tile_number, YDim, XDim, name='t'):
    return dm4.DigitalMicrograph4Import.AddTileToMosaic(
        _Transform(f'{name}_{YDim}x{XDim}'), _Dm4Data(YDim, XDim), tile_number)


# --- valid tiles must all be accepted -----------------------------------------

@pytest.mark.parametrize('YDim, XDim', [(2, 5), (5, 2), (3, 3), (1, 8), (8, 1)])
def test_every_tile_in_the_grid_is_accepted(YDim, XDim):
    for tile_number in range(YDim * XDim):
        assert _add(tile_number, YDim, XDim, name=f'ok{tile_number}') is True


def test_a_wide_montage_no_longer_rejects_its_own_tiles():
    """The regression: on a 2x5 montage the old check failed 6 of the 10 valid tiles."""
    accepted = [t for t in range(10) if _add(t, 2, 5, name=f'wide{t}')]

    assert len(accepted) == 10


# --- tiles past the end of the grid must be rejected --------------------------

@pytest.mark.parametrize('YDim, XDim', [(2, 5), (5, 2), (3, 3)])
def test_a_tile_past_the_end_of_the_grid_is_rejected(YDim, XDim):
    off_grid = YDim * XDim

    with pytest.raises(NornirUserException):
        _add(off_grid, YDim, XDim)


def test_the_error_names_the_position_and_the_grid():
    with pytest.raises(NornirUserException) as caught:
        _add(10, 2, 5)

    text = str(caught.value)
    assert 'Tile 10' in text
    assert 'Y=2' in text and 'X=0' in text
    assert '2 x 5' in text


def test_the_last_tile_in_the_grid_is_still_inside_it():
    """Off-by-one guard: index YDim*XDim - 1 is the final valid tile."""
    assert _add(9, 2, 5) is True

    with pytest.raises(NornirUserException):
        _add(10, 2, 5, name='past')


# --- the check must survive -O ------------------------------------------------

def test_the_bounds_check_is_not_an_assert():
    import inspect

    source = inspect.getsource(dm4.DigitalMicrograph4Import.AddTileToMosaic)
    code = '\n'.join(line for line in source.splitlines()
                     if not line.lstrip().startswith('#'))

    assert 'assert' not in code, 'asserts are stripped under python -O'
    assert 'raise NornirUserException' in code
