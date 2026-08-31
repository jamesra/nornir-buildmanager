"""Reading a DM4 tile must not hold the tile twice (review #157).

Measured on a real 154.5 MiB DM4 image (`Glumi1_3VBSED_stack_00_slice_0476.dm4`, 9000x9000 at
16 bpp) with tracemalloc, peak allocation as a multiple of the tile's own bytes:

                        before   after
    ReadImageAsNumpy     2.06x    1.00x
    ReadImageAsPIL       2.06x    1.00x

Checksums byte-identical throughout (1478380489157 on both paths, before and after).

The order of the two fixes matters, and is the interesting part of this issue. #157 attributed
the excess to the caller -- "a second full copy via `.tobytes()`". Measured, that was not where
it came from: `dm4.read_tag_data_array` built its result with `array.fromfile`, which grows the
buffer as it reads and so peaked at 2.06x on its own, *before any caller copied anything*.
Every caller variant measured the same 2.06x, including `np.frombuffer` and `PIL.frombuffer` --
the read's transient peak dominated, so the obvious zero-copy rewrites saved nothing, and
even raised the resident figure to 1.06x by keeping the array.array alive as buffer owner.

Only after the read was fixed to allocate exactly once did the caller's copy *become* the peak,
at 2.00x. So both layers had to change, and either one alone would have looked ineffective.
General shape: when two allocations overlap, the larger transient hides the smaller, and
fixing the hidden one first measures as no improvement at all.

These tests use in-memory fakes, so they are fast and need no DM4 fixture.
"""

from __future__ import annotations

import array
import sys
import tracemalloc
import types

import numpy as np
import pytest

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


class _Dm4File:
    def __init__(self, data: array.array):
        self._data = data
        self.reads = 0

    def read_tag_data(self, _tag):
        self.reads += 1
        return self._data


class _Handler:
    """Just enough of DM4FileHandler to drive the two read methods."""

    ReadImageAsNumpy = dm4.DM4FileHandler.ReadImageAsNumpy
    ReadImageAsPIL = dm4.DM4FileHandler.ReadImageAsPIL

    def __init__(self, shape, typecode='H', bpp=16, values=None):
        count = shape[0] * shape[1]
        if values is None:
            values = [i % 60000 for i in range(count)]
        self._data = array.array(typecode, values)
        self._shape = shape
        self.image_bpp = bpp
        self.ImageDataTag = object()
        self.dm4file = _Dm4File(self._data)

    @property
    def image_dtype(self):
        return {8: np.uint8, 16: np.uint16, 32: np.uint32}[self.image_bpp]

    def ReadImageShape(self):
        return np.asarray(self._shape, dtype=np.int64)


SHAPE = (64, 64)


class TestTheNumpyReadIsAView:

    def test_the_result_shares_memory_with_the_tag_data(self):
        handler = _Handler(SHAPE)
        result = handler.ReadImageAsNumpy()
        self.assert_shares(result, handler._data)

    @staticmethod
    def assert_shares(result, source):
        base = result
        while getattr(base, 'base', None) is not None:
            base = base.base
        assert memoryview(base).obj is memoryview(source).obj or base is source, (
            'ReadImageAsNumpy copied the tile; the whole point is that it no longer does')

    def test_the_values_and_shape_are_right(self):
        handler = _Handler(SHAPE)
        result = handler.ReadImageAsNumpy()
        assert result.shape == SHAPE
        assert result.dtype == np.uint16
        assert np.array_equal(
            result, np.frombuffer(handler._data, dtype=np.uint16).reshape(SHAPE))

    def test_it_reads_the_tag_only_once(self):
        handler = _Handler(SHAPE)
        handler.ReadImageAsNumpy()
        assert handler.dm4file.reads == 1


