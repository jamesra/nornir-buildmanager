"""Regression for #147 / C08-P003: Pillow tileset row pipeline must drain prior row."""
from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from nornir_buildmanager.operations import tile as tile_ops


class _TrackingExecutor:
    """Records map submissions interleaved with iterator drains."""

    def __init__(self, events: list[tuple[str, int]]) -> None:
        self.events = events
        self.map_calls: list[list[tuple[int, int]]] = []
        self._map_index = 0

    def map(self, fn, iterable):
        coords = list(iterable)
        call_i = self._map_index
        self._map_index += 1
        self.map_calls.append(coords)
        self.events.append(('map', call_i))

        def _gen():
            for item in coords:
                yield fn(item)
            self.events.append(('drain', call_i))

        return _gen()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestPillowTilesetRowPipeline(unittest.TestCase):
    def test_prior_row_is_drained_before_next_row_finishes(self) -> None:
        """Broken extend(map) drained each row fully and never used last_column_tasks."""
        events: list[tuple[str, int]] = []
        outer = _TrackingExecutor(events)
        inner = _TrackingExecutor(events)

        def fake_thread_pool(*args, **kwargs):
            if kwargs.get('initializer') is not None or 'initargs' in kwargs:
                return outer
            return inner

        with patch.object(tile_ops, 'ThreadPoolExecutor', side_effect=fake_thread_pool), \
                patch.object(tile_ops, 'pin_directory_for_worker'), \
                patch.object(tile_ops, '_tile_io_worker_count', return_value=2), \
                patch.object(tile_ops, 'ensure_directory'), \
                patch.object(tile_ops.tileset_functions,
                             'CreateOneTilesetTileWithPillowOverNetwork',
                             return_value=None), \
                patch.object(tile_ops.tileset_functions,
                             'find_missing_lineage_parent_tiles',
                             return_value=[]):
            tile_ops.BuildTilesetLevelWithPillow(
                SourcePath='src',
                DestPath='dest',
                DestGridDimensions=(3, 4),
                TileDim=(64, 64),
                FilePrefix='',
                FilePostfix='.png',
                temp_input_dir=None,
                temp_output_dir='tmp_out',
            )

        self.assertEqual(3, len(outer.map_calls))
        # map row1 before drain row0; map row2 before drain row1.
        self.assertEqual(
            [('map', 0), ('map', 1), ('drain', 0), ('map', 2), ('drain', 1), ('drain', 2)],
            events,
        )

    def test_source_no_longer_extends_map_into_accumulator(self) -> None:
        source = inspect.getsource(tile_ops.BuildTilesetLevelWithPillow)
        self.assertNotIn('this_column_tasks.extend', source)
        self.assertNotIn('last_column_tasks = this_column_tasks', source)
        self.assertIn('last_row_tasks = this_row_tasks', source)


if __name__ == '__main__':
    unittest.main()
