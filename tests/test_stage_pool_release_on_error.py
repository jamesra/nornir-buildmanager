"""Stage pool release must run when a STOS stage raises (#142)."""

from __future__ import annotations

import unittest
from unittest import mock

from nornir_buildmanager.operations import block


class TestEnsureStagePoolsReleased(unittest.TestCase):

    def test_releases_on_success(self):
        with mock.patch.object(block.nornir_pools, 'ReleaseStagePools') as release:
            with block._ensure_stage_pools_released():
                pass
            release.assert_called_once_with()

    def test_releases_when_body_raises(self):
        with mock.patch.object(block.nornir_pools, 'ReleaseStagePools') as release:
            with self.assertRaises(RuntimeError):
                with block._ensure_stage_pools_released():
                    raise RuntimeError('stage failed')
            release.assert_called_once_with()

    def test_bare_except_pattern_skipped_release_before_the_fix(self):
        """Pin that a happy-path-only Release leaves pools held after an error."""
        calls = []

        def release():
            calls.append('release')

        try:
            raise RuntimeError('boom')
            release()  # noqa: unreachable — old ScaleStosGroup shape
        except RuntimeError:
            pass
        self.assertEqual([], calls)


if __name__ == '__main__':
    unittest.main()
