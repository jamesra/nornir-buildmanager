"""block._StosOutputSpec: pin the scaled-STOS staleness checks and the paths __GenerateStosFile writes.

The references below are the pre-change bodies of ``IsStosFileOutdated``, ``IsStosNodeOutdated``
and ``__GenerateStosFile``, which each predicted the control/mapped image and mask paths inline.
The properties run the current code against them on generated STOS path combinations, so a
change in which paths are predicted, compared, or passed to ``ChangeTransformPixelSpacing``
(including the order of imageset reads and skipped mask reads) shows up.
"""

from __future__ import annotations

import os
import typing
from unittest import mock

from hypothesis import example, given, settings
from hypothesis import strategies as st
from nornir_buildmanager.operations import block
from nornir_imageregistration.files import stosfile

_ROOT = os.path.abspath(os.sep + 'vol')
_OUTPUT = os.path.abspath(__file__)  # any existing file; RemoveOutdatedFile is patched


class _FakeImageset:
    """Imageset stub that predicts ``<root>/<name>/<downsample>.png`` and logs reads."""

    def __init__(self, name: str, log: list[tuple[str, typing.Any]]) -> None:
        self._name = name
        self._log = log

    def GetOrPredictImageFullPath(self, Downsample: typing.Any) -> str:
        """Return the predicted path and record the read."""
        self._log.append((self._name, Downsample))
        return os.path.join(_ROOT, self._name, f'{Downsample}.png')


class _FakeFilter:
    """FilterNode stub with an image imageset and an optional mask imageset."""

    def __init__(self, name: str, log: list[tuple[str, typing.Any]], has_mask: bool = True) -> None:
        self.Imageset = _FakeImageset(name, log)
        self.MaskImageset = _FakeImageset(name + '_mask', log) if has_mask else None


