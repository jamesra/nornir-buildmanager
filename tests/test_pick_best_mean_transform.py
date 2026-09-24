"""Best-mean transform selection must not swallow KeyboardInterrupt (#146)."""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest import mock

from nornir_buildmanager.operations import block


class TestPickBestMeanTransform(unittest.TestCase):

    def test_picks_lowest_mean(self):
        tasks = [
            SimpleNamespace(transform_node='A', wait_return=lambda: '3.0'),
            SimpleNamespace(transform_node='B', wait_return=lambda: '1.5'),
            SimpleNamespace(transform_node='C', wait_return=lambda: '2.0'),
        ]
        logger = mock.Mock(spec=logging.Logger)
        self.assertEqual('B', block._pick_best_mean_transform(tasks, logger))
        logger.error.assert_not_called()

    def test_skips_failed_candidates_and_logs(self):
        tasks = [
            SimpleNamespace(transform_node='A', wait_return=mock.Mock(side_effect=RuntimeError('boom'))),
            SimpleNamespace(transform_node='B', wait_return=lambda: '2.0'),
        ]
        logger = mock.Mock(spec=logging.Logger)
        self.assertEqual('B', block._pick_best_mean_transform(tasks, logger))
        logger.error.assert_called()

    def test_all_failures_return_none(self):
        tasks = [
            SimpleNamespace(transform_node='A', wait_return=mock.Mock(side_effect=ValueError('bad'))),
        ]
        logger = mock.Mock(spec=logging.Logger)
        self.assertIsNone(block._pick_best_mean_transform(tasks, logger))

    def test_keyboard_interrupt_propagates(self):
        tasks = [
            SimpleNamespace(
                transform_node='A',
                wait_return=mock.Mock(side_effect=KeyboardInterrupt)),
        ]
        logger = mock.Mock(spec=logging.Logger)
        with self.assertRaises(KeyboardInterrupt):
            block._pick_best_mean_transform(tasks, logger)


if __name__ == '__main__':
    unittest.main()
