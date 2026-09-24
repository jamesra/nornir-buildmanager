"""
The PMG importer searched for *.idoc files.

``pmg.Import`` fell back to ``extension = 'idoc'`` when the caller did not supply one
-- a copy-paste from the idoc importer, which the loop variable named
``idocFullPath`` gave away. A caller relying on the default scanned a directory of
PMG files for idocs, matched nothing, and returned the volume unchanged without
raising or logging: an import that reports success having done nothing.

This is latent rather than live. The ``ImportPMG`` pipeline declares
``<Argument flag="-ext" dest="extension" default="pmg"/>``, so the pipeline always
passes an explicit extension and never reaches the fallback. A sweep of the other
importers found no comparable mismatch -- dm4 and mrc default to their own
extensions, and idoc/sectionimage default to extensions that are correct for what
they read.
"""

from __future__ import annotations

import os

import pytest

from nornir_buildmanager.importers import pmg


class _Volume:
    """Import only reads FullPath, and only once it has matched a file."""

    def __init__(self, path):
        self.FullPath = path


@pytest.fixture
def pmg_dir(tmp_path):
    """A directory of PMG files, with an idoc alongside to catch the old behaviour."""
    section = tmp_path / 'section'
    section.mkdir()

    for name in ('1_A_1_XX_5000_1_DAPI.pmg', '1_A_2_XX_5000_1_DAPI.pmg'):
        (section / name).write_text('pmg', encoding='utf-8')

    (section / 'stray.idoc').write_text('idoc', encoding='utf-8')

    return tmp_path


@pytest.fixture
def imported(monkeypatch):
    """Record what Import hands to ToMosaic instead of doing real work."""
    calls = []

    def record(cls, VolumeElement, path, scale, outputPath, *args, **kwargs):
        calls.append(path)

    monkeypatch.setattr(pmg.PMGImport, 'ToMosaic', classmethod(record))
    return calls


def _run(volume_root, import_path, imported, **kwargs):
    pmg.Import(_Volume(str(volume_root)), str(import_path), scaleValueInNm=4.0, **kwargs)
    return [os.path.basename(p) for p in imported]


# --- the defect ---------------------------------------------------------------

def test_the_default_finds_pmg_files(pmg_dir, imported, tmp_path):
    found = _run(tmp_path, pmg_dir, imported)

    assert sorted(found) == ['1_A_1_XX_5000_1_DAPI.pmg', '1_A_2_XX_5000_1_DAPI.pmg']


def test_the_default_does_not_pick_up_idocs(pmg_dir, imported, tmp_path):
    found = _run(tmp_path, pmg_dir, imported)

    assert not any(name.endswith('.idoc') for name in found), \
        'the PMG importer defaulted to scanning for *.idoc'


def test_a_directory_of_pmg_files_does_not_import_silently_empty(tmp_path, imported):
    """The user-visible symptom: import reported success having done nothing.

    No idoc anywhere, which is what a real PMG capture looks like -- so under the
    old default the scan matched nothing at all and Import returned quietly.
    """
    root = tmp_path / 'capture'
    section = root / 'section'
    section.mkdir(parents=True)
    (section / '1_A_1_XX_5000_1_DAPI.pmg').write_text('pmg', encoding='utf-8')

    assert _run(tmp_path, root, imported) == ['1_A_1_XX_5000_1_DAPI.pmg']


# --- the path the pipeline actually takes must be unaffected ------------------

def test_an_explicit_extension_still_wins(pmg_dir, imported, tmp_path):
    """ImportPMG passes default="pmg" explicitly, so this is the live path."""
    found = _run(tmp_path, pmg_dir, imported, extension='pmg')

    assert sorted(found) == ['1_A_1_XX_5000_1_DAPI.pmg', '1_A_2_XX_5000_1_DAPI.pmg']


def test_an_explicit_extension_can_still_select_something_else(pmg_dir, imported, tmp_path):
    found = _run(tmp_path, pmg_dir, imported, extension='idoc')

    assert found == ['stray.idoc']


def test_an_empty_directory_still_imports_nothing(tmp_path, imported):
    empty = tmp_path / 'empty'
    empty.mkdir()

    assert _run(tmp_path, empty, imported) == []


# --- the copy-paste that caused it --------------------------------------------

def test_the_pipeline_default_and_the_code_default_agree():
    """Divergence here is what made the bug latent and easy to miss."""
    import pathlib
    import re

    pipelines = pathlib.Path(pmg.__file__).parent.parent / 'config' / 'Pipelines.xml'
    text = pipelines.read_text(encoding='utf-8', errors='replace')

    block = text[text.index('Name="ImportPMG"'):]
    block = block[:block.index('</Pipeline>')]
    pipeline_match = re.search(r'dest="extension"\s+default="([^"]+)"', block)
    assert pipeline_match is not None, 'ImportPMG no longer declares an extension default'

    source = pathlib.Path(pmg.__file__).read_text(encoding='utf-8', errors='replace')
    code_match = re.search(
        r"if\s+extension\s+is\s+None:\s*\n\s*extension\s*=\s*['\"]([^'\"]+)['\"]", source)
    assert code_match is not None, 'pmg.Import no longer has an extension fallback'

    assert code_match.group(1) == pipeline_match.group(1) == 'pmg'
