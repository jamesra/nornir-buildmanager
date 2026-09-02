"""Regression for #144 / C08-B008: LinearBlend must match chain_consistent_linear."""
import unittest
from unittest.mock import MagicMock

from nornir_buildmanager.operations import block
from nornir_buildmanager.volumemanager.transformnode import TransformNode


class TestLinearBlendChainParams(unittest.TestCase):
    """LinearBlendStosGroup must not ignore a chain-consistent stamp on reuse."""

    def _blend_node(self, *, chain_consistent_linear: bool | None) -> TransformNode:
        node = TransformNode.Create(Name="blend", Path="blend.stos", Type="Grid")
        node.SetLinearBlendParams(
            0.5,
            10.0,
            1,
            0.01,
            max_blend=None,
            chain_consistent_linear=chain_consistent_linear,
        )
        return node

    def test_params_matched_rejects_chain_consistent_stamp(self):
        """A SliceToVolume chain stamp must not look fresh to LinearBlend."""
        stamped_chain = self._blend_node(chain_consistent_linear=True)
        self.assertFalse(
            block._linear_blend_params_matched(
                stamped_chain, 0.5, 10.0, 1, 0.01, None))

    def test_params_matched_accepts_non_chain_stamp(self):
        non_chain = self._blend_node(chain_consistent_linear=False)
        self.assertTrue(
            block._linear_blend_params_matched(
                non_chain, 0.5, 10.0, 1, 0.01, None))

    def test_params_matched_accepts_legacy_unstamped_node(self):
        """Pre-fix LinearBlend outputs omit the attrib; property defaults False."""
        legacy = self._blend_node(chain_consistent_linear=None)
        self.assertFalse(legacy.chain_consistent_linear)
        self.assertTrue(
            block._linear_blend_params_matched(
                legacy, 0.5, 10.0, 1, 0.01, None))

    def test_omit_chain_kwarg_would_incorrectly_keep_chain_stamp(self):
        """Documents the pre-fix omit behaviour so a revert fails this guard."""
        stamped_chain = self._blend_node(chain_consistent_linear=True)
        self.assertTrue(
            stamped_chain.IsLinearBlendParamsMatched(
                0.5, 10.0, 1, 0.01, max_blend=None),
            "omit chain_consistent_linear= still matches; helper must pass False")
        self.assertFalse(
            block._linear_blend_params_matched(
                stamped_chain, 0.5, 10.0, 1, 0.01, None))

    def test_apply_job_stamps_non_chain(self):
        output = TransformNode.Create(Name="out", Path="out.stos", Type="Grid")
        input_node = TransformNode.Create(Name="in", Path="in.stos", Type="Grid")
        input_node.Checksum = "abc"
        context = block._LinearBlendJobContext(
            output_stos_node=output,
            input_transform_node=input_node,
            output_group_node=MagicMock(),
        )
        block._apply_linear_blend_job(
            context, MagicMock(), 0.5, 10.0, 1, 0.01, None)
        self.assertFalse(output.chain_consistent_linear)
        self.assertEqual(output.attrib.get('chain_consistent_linear'), '0')


if __name__ == '__main__':
    unittest.main()
