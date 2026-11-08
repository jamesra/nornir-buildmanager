"""Schema-drift test: every PythonCall in the shipped Pipelines.xml names a stage that resolves.

``PipelineManager.ProcessPythonCall`` looks each stage up by the ``Module`` and ``Function`` strings in
``Pipelines.xml`` only when the pipeline reaches it, so a renamed or removed stage function fails in the
middle of a long run. This enumerates the whole closed set of stage references and resolves each one the
way the pipeline does.
"""

from __future__ import annotations

import ast
import importlib.util
import unittest
import xml.etree.ElementTree as etree
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import NamedTuple

from hypothesis import example, given
from hypothesis import strategies as st
from nornir_shared.reflection import get_module_class

import nornir_buildmanager.config

# Mirrors the default in PipelineManager.ProcessPythonCall.
DEFAULT_STAGE_MODULE = 'nornir_buildmanager.operations'

# Top-level modules a stage module imports but the buildmanager package does not depend on (dm4 is a
# separate package). A stage in such a module that cannot be imported here is only checked statically:
# its module file must still define the named function. Add a name only with the reason.
OPTIONAL_DEPENDENCY_MODULES = frozenset({'dm4'})

PIPELINES_XML = Path(nornir_buildmanager.config.__file__).parent / 'Pipelines.xml'


class StageRef(NamedTuple):
    """The module and function strings a PythonCall element resolves."""

    module: str
    function: str


Resolver = Callable[[str, str], object]


def collect_stage_refs(root: etree.Element) -> list[StageRef]:
    """Return the distinct stage references of every PythonCall under *root*, nested ones included."""
    refs: dict[StageRef, None] = {}
    for call in root.iter('PythonCall'):
        refs[StageRef(call.get('Module', DEFAULT_STAGE_MODULE), call.get('Function', call.tag))] = None
    return list(refs)


def _resolve_stage(module: str, function: str) -> object:
    return get_module_class(module, function, LogErrIfNotFound=False)


def _module_defines(module: str, function: str) -> bool:
    """Return True if the source of *module* defines a top-level function or class named like *function*'s first part."""
    spec = importlib.util.find_spec(module)
    if spec is None or spec.origin is None:
        return False
    tree = ast.parse(Path(spec.origin).read_bytes())
    wanted = function.split('.')[0]
    return any(isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == wanted for node in tree.body)


def find_unresolved(refs: Iterable[StageRef], resolve: Resolver = _resolve_stage) -> dict[StageRef, str]:
    """Return each reference that does not resolve to a callable, with the reason."""
    unresolved: dict[StageRef, str] = {}
    for ref in refs:
        try:
            target = resolve(ref.module, ref.function)
        except ModuleNotFoundError as e:
            if e.name in OPTIONAL_DEPENDENCY_MODULES and _module_defines(ref.module, ref.function):
                continue
            unresolved[ref] = f'module not importable: {e}'
            continue
        except ImportError as e:
            unresolved[ref] = f'import failed: {e}'
            continue
        if target is None:
            unresolved[ref] = 'function not found'
        elif not callable(target):
            unresolved[ref] = f'not callable: {type(target).__name__}'
    return unresolved


def _parse(xml: str) -> etree.Element:
    return etree.fromstring(xml)


class ShippedPipelinesXmlTests(unittest.TestCase):
    """The Pipelines.xml that ships in nornir_buildmanager.config."""

    def test_every_stage_resolves(self) -> None:
        refs = collect_stage_refs(etree.parse(PIPELINES_XML).getroot())
        unresolved = find_unresolved(refs)
        self.assertEqual({}, unresolved, 'PythonCall stages in Pipelines.xml that PipelineManager cannot resolve')

    def test_collection_sees_default_module_and_explicit_module_stages(self) -> None:
        refs = collect_stage_refs(etree.parse(PIPELINES_XML).getroot())
        self.assertIn(StageRef(DEFAULT_STAGE_MODULE, 'tile.VerifyImages'), refs)
        self.assertIn(StageRef('nornir_buildmanager.importers.idoc', 'Import'), refs)
        self.assertGreater(len(refs), 50)


class CollectStageRefsTests(unittest.TestCase):
    """How stage references are read from a pipeline document."""

    def test_function_defaults_to_the_tag_name(self) -> None:
        refs = collect_stage_refs(_parse('<Pipelines><Pipeline><PythonCall/></Pipeline></Pipelines>'))
        self.assertEqual([StageRef(DEFAULT_STAGE_MODULE, 'PythonCall')], refs)

    def test_nested_and_repeated_calls_are_found_once_each(self) -> None:
        refs = collect_stage_refs(_parse(
            '<Pipelines><Pipeline>'
            '<PythonCall Function="a.B"><Iterate><PythonCall Module="m.n" Function="c"/></Iterate></PythonCall>'
            '<PythonCall Function="a.B"/>'
            '</Pipeline></Pipelines>'))
        self.assertEqual([StageRef(DEFAULT_STAGE_MODULE, 'a.B'), StageRef('m.n', 'c')], refs)


class FindUnresolvedTests(unittest.TestCase):
    """The resolution check reports every kind of drift and tolerates only the named optional dependencies."""

    def test_resolvable_stage_is_not_reported(self) -> None:
        refs = [StageRef(DEFAULT_STAGE_MODULE, 'tile.VerifyImages'), StageRef('json', 'dumps')]
        self.assertEqual({}, find_unresolved(refs))

    @given(st.from_regex(r'\Azz_missing_[a-z_]{1,12}\Z'))
    @example('zz_missing_function')
    def test_missing_function_is_reported(self, name: str) -> None:
        ref = StageRef(DEFAULT_STAGE_MODULE, f'tile.{name}')
        self.assertEqual({ref: 'function not found'}, find_unresolved([ref]))

    def test_missing_module_is_reported(self) -> None:
        ref = StageRef('nornir_buildmanager.no_such_module', 'Import')
        self.assertEqual([ref], list(find_unresolved([ref])))

    def test_non_callable_target_is_reported(self) -> None:
        ref = StageRef(DEFAULT_STAGE_MODULE, '__all__')
        self.assertEqual({ref: 'not callable: list'}, find_unresolved([ref]))

    def test_optional_dependency_missing_is_checked_statically(self) -> None:
        def resolver(module: str, function: str) -> object:
            raise ModuleNotFoundError("No module named 'dm4'", name='dm4')

        present = StageRef('nornir_buildmanager.importers.dm4', 'Import')
        renamed = StageRef('nornir_buildmanager.importers.dm4', 'NoSuchImport')
        self.assertEqual([renamed], list(find_unresolved([present, renamed], resolver)))

    def test_import_error_inside_a_stage_module_is_reported(self) -> None:
        def resolver(module: str, function: str) -> object:
            raise ImportError("cannot import name 'x'")

        ref = StageRef(DEFAULT_STAGE_MODULE, 'tile.VerifyImages')
        self.assertEqual({ref: "import failed: cannot import name 'x'"}, find_unresolved([ref], resolver))

    def test_other_missing_dependency_is_reported(self) -> None:
        def resolver(module: str, function: str) -> object:
            raise ModuleNotFoundError("No module named 'other'", name='other')

        ref = StageRef('nornir_buildmanager.importers.dm4', 'Import')
        self.assertEqual([ref], list(find_unresolved([ref], resolver)))


if __name__ == '__main__':
    unittest.main()
