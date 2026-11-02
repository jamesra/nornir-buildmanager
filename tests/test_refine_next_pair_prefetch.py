"""Each refine job starts loading the next refined pair's images before it computes.

Refine runs one pair at a time, so the pipeline overlaps the next pair's image load
with the current pair: ``RefineInvoker`` gives each job the next job's input stos,
skipping manual copies (they read no images), and ``_run_refine_or_manual_copy``
hands it to the prefetch function before calling RefineFunc.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import cast

from nornir_buildmanager.operations import stosgroup_workers
from nornir_buildmanager.operations.block import (
    _next_refine_inputs,
    _RefineStosJobContext,
    _run_refine_or_manual_copy,
)

_REFINE = stosgroup_workers.RefineScanDecision.REFINE
_MANUAL = stosgroup_workers.RefineScanDecision.MANUAL_COPY


def _job(name: str, decision=_REFINE) -> _RefineStosJobContext:
    return cast(_RefineStosJobContext, SimpleNamespace(decision=decision, input_stos_path=name))


def test_each_job_gets_the_next_refined_input():
    jobs = [_job('a'), _job('b'), _job('c')]

    assert _next_refine_inputs(jobs) == ['b', 'c', None]


def test_manual_copies_are_skipped():
    jobs = [_job('a'), _job('m1', _MANUAL), _job('b'), _job('m2', _MANUAL)]

    assert _next_refine_inputs(jobs) == ['b', 'b', None, None]


def test_no_jobs():
    assert _next_refine_inputs([]) == []


class _SectionMapping:
    def remove(self, node):
        pass


def test_prefetch_runs_before_refine(tmp_path):
    output_stos_path = os.path.join(str(tmp_path), '0002-0001.stos')
    context = cast(_RefineStosJobContext, SimpleNamespace(
        decision=_REFINE,
        decision_reason='',
        output_stos_node=SimpleNamespace(FullPath=output_stos_path, Path='0002-0001.stos'),
        output_section_mapping_node=_SectionMapping(),
        input_stos_path=os.path.join(str(tmp_path), 'in.stos'),
        output_stos_path=output_stos_path,
        mapped_section=2,
        control_section=1,
    ))
    calls: list[tuple[str, str]] = []

    def refine(in_path, _out_path, **_kwargs):
        calls.append(('refine', in_path))

    _run_refine_or_manual_copy(
        context, refine, {},
        prefetch_func=lambda path: calls.append(('prefetch', path)),
        next_input_stos_path='next.stos')

    assert calls == [('prefetch', 'next.stos'), ('refine', context.input_stos_path)]


def test_no_prefetch_without_a_next_pair(tmp_path):
    context = cast(_RefineStosJobContext, SimpleNamespace(
        decision=_REFINE, decision_reason='',
        output_stos_node=SimpleNamespace(FullPath='x', Path='x'),
        output_section_mapping_node=_SectionMapping(),
        input_stos_path='in.stos', output_stos_path=os.path.join(str(tmp_path), 'out.stos'),
        mapped_section=2, control_section=1))
    calls: list[str] = []

    _run_refine_or_manual_copy(
        context, lambda *_a, **_k: None, {},
        prefetch_func=calls.append, next_input_stos_path=None)

    assert calls == []


def _volume_with_two_mappings(tmp_path):
    """Sections 1-3; mappings 2->1 then 3->2 in StosBrute64; Leveled images on disk at downsample 1."""
    import nornir_buildmanager.volumemanager as vm

    from .metadata_port_characterize_data import build_volume

    root = os.path.join(str(tmp_path), 'vol')
    build_volume(root, sections=(1, 2, 3))
    volume = vm.VolumeManager.Load(root)
    block = volume.find('Block')
    group = block.find("StosGroup[@Name='StosBrute64']")
    _, mappings = group.UpdateOrAddChildByAttrib(vm.SectionMappingsNode.Create(MappedSectionNumber=3),
                                                 'MappedSectionNumber')
    mappings.UpdateOrAddChild(vm.TransformNode.Create(
        '3-2', 'Grid', Path='3-2.stos', ControlSectionNumber='2', MappedSectionNumber='3',
        ControlChannelName='TEM', MappedChannelName='TEM', ControlFilterName='Leveled', MappedFilterName='Leveled'))
    stos_map = block.find("StosMap[@Name='PotentialRegistrationChain']")
    stos_map.append(vm.MappingNode.Create(2, [3]))
    for number in (1, 2, 3):
        image = block.GetSection(number).GetChannel('TEM').GetFilter('Leveled').Imageset.find('Level/Image')
        os.makedirs(os.path.dirname(image.FullPath), exist_ok=True)
        with open(image.FullPath, 'wb') as handle:
            handle.write(b'x')
    return block, group, list(stos_map.Mappings)


def test_next_mapping_images_are_prefetched(tmp_path, monkeypatch):
    """The pipeline calls refine once per mapping, so each call prefetches the next mapping's images."""
    from nornir_buildmanager.operations import block as block_ops
    from nornir_imageregistration.refine_shared import image_prefetch

    block, group, mappings = _volume_with_two_mappings(tmp_path)
    seen: list[list[tuple[str, object]]] = []
    monkeypatch.setattr(image_prefetch, 'prefetch_image_files', seen.append)

    block_ops._prefetch_next_mapping_images(mappings[0], group, block, 1, False)

    assert len(seen) == 1
    paths = sorted(os.path.normcase(path) for path, _ in seen[0])
    expected = sorted(os.path.normcase(block.GetSection(n).GetChannel('TEM').GetFilter('Leveled')
                                       .GetImage(1).FullPath) for n in (2, 3))
    assert paths == expected
    assert all(dtype == image_prefetch.refine_image_dtype() for _, dtype in seen[0])


def test_last_mapping_prefetches_nothing(tmp_path, monkeypatch):
    from nornir_buildmanager.operations import block as block_ops
    from nornir_imageregistration.refine_shared import image_prefetch

    block, group, mappings = _volume_with_two_mappings(tmp_path)
    seen: list = []
    monkeypatch.setattr(image_prefetch, 'prefetch_image_files', seen.append)

    block_ops._prefetch_next_mapping_images(mappings[-1], group, block, 1, False)

    assert seen == []
