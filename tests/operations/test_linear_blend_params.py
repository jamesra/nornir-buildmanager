"""Old-vs-new wiring tests for block._LinearBlendParams (linear-blend parameter object)."""
import dataclasses
import typing
import unittest
from types import SimpleNamespace
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st
from nornir_buildmanager.operations import block
from nornir_buildmanager.volumemanager.transformnode import TransformNode

_opt_float = st.one_of(st.none(), st.floats(min_value=0.0, max_value=4096.0, allow_nan=False))
_opt_iterations = st.one_of(st.none(), st.integers(min_value=0, max_value=16))
_RIGID: typing.Any = mock.sentinel.rigid_ac
_blend_params = st.builds(block._LinearBlendParams,
                          _opt_float, _opt_float, _opt_iterations, _opt_float, _opt_float)


def _new_node() -> TransformNode:
    """Return an unstamped output transform node."""
    return TransformNode.Create(Name="out", Path="out.stos", Type="Grid")


def _old_stamp(node: TransformNode, p: block._LinearBlendParams, chain: bool) -> None:
    """Pre-refactor call shape used at each SetLinearBlendParams site."""
    node.SetLinearBlendParams(p.min_blend,
                              p.travel_limit,
                              p.reblend_iterations,
                              p.reblend_tolerance,
                              max_blend=p.max_blend,
                              chain_consistent_linear=chain)


class TestLinearBlendParamsWiring(unittest.TestCase):
    """The parameter object must call the node methods exactly as the inline calls did."""

    @given(_blend_params, st.booleans())
    @settings(max_examples=200, deadline=None)
    def test_stamp_writes_same_attributes(self, params, chain):
        old_node = _new_node()
        new_node = _new_node()
        _old_stamp(old_node, params, chain)
        params.stamp(new_node, chain)
        self.assertEqual(dict(old_node.attrib), dict(new_node.attrib))

    @given(_blend_params, _blend_params, st.booleans(), st.booleans())
    @settings(max_examples=300, deadline=None)
    def test_is_matched_agrees_with_node_method(self, stored, expected, stored_chain, chain):
        node = _new_node()
        _old_stamp(node, stored, stored_chain)
        old = node.IsLinearBlendParamsMatched(expected.min_blend,
                                              expected.travel_limit,
                                              expected.reblend_iterations,
                                              expected.reblend_tolerance,
                                              max_blend=expected.max_blend,
                                              chain_consistent_linear=chain)
        self.assertEqual(old, expected.is_matched(node, chain))

    def test_is_matched_detects_each_field_change(self):
        base = block._LinearBlendParams(0.05, 64.0, 8, 0.5, 0.9)
        node = _new_node()
        base.stamp(node, True)
        self.assertTrue(base.is_matched(node, True))
        self.assertFalse(base.is_matched(node, False))
        for field, value in (('min_blend', 0.1), ('travel_limit', 32.0), ('reblend_iterations', 4),
                             ('reblend_tolerance', 0.25), ('max_blend', None)):
            with self.subTest(field=field):
                self.assertFalse(dataclasses.replace(base, **{field: value}).is_matched(node, True))

    @given(_opt_float, _opt_float)
    def test_enabled_matches_old_rule(self, min_blend, travel_limit):
        params = block._LinearBlendParams(min_blend, travel_limit, None, None, None)
        self.assertEqual(params.enabled, min_blend is not None or travel_limit is not None)

    def test_use_chain_consistent_linear_truth_table(self):
        for chain in (False, True):
            for min_blend, travel_limit in ((None, None), (0.1, None), (None, 5.0), (0.1, 5.0)):
                params = block._LinearBlendParams(min_blend, travel_limit, None, None, None)
                old = bool(chain and (min_blend is not None or travel_limit is not None))
                self.assertIs(block._use_chain_consistent_linear(chain, params), old)

    def test_frozen(self):
        params = block._LinearBlendParams(None, None, None, None, None)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            params.min_blend = 0.5  # type: ignore[misc]


class TestHopNeedsWriteBlendCheck(unittest.TestCase):
    """_slice_to_volume_hop_needs_write must rebuild an indirect hop when stamped blend params differ."""

    _STAMPED = block._LinearBlendParams(0.05, 64.0, 8, 0.5, None)

    def _needs_write(self, blend: block._LinearBlendParams, use_chain_linear: bool) -> bool:
        stamped = _new_node()
        self._STAMPED.stamp(stamped, True)
        mapped = SimpleNamespace(FullPath='/tmp/in.stos')
        output = SimpleNamespace(
            FullPath='/tmp/out.stos',
            IsInputTransformMatched=lambda _src: True,
            ControlToVolumeTransformChecksum='abc',
            IsLinearBlendParamsMatched=stamped.IsLinearBlendParamsMatched,
        )
        with mock.patch.object(block.os.path, 'exists', return_value=True):
            return block._slice_to_volume_hop_needs_write(
                mapped, output, is_direct_to_center=False,
                blend=blend, use_chain_linear=use_chain_linear)

    def test_matched_params_skip_write(self):
        self.assertFalse(self._needs_write(self._STAMPED, True))

    def test_changed_params_need_write(self):
        self.assertTrue(self._needs_write(dataclasses.replace(self._STAMPED, travel_limit=32.0), True))

    def test_chain_mode_change_needs_write(self):
        self.assertTrue(self._needs_write(self._STAMPED, False))


class TestComposePairBlendKwargs(unittest.TestCase):
    """_compose_slice_to_volume_pair must pass the same blend kwargs as the pre-refactor caller."""

    def _compose(self, params: block._LinearBlendParams) -> mock.MagicMock:
        unblended = mock.MagicMock()
        with mock.patch.object(block.stosfile, 'AddStosTransforms', return_value=unblended), \
                mock.patch.object(block.nornir_imageregistration.transforms, 'LoadTransform',
                                  return_value='nonlinear'), \
                mock.patch.object(block.nornir_imageregistration.transforms.utils,
                                  'BlendTransformsIteratively', return_value='blended') as blend_fn, \
                mock.patch.object(block.nornir_imageregistration.transforms, 'TransformToIRToolsString',
                                  return_value='text'), \
                mock.patch.object(block.copy, 'deepcopy', return_value=mock.MagicMock()):
            first, second = block._compose_slice_to_volume_pair(
                'a.stos', 'b.stos', enrich_tolerance=None, blend=params, rigid_ac=_RIGID)
        self.assertIs(first, unblended)
        if params.enabled:
            self.assertIsNot(second, unblended)
        else:
            self.assertIs(second, unblended)
        return blend_fn

    @given(_blend_params)
    @settings(max_examples=100, deadline=None)
    def test_blend_kwargs_match_old_wiring(self, params):
        blend_fn = self._compose(params)
        if not params.enabled:
            blend_fn.assert_not_called()
            return
        # Old caller passed reblend_iterations=<value> or 1; old helper omitted None tolerance/max.
        expected = {'min_blend': params.min_blend,
                    'travel_limit': params.travel_limit,
                    'reblend_iterations': params.reblend_iterations or 1}
        if params.reblend_tolerance is not None:
            expected['reblend_tolerance'] = params.reblend_tolerance
        if params.max_blend is not None:
            expected['max_blend'] = params.max_blend
        blend_fn.assert_called_once_with('nonlinear', _RIGID, **expected)

    def test_none_iterations_runs_one_pass(self):
        blend_fn = self._compose(block._LinearBlendParams(0.2, None, None, None, None))
        self.assertEqual(blend_fn.call_args.kwargs['reblend_iterations'], 1)


if __name__ == '__main__':
    unittest.main()
