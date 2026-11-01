"""The dashboard gets one "Meta" status per path, not one per pipeline child element.

``ProcessStageElement`` runs for every child of a pipeline node, so CreateBlobFilter sent
"Meta: .../1753" twice and "Meta: .../1753/TEM" three times within ~20 ms for every
section (Build2 RC, srv, 2026-11-01). The console line still updates each time.
"""

from __future__ import annotations

import unittest
from unittest import mock
from xml.etree import ElementTree

from nornir_buildmanager.pipelinemanager import PipelineManager, XResourceElementWrapper


def _resource(path: str) -> mock.MagicMock:
    elem = mock.MagicMock(spec=XResourceElementWrapper)
    elem.FullPath = path
    return elem


class TestMetaStatusPublishedOncePerPath(unittest.TestCase):

    def test_repeated_path_is_published_once(self) -> None:
        pm = PipelineManager.__new__(PipelineManager)  # class default _last_meta_path applies
        node = ElementTree.fromstring('<Select XPath="Channel" VariableName="c"/>')
        paths = ['/v/1753', '/v/1753', '/v/1753/TEM', '/v/1753/TEM', '/v/1753/TEM', '/v/1754']

        with mock.patch('nornir_buildmanager.pipelinemanager.prettyoutput.CurseString') as curse, \
                mock.patch.object(PipelineManager, 'ProcessSelectNode'):
            for path in paths:
                pm.ProcessStageElement(_resource(path), node, ArgSet=mock.MagicMock())

        self.assertEqual(paths, [c.args[1] for c in curse.call_args_list],
                         'the console line should still update for every element')
        self.assertEqual([True, False, True, False, False, True],
                         [c.kwargs['publish_status'] for c in curse.call_args_list])


if __name__ == '__main__':
    unittest.main()