class _FakeStos:
    """StosFile stub holding the four embedded paths; HasMasks follows StosFile's rule."""

    def __init__(self, control: str | None, mapped: str | None,
                 control_mask: str | None, mapped_mask: str | None) -> None:
        self.ControlImageFullPath = control
        self.MappedImageFullPath = mapped
        self.ControlMaskFullPath = control_mask
        self.MappedMaskFullPath = mapped_mask

    @property
    def HasMasks(self) -> bool:
        """True when both masks are set, as in StosFile."""
        return not (self.ControlMaskFullPath is None or self.MappedMaskFullPath is None)

    def ChangeTransformPixelSpacing(self, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """Return the arguments so the test can compare them."""
        return kwargs


def _same(a: str | None, b: str | None) -> bool:
    return stosfile.paths_refer_to_same_file(a, b)


def _reference_is_stos_file_outdated(InputTransformNode, OutputTransformPath, OutputDownsample,
                                     ControlFilter, MappedFilter, UseMasks) -> bool:
    """Pre-change IsStosFileOutdated body (RemoveOutdatedFile/exists/Load patched by the caller)."""
    if block.files.RemoveOutdatedFile(InputTransformNode.FullPath, OutputTransformPath):
        return True
    if not os.path.exists(OutputTransformPath):
        return True
    if UseMasks is None:
        UseMasks = stosfile.StosFile.Load(InputTransformNode.FullPath).HasMasks
    OutputStos = stosfile.StosFile.Load(OutputTransformPath)
    if not stosfile.stos_transform_maps_onto_control_image(OutputStos):
        return True
    if OutputStos.HasMasks != UseMasks:
        return True
    ControlImageFullPath = ControlFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    MappedImageFullPath = MappedFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    if not (_same(OutputStos.ControlImageFullPath, ControlImageFullPath)
            and _same(OutputStos.MappedImageFullPath, MappedImageFullPath)):
        return True
    if UseMasks:
        ControlMaskImageFullPath = ControlFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
        MappedMaskImageFullPath = MappedFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
        if not (_same(OutputStos.ControlMaskFullPath, ControlMaskImageFullPath)
                and _same(OutputStos.MappedMaskFullPath, MappedMaskImageFullPath)):
            return True
    return False


def _reference_node_tail(OutputStos, ControlFilter, MappedFilter, UseMasks, OutputDownsample) -> bool:
    """Pre-change IsStosNodeOutdated body after the AreStosInputImagesOutdated check."""
    if OutputStos.HasMasks != UseMasks:
        return True
    ControlImageFullPath = ControlFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    MappedImageFullPath = MappedFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    ControlMaskImageFullPath = None
    MappedMaskImageFullPath = None
    if UseMasks:
        ControlMaskImageFullPath = ControlFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
        MappedMaskImageFullPath = MappedFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
    return not (
        _same(OutputStos.ControlImageFullPath, ControlImageFullPath)
        and _same(OutputStos.MappedImageFullPath, MappedImageFullPath)
        and _same(OutputStos.ControlMaskFullPath, ControlMaskImageFullPath)
        and _same(OutputStos.MappedMaskFullPath, MappedMaskImageFullPath)
        and OutputStos.HasMasks == UseMasks)


def _reference_generate_tail(InputStos, InputDownsample, OutputDownsample, ControlFilter, MappedFilter, UseMasks):
    """Pre-change __GenerateStosFile body after the input .stos is loaded."""
    if UseMasks is None:
        UseMasks = InputStos.HasMasks
    ControlImageFullPath = ControlFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    MappedImageFullPath = MappedFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    ControlMaskImageFullPath = None
    MappedMaskImageFullPath = None
    if UseMasks:
        ControlMaskImageFullPath = ControlFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
        MappedMaskImageFullPath = MappedFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
    if not (
            _same(InputStos.ControlImageFullPath, ControlImageFullPath)
            and _same(InputStos.MappedImageFullPath, MappedImageFullPath)
            and _same(InputStos.ControlMaskFullPath, ControlMaskImageFullPath)
            and _same(InputStos.MappedMaskFullPath, MappedMaskImageFullPath)
            and float(OutputDownsample) == InputDownsample
            and InputStos.HasMasks == UseMasks):
        return InputStos.ChangeTransformPixelSpacing(oldspacing=InputDownsample,
                                                     newspacing=OutputDownsample,
                                                     ControlImageFullPath=ControlImageFullPath,
                                                     MappedImageFullPath=MappedImageFullPath,
                                                     ControlMaskFullPath=ControlMaskImageFullPath,
                                                     MappedMaskFullPath=MappedMaskImageFullPath)
    return None


_downsample = st.sampled_from([1, 2, 4, 8, 16, 32])
_use_masks = st.sampled_from([True, False, None])


@st.composite
def _embedded_stos(draw, downsample: int) -> _FakeStos:
    """A STOS whose each path is the predicted one (at ``downsample``), a different one, or (masks) absent."""
    def pick(name: str, optional: bool) -> str | None:
        options = [os.path.join(_ROOT, name, f'{downsample}.png'),
                   os.path.join(_ROOT, name, f'{downsample * 2}.png'),
                   os.path.join(_ROOT, 'other', name, f'{downsample}.png')]
        if optional:
            options.append(None)  # type: ignore[arg-type]
        return draw(st.sampled_from(options))
    return _FakeStos(pick('control', False), pick('mapped', False),
                     pick('control_mask', True), pick('mapped_mask', True))


def _run(fn: typing.Callable[[list], typing.Any], has_mask: bool) -> tuple[typing.Any, list]:
    """Call ``fn`` with fresh filters and return (result or exception repr, imageset read log)."""
    log: list[tuple[str, typing.Any]] = []
    control = _FakeFilter('control', log, has_mask)
    mapped = _FakeFilter('mapped', log, has_mask)
    try:
        result = fn([control, mapped])
    except Exception as e:  # noqa: BLE001 - the reference raises too (no mask imageset)
        result = (type(e).__name__, str(e))
    return result, log


@settings(max_examples=300, deadline=None)
@given(data=st.data(), downsample=_downsample, use_masks=_use_masks, input_has_masks=st.booleans(),
       on_control=st.booleans(), has_mask=st.booleans())
@example(data=None, downsample=8, use_masks=False, input_has_masks=False, on_control=True, has_mask=False)
def test_is_stos_file_outdated_matches_pre_change(data, downsample, use_masks, input_has_masks, on_control,
                                                  has_mask) -> None:
    """IsStosFileOutdated(spec) returns, and reads imagesets, as the pre-change body did."""
    if data is None:  # the @example: output embeds exactly the predicted image paths, no masks
        output = _FakeStos(os.path.join(_ROOT, 'control', '8.png'), os.path.join(_ROOT, 'mapped', '8.png'), None, None)
    else:
        output = data.draw(_embedded_stos(downsample))
    mask = os.path.join(_ROOT, 'm.png') if input_has_masks else None
    input_stos = _FakeStos('a', 'b', mask, mask)
    input_node = mock.Mock(FullPath='input.stos')

    def load(path: str):
        return output if path == _OUTPUT else input_stos

    with mock.patch.object(block.files, 'RemoveOutdatedFile', return_value=False), \
            mock.patch.object(block.stosfile.StosFile, 'Load', side_effect=load), \
            mock.patch.object(block.stosfile, 'stos_transform_maps_onto_control_image', return_value=on_control), \
            mock.patch.object(block.prettyoutput, 'LogErr'):
        expected = _run(lambda f: _reference_is_stos_file_outdated(
            input_node, _OUTPUT, downsample, f[0], f[1], use_masks), has_mask)
        actual = _run(lambda f: block.IsStosFileOutdated(
            input_node, _OUTPUT, block._StosOutputSpec(f[0], f[1], downsample, use_masks)), has_mask)
    assert actual == expected


@settings(max_examples=300, deadline=None)
@given(data=st.data(), downsample=_downsample, use_masks=st.booleans(), has_mask=st.booleans())
def test_is_stos_node_outdated_matches_pre_change(data, downsample, use_masks, has_mask) -> None:
    """IsStosNodeOutdated keeps its signature and result after the spec refactor."""
    output = data.draw(_embedded_stos(downsample))
    output_node = mock.Mock(FullPath=_OUTPUT)
    output_node.attrib = {}
    output_node.GetParent.return_value.AreStosInputImagesOutdated.return_value = False
    with mock.patch.object(block.stosfile.StosFile, 'Load', return_value=output):
        expected = _run(lambda f: _reference_node_tail(output, f[0], f[1], use_masks, downsample), has_mask)
        actual = _run(lambda f: block.IsStosNodeOutdated(
            mock.Mock(FullPath='input.stos'), output_node, f[0], f[1], use_masks, downsample), has_mask)
    assert actual == expected


@settings(max_examples=300, deadline=None)
@given(data=st.data(), downsample=_downsample, input_downsample=_downsample, use_masks=_use_masks,
       has_mask=st.booleans())
def test_generate_stos_file_matches_pre_change(data, downsample, input_downsample, use_masks, has_mask) -> None:
    """__GenerateStosFile passes the same paths and spacing to ChangeTransformPixelSpacing, or returns None."""
    input_stos = data.draw(_embedded_stos(downsample))
    group = mock.Mock(Downsample=float(input_downsample))
    group.Name = 'Grid'
    transform = mock.Mock(FullPath='input.stos')
    transform.FindParent.return_value = group
    generate = getattr(block, '__GenerateStosFile')
    with mock.patch.object(block.stosfile.StosFile, 'Load', return_value=input_stos), \
            mock.patch.object(block.stosfile, 'stos_transform_maps_onto_control_image', return_value=True):
        expected = _run(lambda f: _reference_generate_tail(
            input_stos, float(input_downsample), downsample, f[0], f[1], use_masks), has_mask)
        actual = _run(lambda f: generate(transform, block._StosOutputSpec(f[0], f[1], downsample, use_masks)),
                      has_mask)
    assert actual == expected


def test_mask_paths_without_masks_does_not_read_mask_imagesets() -> None:
    """mask_paths(False) is (None, None) even when the filters have no mask imageset."""
    log: list[tuple[str, typing.Any]] = []
    control: typing.Any = _FakeFilter('control', log, False)
    mapped: typing.Any = _FakeFilter('mapped', log, False)
    spec = block._StosOutputSpec(control, mapped, 4, None)
    assert spec.mask_paths(False) == (None, None)
    assert spec.image_paths() == (os.path.join(_ROOT, 'control', '4.png'), os.path.join(_ROOT, 'mapped', '4.png'))
    assert log == [('control', 4), ('mapped', 4)]
