"""Unit tests for TransformRefineOrchestrator."""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace

from nornir_buildmanager.operations.transform_refine_orchestrator import (
    TransformRefineOrchestrator,
)


class TestTransformRefineOrchestrator(unittest.TestCase):
    """Checksum / stale-output skip decisions."""

    def test_skip_when_checksum_matches(self) -> None:
        """Matching InputTransformChecksum skips refine."""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, 'in.mosaic')
            output_path = os.path.join(tmp, 'out.mosaic')
            with open(input_path, 'w', encoding='utf-8') as handle:
                handle.write('in')
            with open(output_path, 'w', encoding='utf-8') as handle:
                handle.write('out')
            os.utime(input_path, (1, 1))
            os.utime(output_path, (2, 2))
            input_node = SimpleNamespace(Checksum='abc', FullPath=input_path)
            output_node = SimpleNamespace(
                Locked=False,
                FullPath=output_path,
                InputTransformChecksum='abc',
                attrib={'InputTransformChecksum': 'abc'},
            )
            decision = TransformRefineOrchestrator().should_skip_refine(
                input_node, output_node, input_checksum='abc')
            self.assertTrue(decision.skip)

    def test_no_skip_when_input_newer(self) -> None:
        """Newer input transform file forces rebuild."""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, 'in.mosaic')
            output_path = os.path.join(tmp, 'out.mosaic')
            with open(input_path, 'w', encoding='utf-8') as handle:
                handle.write('in')
            with open(output_path, 'w', encoding='utf-8') as handle:
                handle.write('out')
            os.utime(output_path, (1, 1))
            os.utime(input_path, (2, 2))
            input_node = SimpleNamespace(Checksum='abc', FullPath=input_path)
            output_node = SimpleNamespace(
                Locked=False,
                FullPath=output_path,
                InputTransformChecksum='abc',
                attrib={'InputTransformChecksum': 'abc'},
            )
            decision = TransformRefineOrchestrator().should_skip_refine(
                input_node, output_node, input_checksum='abc')
            self.assertFalse(decision.skip)
            self.assertIn('newer', decision.reason)


if __name__ == '__main__':
    unittest.main()
