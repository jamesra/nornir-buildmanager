"""
The idoc importer materialized every section before importing the first one.

``find_sections`` is deliberately incremental: it drops each candidate as its
directory scan lands and yields a section the moment that section's last candidate
is consumed. ``Import`` then did::

    found_sections = list(find_sections(extension, found_section_candidates))
    total_idocs = sum(len(idocFileList) for _, idocFileList in found_sections)

which drains the generator before any import work begins, so the first section
waits on a scan of every section directory in the import tree.

The ``list()`` was there to total the idocs for the progress bar, and that total is
genuinely unknowable without scanning every section directory. Progress is now
counted in sections instead, which is known immediately from the single top-level
scan ``find_section_candidates`` already performs, so nothing has to be drained.

Measured on a 200-section tree with 5ms per directory:

    list()      first section 44.3 ms, all sections 44.3 ms
    streaming   first section  9.9 ms, all sections 44.2 ms
"""

from __future__ import annotations

import os
from typing import cast

import pytest

from nornir_buildmanager.importers import find, idoc
from nornir_buildmanager.volumemanager import VolumeNode

EXTENSION = '*.idoc'


@pytest.fixture
def section_tree(tmp_path):
    """Twelve section directories, each holding one idoc."""
    for i in range(1, 13):
        section = tmp_path / f'{i:04d}'
        section.mkdir()
        (section / '1.idoc').write_text('x', encoding='utf-8')

    return tmp_path


def _candidates(root):
    return find.find_section_candidates(str(root), None)


class _Volume:
    """Import only passes this through to ToMosaic, which is stubbed."""

    def __init__(self, path):
        self.FullPath = str(path)


# --- the generator must not be drained before the first section ---------------

def test_import_starts_work_before_every_directory_is_scanned(section_tree, monkeypatch):
    """The defect proper: Import drained find_sections before importing anything.

    ``find_sections`` was always incremental, so testing it alone cannot see this.
    The stall was in how Import consumed it.

    Note this counts sections pulled from the generator, not directories scanned.
    ``section_directory_metadata_generator`` hands the whole candidate list to
    ``ThreadPoolExecutor.map``, which submits every scan immediately, so the scans
    are issued eagerly either way. What streaming changes is that import begins when
    the *first* scan lands instead of the *last* -- which is where the measured
    44.3ms to 9.9ms comes from.
    """
    consumed = []
    real_find_sections = idoc.find_sections

    def counting_find_sections(extension, candidates):
        for item in real_find_sections(extension, candidates):
            consumed.append(item[0].number)
            yield item

    monkeypatch.setattr(idoc, 'find_sections', counting_find_sections)

    consumed_when_work_started = []

    def fake_to_mosaic(cls, VolumeObj, idocFileFullPath, *args, **kwargs):
        consumed_when_work_started.append(len(consumed))
        yield None

    monkeypatch.setattr(idoc.SerialEMIDocImport, 'ToMosaic', classmethod(fake_to_mosaic))

    generator = idoc.Import(cast(VolumeNode, _Volume(section_tree)), str(section_tree),
                            Min=0.0, Max=1.0)
    next(generator)     # advance only as far as the first section's import

    assert consumed_when_work_started == [1], (
        f'Import pulled {consumed_when_work_started} sections from the generator '
        'before starting the first import; streaming should pull exactly one')


def test_import_iterates_the_generator_rather_than_a_list():
    """A list() here would silently reintroduce the stall."""
    import inspect

    source = inspect.getsource(idoc.Import)
    code = '\n'.join(line for line in source.splitlines()
                     if not line.lstrip().startswith('#'))

    assert 'list(find_sections' not in code
    assert 'for (section_meta_data, idocFileList) in find_sections(' in code


# --- and must still produce exactly the same sections -------------------------

def test_streaming_yields_every_section(section_tree):
    found = list(idoc.find_sections(EXTENSION, _candidates(section_tree)))

    assert sorted(m.number for m, _ in found) == list(range(1, 13))


def test_every_section_still_reports_its_idoc(section_tree):
    found = list(idoc.find_sections(EXTENSION, _candidates(section_tree)))

    for _meta, idoc_files in found:
        assert [os.path.basename(p) for p in idoc_files] == ['1.idoc']


# --- the progress total ------------------------------------------------------

def test_the_section_total_is_known_without_draining_anything(section_tree):
    """What replaced total_idocs: available from the top-level scan alone."""
    assert len(_candidates(section_tree)) == 12


def test_the_candidate_count_is_captured_before_it_is_consumed(section_tree):
    """find_sections mutates the dict, so len() after iterating would read zero."""
    candidates = _candidates(section_tree)
    before = len(candidates)

    list(idoc.find_sections(EXTENSION, candidates))

    assert before == 12
    assert len(candidates) < before, (
        'find_sections consumes the dict; the total must be taken up front')
