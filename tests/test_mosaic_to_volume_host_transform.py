"""MosaicToVolume hands pool workers a host (NumPy) transform, never a CuPy one.

Under CuPy, ``factory.LoadTransform`` builds ``*_GPUComponent`` transforms. Every
tile task pickles the slice-to-volume transform into a pool worker, and unpickling
CuPy arrays opens a CUDA context there. On srv (RC3 section 0001, 2026-11-02) ~130
workers did that and 114 of 900 tiles failed with CUDA_ERROR_OUT_OF_MEMORY while
only 3.7 of 24.5 GB was in use.
"""
from __future__ import annotations

import pickle
import unittest

import nornir_imageregistration
import numpy as np
from nornir_imageregistration import assemble
from nornir_imageregistration.transforms import factory


def _grid_transform_text() -> str:
    grid = nornir_imageregistration.ITKGridDivision((400, 400), cell_size=(256, 256), grid_dims=(3, 3))
    grid.TargetPoints = grid.SourcePoints + np.array((7.0, -3.0))
    return nornir_imageregistration.transforms.GridWithRBFFallback(grid).ToITKString()


@unittest.skipUnless(nornir_imageregistration.HasCupy(), 'needs CuPy')
class TestHostTransformForPool(unittest.TestCase):

    def setUp(self) -> None:
        self._lib = nornir_imageregistration.GetActiveComputationLib()
        nornir_imageregistration.SetActiveComputationLib(nornir_imageregistration.ComputationLib.cupy)

    def tearDown(self) -> None:
        nornir_imageregistration.SetActiveComputationLib(self._lib)

    def test_loaded_grid_is_a_gpu_component_under_cupy(self):
        """Precondition for the fix: this is what the stage used to pickle."""
        loaded = factory.LoadTransform(_grid_transform_text())

        self.assertTrue(type(loaded).__name__.endswith('_GPUComponent'))

    def test_host_copy_pickles_without_cupy_and_maps_points_the_same(self):
        loaded = factory.LoadTransform(_grid_transform_text())
        host = assemble.transform_for_host_assembly(loaded)

        self.assertFalse(type(host).__name__.endswith('_GPUComponent'))
        self.assertNotIn(b'cupy', pickle.dumps(host))
        points = np.array([[50.0, 60.0], [200.0, 210.0], [350.0, 120.0]])
        np.testing.assert_allclose(
            nornir_imageregistration.EnsureNumpyArray(host.Transform(points)),
            nornir_imageregistration.EnsureNumpyArray(loaded.Transform(nornir_imageregistration.EnsureCupyArray(points))),
            atol=1e-3)


if __name__ == '__main__':
    unittest.main()
