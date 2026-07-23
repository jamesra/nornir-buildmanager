"""Tests for terminal linear-blend SliceToVolume chain composition."""

from __future__ import annotations

import os
import tempfile
import unittest

import numpy as np

import nornir_imageregistration.transforms
from nornir_buildmanager.operations import block
from nornir_imageregistration.files import stosfile
from nornir_imageregistration.transforms import MeshWithRBFFallback
from nornir_imageregistration.transforms.addition import AddTransforms


def _mesh_from_points(source_points: np.ndarray, target_points: np.ndarray) -> MeshWithRBFFallback:
    point_pairs = np.hstack((target_points, source_points))
    return MeshWithRBFFallback(point_pairs)


def _write_mesh_stos(path: str,
                     mapped_section: int,
                     control_section: int,
                     source_points: np.ndarray,
                     target_points: np.ndarray) -> None:
    stos = stosfile.StosFile()
    stos.TargetSectionNumber = control_section
    stos.SourceSectionNumber = mapped_section
    stos.ControlImageFullPath = f"section_{control_section}.png"
    stos.MappedImageFullPath = f"section_{mapped_section}.png"
    stos.ControlImageDim = [1.0, 1.0, 512.0, 512.0]
    stos.MappedImageDim = [1.0, 1.0, 512.0, 512.0]
    mesh = _mesh_from_points(source_points, target_points)
    stos.Transform = nornir_imageregistration.transforms.TransformToIRToolsString(mesh)  # type: ignore[arg-type]
    stos.Save(path)


class TestSliceToVolumeChainComposition(unittest.TestCase):
    def test_no_linear_blend_clears_blend_parameters(self) -> None:
        """-NoLinearBlend should override pipeline defaults."""
        min_blend, travel_limit, max_blend = block._resolve_slice_to_volume_blend_params(
            True,
            min_blend=0.05,
            travel_limit=512.0,
            max_blend=0.9,
            linear_blend_factor=None,
        )
        self.assertIsNone(min_blend)
        self.assertIsNone(travel_limit)
        self.assertIsNone(max_blend)

    def test_compose_pair_applies_blend_only_once(self) -> None:
        """Terminal blend should modify the composed transform without re-blending the parent hop."""
        source = np.array(
            [[0.0, 0.0], [100.0, 0.0], [0.0, 100.0], [100.0, 100.0], [50.0, 50.0]],
            dtype=np.float64,
        )
        target_ab = source + np.array([[4.0, 1.0], [1.0, 4.0], [-4.0, 1.0], [1.0, -4.0], [2.0, 2.0]])
        target_bc = target_ab + np.array([[6.0, -2.0], [-2.0, 6.0], [6.0, 2.0], [2.0, 6.0], [1.0, 1.0]])

        with tempfile.TemporaryDirectory() as temp_dir:
            ab_path = os.path.join(temp_dir, "3-2.stos")
            bc_path = os.path.join(temp_dir, "2-1.stos")
            _write_mesh_stos(ab_path, 3, 2, source, target_ab)
            _write_mesh_stos(bc_path, 2, 1, target_ab, target_bc)

            rigid_ac = AddTransforms(
                stosfile.RigidTransformFromStosPath(bc_path),
                stosfile.RigidTransformFromStosPath(ab_path),
            )  # type: ignore[arg-type]

            unblended, blended = block._compose_slice_to_volume_pair(
                ab_path,
                bc_path,
                enrich_tolerance=None,
                min_blend=0.2,
                travel_limit=32.0,
                reblend_iterations=1,
                reblend_tolerance=0.5,
                max_blend=None,
                rigid_ac=rigid_ac,
            )

            unblended_transform = nornir_imageregistration.transforms.LoadTransform(unblended.Transform)  # type: ignore[arg-type]
            blended_transform = nornir_imageregistration.transforms.LoadTransform(blended.Transform)  # type: ignore[arg-type]
            self.assertGreater(
                float(np.max(np.abs(blended_transform.TargetPoints - unblended_transform.TargetPoints))),  # type: ignore[attr-defined]
                0.01,
            )

    def test_unblended_sidecar_path(self) -> None:
        """Sidecar naming should be stable for downstream cache lookup."""
        self.assertEqual(
            block._unblended_sidecar_path("/tmp/453-450.stos"),
            "/tmp/453-450.unblended.stos",
        )


if __name__ == "__main__":
    unittest.main()
