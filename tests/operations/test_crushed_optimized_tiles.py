"""AssembleTiles keeps crushed codes: optimized tiles are codes of the section quanta with gain 1.

The optimized (Viking) tileset loaded each source tile divided by its own maximum and saved it x255, which
stretched crushed codes like Assemble once did (RC1_Crush, 2026-11). Crushed tiles are now loaded on the section's
code unit with their gains applied, written as codes with the ``nornir_crush`` chunk, and the tileset records the
quanta and the gains it was built with.
"""
import os
import tempfile
from unittest import mock

import numpy as np
from nornir_buildmanager.operations import tile as tile_ops
from nornir_imageregistration import noise_crush
from nornir_imageregistration.noise_crush import CrushParams
from PIL import Image

_PARAMS = CrushParams(k=2.5, step=244, sigma=97.48, offset=0, bits=7, source_dtype='uint16')


def _node(attrib: dict[str, str]) -> mock.Mock:
    return mock.Mock(attrib=dict(attrib))


def test_crushed_tile_is_saved_as_codes_with_the_chunk():
    codes = np.arange(128, dtype=np.float64).reshape(8, 16)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'tile.png')

        tile_ops._encode_tile_to_temp(path, codes / _PARAMS.max_code, bpp=8, crush_params=_PARAMS)

        with Image.open(path) as im:
            np.testing.assert_array_equal(np.asarray(im), codes)
        params = noise_crush.read_crush_params(path)
    assert params == _PARAMS


def test_uncrushed_tile_is_saved_as_before():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'tile.png')

        tile_ops._encode_tile_to_temp(path, np.full((8, 8), 0.5), bpp=8)

        with Image.open(path) as im:
            assert np.asarray(im).max() == 127
        assert noise_crush.read_crush_params(path) is None


def test_uncrushed_filter_never_makes_a_tileset_stale():
    assert tile_ops._crushed_tileset_stale_reason(_node({}), _node({}), None) is None


def test_tileset_built_before_codes_were_kept_is_stale():
    filter_node = _node(_PARAMS.to_attributes())

    assert tile_ops._crushed_tileset_stale_reason(_node({}), filter_node, _PARAMS) is not None


def test_recorded_tileset_is_current_until_the_gains_change():
    filter_node = _node({**_PARAMS.to_attributes(), 'TileGainsChecksum': 'a'})
    tile_set_node = _node({})

    tile_ops._record_crushed_tileset(tile_set_node, filter_node, _PARAMS)
    assert tile_ops._crushed_tileset_stale_reason(tile_set_node, filter_node, _PARAMS) is None

    filter_node.attrib['TileGainsChecksum'] = 'b'
    assert 'gains' in tile_ops._crushed_tileset_stale_reason(tile_set_node, filter_node, _PARAMS)


def test_recording_without_gains_clears_old_gains():
    tile_set_node = _node({'TileGainsChecksum': 'old'})

    tile_ops._record_crushed_tileset(tile_set_node, _node(_PARAMS.to_attributes()), _PARAMS)

    assert 'TileGainsChecksum' not in tile_set_node.attrib
    assert CrushParams.from_attributes(tile_set_node.attrib) is not None
