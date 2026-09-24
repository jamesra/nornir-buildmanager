"""Tests for shared STOS-group scan walk and progress wrapper."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from hypothesis import given, settings, strategies as st

from nornir_buildmanager.operations.stos_scan import (
    iter_stos_group_transforms,
    report_stos_work_progress,
    stos_transform_sort_key,
)
from nornir_buildmanager.operations import block as block_mod


def _transform(mapped: int, control: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        MappedSectionNumber=mapped,
        ControlSectionNumber=control,
        FullPath=f'/tmp/{name}',
        Path=name,
    )


class TestIterStosGroupTransforms(unittest.TestCase):
    def test_sorts_mapped_then_control_then_basename(self) -> None:
        mapping_a = SimpleNamespace(MappedSectionNumber=3, Transforms=[
            _transform(3, 1, '3-1.stos'),
        ])
        mapping_b = SimpleNamespace(MappedSectionNumber=1, Transforms=[
            _transform(1, 2, '1-2.stos'),
            _transform(1, 1, '1-1.stos'),
        ])
        group = SimpleNamespace(SectionMappings=[mapping_a, mapping_b])
        names = [t.Path for t in iter_stos_group_transforms(group)]
        self.assertEqual(names, ['1-1.stos', '1-2.stos', '3-1.stos'])


class TestReportStosWorkProgress(unittest.TestCase):
    def test_delegates_to_report_iterate(self) -> None:
        with mock.patch('nornir_buildmanager.operations.stos_scan.report_iterate') as report:
            report_stos_work_progress(
                'stos_refine:files', 2, 5, 'StosGridRefine', depth=0, section=3)
        report.assert_called_once_with(
            'stos_refine:files', 2, 5, 'StosGridRefine', depth=0, section=3)

    def test_skips_zero_total(self) -> None:
        with mock.patch('nornir_buildmanager.progress.publish_run_event') as publish:
            report_stos_work_progress('t', 0, 0, 'label')
            publish.assert_not_called()


class TestSliceToVolumeHopNeedsWrite(unittest.TestCase):
    def test_skip_when_output_exists_and_matched(self) -> None:
        mapped = SimpleNamespace(FullPath='/tmp/in.stos')
        output = SimpleNamespace(
            FullPath='/tmp/out.stos',
            IsInputTransformMatched=lambda _src: True,
            ControlToVolumeTransformChecksum='abc',
            IsLinearBlendParamsMatched=lambda *a, **k: True,
        )
        with mock.patch.object(block_mod.os.path, 'exists', return_value=True):
            self.assertFalse(block_mod._slice_to_volume_hop_needs_write(
                mapped, output, is_direct_to_center=True,
                min_blend=None, travel_limit=None,
                reblend_iterations=None, reblend_tolerance=None,
                max_blend=None, use_chain_linear=False))

    def test_needs_write_when_output_missing(self) -> None:
        mapped = SimpleNamespace(FullPath='/tmp/in.stos')
        with mock.patch.object(block_mod.os.path, 'exists', side_effect=lambda p: p == '/tmp/in.stos'):
            self.assertTrue(block_mod._slice_to_volume_hop_needs_write(
                mapped, None, is_direct_to_center=True,
                min_blend=None, travel_limit=None,
                reblend_iterations=None, reblend_tolerance=None,
                max_blend=None, use_chain_linear=False))


@given(st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=20),
        st.integers(min_value=0, max_value=20),
        st.text(alphabet='abcdef', min_size=1, max_size=6),
    ),
    min_size=1,
    max_size=8,
    unique=True,
))
@settings(max_examples=25)
def test_iter_stos_group_transforms_is_sorted(rows: list[tuple[int, int, str]]) -> None:
    transforms = [_transform(m, c, f'{name}.stos') for m, c, name in rows]
    group = SimpleNamespace(SectionMappings=[
        SimpleNamespace(MappedSectionNumber=0, Transforms=transforms),
    ])
    walked = list(iter_stos_group_transforms(group))
    keys = [stos_transform_sort_key(t) for t in walked]
    assert keys == sorted(keys)


if __name__ == '__main__':
    unittest.main()
