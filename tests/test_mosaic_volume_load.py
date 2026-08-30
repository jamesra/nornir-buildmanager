"""MosaicVolume.Load takes transform nodes; two callers handed it paths.

Review finding C00-P006 read this helper as "loads full mosaic files into pools; should
pass paths and stream tile jobs". The pool submission already passes a path and loads
inside the worker:

    pool.add_task("Load %s" % transform.FullPath,
                  mosaic.Mosaic.LoadFromMosaicFile, transform.FullPath)

so no mosaic object is ever shipped into the pool. What the finding's suggested direction
misses is that ``Load`` itself cannot work from paths: it needs each node's ``Section`` and
``Channel`` parents to key the section, and ``Save`` writes back through
``mosaicObj.transformNode``. A path supplies neither.

Two callers tried it anyway, both spelled ``[tnode.FullPath for tnode in ...]``:

  * ``block.ReportVolumeBounds`` -- reachable, so the operation raised
    ``AttributeError: 'str' object has no attribute 'FullPath'``.
  * ``MosaicVolume.LoadVolume`` -- same bug, no caller.

Both now pass nodes. Because the mistake was made twice independently, ``Load`` also
rejects strings up front with a message naming the contract, rather than failing later
inside the loop with an AttributeError that explains nothing.
"""
from __future__ import annotations

import os
import unittest

from nornir_buildmanager.operations.helpers.mosaicvolume import MosaicVolume

TESTDATA = os.environ.get('TESTINPUTPATH', r'D:\nornir-testdata')
REAL_MOSAIC = os.path.join(TESTDATA, 'Transforms', 'mosaics', 'IDOC1', 'ChannelToVolume.mosaic')


class _FakeSection:
    def __init__(self, number: int):
        self.Number = number


class _FakeChannel:
    def __init__(self, name: str):
        self.Name = name


class _FakeTransformNode:
    """The surface of TransformNode that MosaicVolume.Load actually touches."""

    def __init__(self, full_path: str, section_number: int = 42, channel: str = 'TEM'):
        self.FullPath = full_path
        self._section = _FakeSection(section_number)
        self._channel = _FakeChannel(channel)

    def FindParent(self, kind: str):
        if kind == 'Section':
            return self._section
        if kind == 'Channel':
            return self._channel
        raise AssertionError(f'unexpected parent request: {kind}')


@unittest.skipUnless(os.path.exists(REAL_MOSAIC), f'needs test data at {REAL_MOSAIC}')
class TestLoadAcceptsNodes(unittest.TestCase):

    def test_a_single_node_loads(self):
        vol = MosaicVolume.Load([_FakeTransformNode(REAL_MOSAIC)])

        self.assertEqual(list(vol.SectionToVolumeTransforms.keys()), ['42_TEM'])

    def test_the_section_key_combines_section_number_and_channel(self):
        vol = MosaicVolume.Load(
            [_FakeTransformNode(REAL_MOSAIC, section_number=7, channel='Mask')])

        self.assertIn('7_Mask', vol.SectionToVolumeTransforms)

    def test_several_nodes_all_load(self):
        nodes = [_FakeTransformNode(REAL_MOSAIC, section_number=n) for n in (1, 2, 3)]

        vol = MosaicVolume.Load(nodes)

        self.assertEqual(sorted(vol.SectionToVolumeTransforms.keys()),
                         ['1_TEM', '2_TEM', '3_TEM'])

    def test_the_loaded_mosaic_keeps_a_handle_on_its_node(self):
        """Save() writes back through this; a path could not provide it."""
        node = _FakeTransformNode(REAL_MOSAIC)

        vol = MosaicVolume.Load([node])

        self.assertIs(vol.SectionToVolumeTransforms['42_TEM'].transformNode, node)

    def test_an_empty_list_yields_an_empty_volume(self):
        vol = MosaicVolume.Load([])

        self.assertEqual(len(vol.SectionToVolumeTransforms), 0)

    def test_an_iterator_is_accepted(self):
        """Load consumes the argument twice, so it must materialise it first."""
        nodes = iter([_FakeTransformNode(REAL_MOSAIC)])

        vol = MosaicVolume.Load(nodes)

        self.assertEqual(list(vol.SectionToVolumeTransforms.keys()), ['42_TEM'])


class TestLoadRejectsPaths(unittest.TestCase):
    """The regression: paths used to fail late and cryptically."""

    def test_a_path_string_raises_type_error(self):
        with self.assertRaises(TypeError) as ctx:
            MosaicVolume.Load([REAL_MOSAIC])

        message = str(ctx.exception)
        self.assertIn('transform nodes, not paths', message)
        # The offending value is quoted with !r, which escapes Windows separators, so
        # match on the filename rather than the full path.
        self.assertIn('ChannelToVolume.mosaic', message)

    def test_it_is_not_the_old_attribute_error(self):
        """'str' object has no attribute 'FullPath' named neither cause nor contract."""
        with self.assertRaises(TypeError):
            MosaicVolume.Load([REAL_MOSAIC])

    def test_a_path_mixed_in_among_nodes_is_still_caught(self):
        with self.assertRaises(TypeError) as ctx:
            MosaicVolume.Load([_FakeTransformNode(REAL_MOSAIC), REAL_MOSAIC])

        self.assertIn('1 str of 2 entries', str(ctx.exception))

    def test_the_check_happens_before_any_loading(self):
        """A bad list must not half-build a volume or spin up pool work."""
        missing = os.path.join(TESTDATA, 'definitely', 'not', 'here.mosaic')

        with self.assertRaises(TypeError):
            MosaicVolume.Load([missing])


class TestCallersPassNodes(unittest.TestCase):
    """Pin the two call sites, so the path conversion cannot creep back in."""

    def test_report_volume_bounds_does_not_convert_to_paths(self):
        import inspect

        from nornir_buildmanager.operations import block

        source = inspect.getsource(block.ReportVolumeBounds)

        self.assertNotIn('FullPath for tnode', source)
        self.assertIn('MosaicVolume.Load(StosMosaicTransformNodes)', source)

    def test_load_volume_does_not_convert_to_paths(self):
        import inspect

        source = inspect.getsource(MosaicVolume.LoadVolume)

        self.assertNotIn('FullPath for tnode', source)


if __name__ == '__main__':
    unittest.main()
