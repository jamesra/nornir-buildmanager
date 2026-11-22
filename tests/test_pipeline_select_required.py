"""<Select Required="True"> stops the pipeline when nothing matches instead of skipping silently.

RC1 (2026-11-21): AlignSections' top-level Select of the StosMap matched nothing, so StosBruteAll never ran and the
pipeline exited 0 after two minutes.
"""

from __future__ import annotations

from unittest import mock
from xml.etree import ElementTree

import pytest
from nornir_buildmanager.exceptions import NornirRethrownException
from nornir_buildmanager.pipeline_exceptions import PipelineError, PipelineSelectFailed
from nornir_buildmanager.pipelinemanager import PipelineManager


def _arg_set() -> mock.Mock:
    args = mock.Mock()
    args.SubstituteStringVariables.side_effect = lambda s: s
    args.Variables = {}
    args.Arguments = {'debug': False}
    return args


def _volume() -> ElementTree.Element:
    root = ElementTree.Element('Block')
    ElementTree.SubElement(root, 'StosMap', Name='N1BruteChain')
    return root


def _select(required: str | None) -> ElementTree.Element:
    node = ElementTree.Element('Select', VariableName='StosMapNodeObj', XPath="StosMap[@Name='Missing']")
    if required is not None:
        node.set('Required', required)
    return node


def _manager() -> PipelineManager:
    return PipelineManager.__new__(PipelineManager)


@pytest.mark.parametrize('required', [None, 'False'])
def test_unrequired_select_without_a_match_skips(required: str | None) -> None:
    with pytest.raises(PipelineSelectFailed):
        _manager().ProcessSelectNode(_arg_set(), _volume(), _select(required))


@pytest.mark.parametrize('required', ['True', 'true', '1', 'yes'])
def test_required_select_without_a_match_is_an_error(required: str) -> None:
    with pytest.raises(PipelineError) as err:
        _manager().ProcessSelectNode(_arg_set(), _volume(), _select(required))
    assert not isinstance(err.value, PipelineSelectFailed)
    assert "StosMap[@Name='Missing']" in err.value.message


def test_required_select_with_a_match_binds_the_variable() -> None:
    args = _arg_set()
    node = _select('True')
    node.set('XPath', "StosMap[@Name='N1BruteChain']")
    _manager().ProcessSelectNode(args, _volume(), node)
    args.AddVariable.assert_called_once()
    assert args.AddVariable.call_args.args[1].get('Name') == 'N1BruteChain'


def test_required_select_stops_the_pipeline_and_later_stages_do_not_run() -> None:
    pipeline = ElementTree.Element('Pipeline', Name='AlignSections')
    pipeline.append(_select('True'))
    ElementTree.SubElement(pipeline, 'PythonCall', Function='block.StosBruteAll')
    manager = _manager()
    with mock.patch.object(PipelineManager, 'ProcessPythonCall') as call, \
            pytest.raises(NornirRethrownException):
        manager.ExecuteChildPipelines(_arg_set(), _volume(), pipeline)
    call.assert_not_called()


def test_unrequired_select_still_skips_the_rest_quietly() -> None:
    pipeline = ElementTree.Element('Pipeline', Name='AlignSections')
    pipeline.append(_select(None))
    ElementTree.SubElement(pipeline, 'PythonCall', Function='block.StosBruteAll')
    with mock.patch.object(PipelineManager, 'ProcessPythonCall') as call:
        assert _manager().ExecuteChildPipelines(_arg_set(), _volume(), pipeline) == 0
    call.assert_not_called()
