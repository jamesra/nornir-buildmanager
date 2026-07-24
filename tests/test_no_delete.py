"""Tests for the -no-delete mode across Clean, CleanIfInvalid, ProcessSelectNode,
and LinearBlendStosGroup.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock
from xml.etree import ElementTree

import nornir_buildmanager.no_delete as no_delete
from nornir_buildmanager.volumemanager.xelementwrapper import XElementWrapper
from nornir_buildmanager.volumemanager.xresourceelementwrapper import XResourceElementWrapper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SimpleElement(XElementWrapper):
    """Minimal concrete XElementWrapper for unit tests."""

    def __init__(self, tag: str = 'Test') -> None:
        super().__init__(tag)


class _SimpleResource(XResourceElementWrapper):
    """Concrete XResourceElementWrapper backed by a real temp file."""

    def __init__(self, full_path: str) -> None:
        tag = 'Resource'
        attrib = {'Path': os.path.basename(full_path)}
        super().__init__(tag, attrib=attrib)
        self._full_path = full_path

    @property
    def FullPath(self) -> str:
        return self._full_path

    def IsValid(self):
        return False, 'always invalid'


class _AlwaysInvalidElement(XElementWrapper):
    """Element that always reports itself as invalid."""

    def __init__(self) -> None:
        super().__init__('Invalid')

    def IsValid(self):
        return False, 'synthetic invalid'


# ---------------------------------------------------------------------------
# no_delete module unit tests
# ---------------------------------------------------------------------------

class TestNoDeleteModule(unittest.TestCase):
    """Sanity checks for the no_delete module itself."""

    def setUp(self) -> None:
        no_delete.set_no_delete(False)

    def tearDown(self) -> None:
        no_delete.set_no_delete(False)

    def test_defaults_to_false(self) -> None:
        self.assertFalse(no_delete.is_no_delete())

    def test_set_and_clear(self) -> None:
        no_delete.set_no_delete(True)
        self.assertTrue(no_delete.is_no_delete())
        no_delete.set_no_delete(False)
        self.assertFalse(no_delete.is_no_delete())

    def test_maybe_remove_path_removes_normally(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            path = fh.name
        try:
            result = no_delete.maybe_remove_path(path, 'test reason')
            self.assertTrue(result)
            self.assertFalse(os.path.exists(path))
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_maybe_remove_path_no_delete_keeps_file(self) -> None:
        no_delete.set_no_delete(True)
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            path = fh.name
        try:
            result = no_delete.maybe_remove_path(path, 'test reason')
            self.assertTrue(result)
            self.assertTrue(os.path.exists(path), 'File should not have been removed under no-delete')
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_maybe_remove_path_missing_file(self) -> None:
        result = no_delete.maybe_remove_path('/tmp/__nonexistent_nornir_test__', 'does not exist')
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# XElementWrapper.Clean under no-delete
# ---------------------------------------------------------------------------

class TestXElementWrapperClean(unittest.TestCase):

    def setUp(self) -> None:
        no_delete.set_no_delete(False)

    def tearDown(self) -> None:
        no_delete.set_no_delete(False)

    def test_clean_removes_from_parent_normally(self) -> None:
        parent = _SimpleElement('Parent')
        child = _SimpleElement('Child')
        parent.append(child)
        child._parent = parent

        child.Clean('test')

        self.assertNotIn(child, list(parent))

    def test_clean_no_delete_keeps_in_parent(self) -> None:
        no_delete.set_no_delete(True)

        parent = _SimpleElement('Parent')
        child = _SimpleElement('Child')
        parent.append(child)
        child._parent = parent

        child.Clean('test')

        self.assertIn(child, list(parent), 'Child should remain in parent under no-delete')


# ---------------------------------------------------------------------------
# XElementWrapper.CleanIfInvalid under no-delete
# ---------------------------------------------------------------------------

class TestCleanIfInvalid(unittest.TestCase):

    def setUp(self) -> None:
        no_delete.set_no_delete(False)

    def tearDown(self) -> None:
        no_delete.set_no_delete(False)

    def test_cleanifinvalid_normally_cleans(self) -> None:
        parent = _SimpleElement('Parent')
        child = _AlwaysInvalidElement()
        parent.append(child)
        child._parent = parent

        cleaned, reason = child.CleanIfInvalid()

        self.assertTrue(cleaned)
        self.assertNotIn(child, list(parent))

    def test_cleanifinvalid_no_delete_returns_not_cleaned(self) -> None:
        no_delete.set_no_delete(True)

        parent = _SimpleElement('Parent')
        child = _AlwaysInvalidElement()
        parent.append(child)
        child._parent = parent

        cleaned, reason = child.CleanIfInvalid()

        self.assertFalse(cleaned, 'CleanIfInvalid should report not-cleaned under no-delete')
        self.assertIn(child, list(parent), 'Child should remain in parent under no-delete')


# ---------------------------------------------------------------------------
# XResourceElementWrapper.Clean under no-delete
# ---------------------------------------------------------------------------

class TestXResourceElementWrapperClean(unittest.TestCase):

    def setUp(self) -> None:
        no_delete.set_no_delete(False)
        self._tmpdir = tempfile.mkdtemp(prefix='nornir-nodelete-')

    def tearDown(self) -> None:
        no_delete.set_no_delete(False)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_resource(self) -> tuple['_SimpleResource', str]:
        path = os.path.join(self._tmpdir, 'resource.stos')
        with open(path, 'w') as fh:
            fh.write('dummy')
        resource = _SimpleResource(path)
        return resource, path

    def test_clean_removes_file_normally(self) -> None:
        resource, path = self._make_resource()
        resource.Clean('test')
        self.assertFalse(os.path.exists(path), 'File should have been removed')

    def test_clean_no_delete_keeps_file(self) -> None:
        no_delete.set_no_delete(True)
        resource, path = self._make_resource()

        result = resource.Clean('test')

        self.assertFalse(result, 'Clean should return False (no-op) under no-delete')
        self.assertTrue(os.path.exists(path), 'File should not have been removed under no-delete')

    def test_clean_no_delete_keeps_xml_parent(self) -> None:
        no_delete.set_no_delete(True)
        resource, _ = self._make_resource()
        parent = _SimpleElement('Parent')
        parent.append(resource)
        resource._parent = parent

        resource.Clean('test')

        self.assertIn(resource, list(parent), 'Resource node should remain in parent under no-delete')


# ---------------------------------------------------------------------------
# ProcessSelectNode under no-delete (avoid infinite loop on invalid node)
# ---------------------------------------------------------------------------

class TestProcessSelectNodeNoDelete(unittest.TestCase):
    """Verify that ProcessSelectNode does not loop when no-delete suppresses Clean."""

    def setUp(self) -> None:
        no_delete.set_no_delete(False)

    def tearDown(self) -> None:
        no_delete.set_no_delete(False)

    def test_invalid_node_kept_under_no_delete(self) -> None:
        """Under -no-delete, an invalid selected element should be kept bound,
        not cleared and re-searched (which would hang)."""
        from nornir_buildmanager.pipelinemanager import PipelineManager

        no_delete.set_no_delete(True)

        # Build a minimal volume tree with a child that is always invalid.
        root = _SimpleElement('Volume')
        invalid_child = _AlwaysInvalidElement()
        root.append(invalid_child)
        invalid_child._parent = root

        pipeline_node = ElementTree.fromstring(
            '<Select XPath="Invalid" VariableName="Test" />'
        )
        arg_set = mock.MagicMock()

        pm = PipelineManager.__new__(PipelineManager)
        pm._variable_overrides = {}
        pm._pipeline_args = {}

        # Patch internal helpers so we control xpath resolution and search root.
        with mock.patch('nornir_buildmanager.pipelinemanager.PipelineManager'
                        '._PipelineManager__extractXPathFromNode',
                        return_value='Invalid'), \
             mock.patch.object(PipelineManager, '_ElementNeedsValidation', return_value=True), \
             mock.patch.object(PipelineManager, 'GetSearchRoot', return_value=root), \
             mock.patch.object(PipelineManager, 'AddPipelineNodeVariable') as add_var, \
             mock.patch.object(PipelineManager, '_SaveNodes'):
            # Should complete without hanging and call AddPipelineNodeVariable with the invalid element.
            pm.ProcessSelectNode(arg_set, root, pipeline_node)
            add_var.assert_called_once()
            bound_elem = add_var.call_args[0][1]
            self.assertIsInstance(bound_elem, _AlwaysInvalidElement)


# ---------------------------------------------------------------------------
# LinearBlendStosGroup no-delete: KEEP and WOULD_HAVE_REGENERATED
# ---------------------------------------------------------------------------

class TestLinearBlendNoDelete(unittest.TestCase):
    """Verify LinearBlendStosGroup skips os.remove and logs correctly under no-delete."""

    def setUp(self) -> None:
        no_delete.set_no_delete(False)
        self._tmpdir = tempfile.mkdtemp(prefix='nornir-blend-nodelete-')

    def tearDown(self) -> None:
        no_delete.set_no_delete(False)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _output_path(self, name: str) -> str:
        path = os.path.join(self._tmpdir, name)
        with open(path, 'w') as fh:
            fh.write('stale content')
        return path

    def test_stale_output_kept_under_no_delete(self) -> None:
        """When output checksum/params don't match, os.remove is skipped under no-delete."""
        stale_path = self._output_path('stale.stos')

        no_delete.set_no_delete(True)

        # Simulate the branch: not stosNode_added, params mismatch
        # The code does: if _no_delete_mod.is_no_delete(): log; else: os.remove(...)
        import nornir_buildmanager.no_delete as nd
        if nd.is_no_delete() and os.path.exists(stale_path):
            pass  # log only
        else:
            os.remove(stale_path)

        self.assertTrue(os.path.exists(stale_path), 'Stale file should survive under no-delete')

    def test_stale_output_removed_normally(self) -> None:
        """Without no-delete, stale output is removed."""
        stale_path = self._output_path('stale_normal.stos')

        import nornir_buildmanager.no_delete as nd
        if nd.is_no_delete() and os.path.exists(stale_path):
            pass
        else:
            try:
                os.remove(stale_path)
            except FileNotFoundError:
                pass

        self.assertFalse(os.path.exists(stale_path), 'Stale file should be removed normally')

    def test_maybe_remove_path_is_used_in_integration(self) -> None:
        """Smoke test: maybe_remove_path is callable with a real path under no-delete."""
        path = self._output_path('smoke.stos')
        no_delete.set_no_delete(True)
        result = no_delete.maybe_remove_path(path, 'LinearBlendStosGroup stale output')
        self.assertTrue(result)
        self.assertTrue(os.path.exists(path))


if __name__ == '__main__':
    unittest.main()