class TestItCopiesWhenTheWidthsDisagree:
    """image_dtype comes from PixelDepth, the typecode from DataType; they can differ."""

    def test_a_narrower_array_than_the_reported_depth_is_copied_not_reinterpreted(self):
        # 8-bit stored data reported as 16 bpp: a view would halve the pixel count and
        # reshape would fail, or worse, succeed with garbage.
        handler = _Handler(SHAPE, typecode='B', bpp=16,
                           values=[i % 251 for i in range(SHAPE[0] * SHAPE[1])])
        result = handler.ReadImageAsNumpy()
        assert result.shape == SHAPE
        assert result.dtype == np.uint16
        assert np.array_equal(result,
                              np.array(handler._data, dtype=np.uint16).reshape(SHAPE))

    def test_a_wider_array_than_the_reported_depth_is_copied(self):
        handler = _Handler(SHAPE, typecode='I', bpp=16,
                           values=[i % 60000 for i in range(SHAPE[0] * SHAPE[1])])
        result = handler.ReadImageAsNumpy()
        assert result.shape == SHAPE
        assert np.array_equal(result,
                              np.array(handler._data, dtype=np.uint16).reshape(SHAPE))

    def test_the_matching_case_really_is_the_common_one(self):
        """Guards the premise: the fast path must be the one production takes."""
        handler = _Handler(SHAPE, typecode='H', bpp=16)
        assert handler._data.itemsize == np.dtype(handler.image_dtype).itemsize


class TestThePilReadDoesNotCopyToBytes:

    def test_the_pixels_are_correct(self):
        handler = _Handler(SHAPE)
        im = handler.ReadImageAsPIL()
        assert im.mode == 'I;16'
        assert im.size == (SHAPE[1], SHAPE[0])
        assert np.array_equal(
            np.asarray(im), np.frombuffer(handler._data, dtype=np.uint16).reshape(SHAPE))

    def test_the_image_outlives_the_frame_that_read_it(self):
        """frombuffer shares the buffer, so Pillow must hold a reference to it."""
        def read():
            return _Handler(SHAPE).ReadImageAsPIL()

        im = read()
        expected = np.frombuffer(
            array.array('H', [i % 60000 for i in range(SHAPE[0] * SHAPE[1])]),
            dtype=np.uint16).reshape(SHAPE)
        assert np.array_equal(np.asarray(im), expected)

    def test_an_unsupported_depth_still_raises_before_touching_the_buffer(self):
        from nornir_buildmanager.exceptions import NornirUserException

        handler = _Handler(SHAPE, bpp=32)
        with pytest.raises(NornirUserException):
            handler.ReadImageAsPIL()
        assert handler.dm4file.reads == 0, 'the guard should short-circuit before the read'


class TestPeakAllocation:
    """The measurable property, at a size where a doubling is unmistakable."""

    BIG = (1024, 1024)  # 2 MiB at uint16

    def peak_of(self, fn):
        tracemalloc.start()
        tracemalloc.reset_peak()
        result = fn()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return result, peak

    def test_the_numpy_read_does_not_allocate_another_tile(self):
        handler = _Handler(self.BIG)
        tile_bytes = self.BIG[0] * self.BIG[1] * 2
        _, peak = self.peak_of(handler.ReadImageAsNumpy)
        assert peak < tile_bytes * 0.5, (
            f'peak {peak} approaches the {tile_bytes}-byte tile, so a copy is being made')

    def test_the_pil_read_does_not_allocate_another_tile(self):
        handler = _Handler(self.BIG)
        tile_bytes = self.BIG[0] * self.BIG[1] * 2
        _, peak = self.peak_of(handler.ReadImageAsPIL)
        assert peak < tile_bytes * 0.5, (
            f'peak {peak} approaches the {tile_bytes}-byte tile, so a copy is being made')

    def test_the_old_patterns_really_did_allocate_another_tile(self):
        """Guards the premise; if these stop copying, the change above is redundant."""
        handler = _Handler(self.BIG)
        tile_bytes = self.BIG[0] * self.BIG[1] * 2

        _, numpy_peak = self.peak_of(
            lambda: np.reshape(np.array(handler._data, dtype=np.uint16), self.BIG))
        _, bytes_peak = self.peak_of(handler._data.tobytes)

        assert numpy_peak > tile_bytes * 0.9, (
            'np.array over the tag data should cost a full extra tile')
        assert bytes_peak > tile_bytes * 0.9, (
            '.tobytes() should cost a full extra tile')
