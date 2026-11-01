"""Tests for importers.LoadOrCreateHistogramCutoffs and the importers that call it."""

import errno
import importlib
import os
from pathlib import Path
from unittest import mock

import pytest
from nornir_buildmanager import importers
from nornir_buildmanager.importers.shared import ContrastValue

_DEFAULT_CONTENT = "#Section Min Max Gamma"


def _raise_oserror(code: int):
    """Return a CreateDefaultHistogramCutoffFile stand-in that fails with errno code."""

    def _fail(histogramFilename: str) -> None:
        raise OSError(code, os.strerror(code), histogramFilename)

    return _fail


def test_missing_file_is_created_with_header(tmp_path: Path) -> None:
    filename = tmp_path / "sub" / importers.DefaultHistogramFilename

    loaded = importers.LoadOrCreateHistogramCutoffs(str(filename))

    assert loaded == {}
    assert filename.read_text() == _DEFAULT_CONTENT


def test_existing_overrides_are_returned_and_file_left_unchanged(tmp_path: Path) -> None:
    filename = tmp_path / importers.DefaultHistogramFilename
    content = "#Section Min Max Gamma\n1 10 200\n2 11 201 0.75\n"
    filename.write_text(content)

    loaded = importers.LoadOrCreateHistogramCutoffs(str(filename))

    assert loaded == {1: ContrastValue(1, 10, 200, 1.0), 2: ContrastValue(2, 11, 201, 0.75)}
    assert filename.read_text() == content


@pytest.mark.parametrize(
    ("code", "tolerated", "should_raise"),
    [
        (errno.EROFS, None, False),
        (errno.EACCES, None, True),
        (errno.ENOSPC, None, True),
        (errno.EROFS, (errno.EROFS, errno.EACCES), False),
        (errno.EACCES, (errno.EROFS, errno.EACCES), False),
        (errno.ENOSPC, (errno.EROFS, errno.EACCES), True),
    ],
)
def test_create_failure_is_tolerated_only_for_listed_errnos(tmp_path: Path, code: int,
                                                            tolerated: tuple[int, ...] | None,
                                                            should_raise: bool) -> None:
    filename = str(tmp_path / importers.DefaultHistogramFilename)
    kwargs = {} if tolerated is None else {"tolerated_errnos": tolerated}

    with mock.patch.object(importers, "CreateDefaultHistogramCutoffFile", side_effect=_raise_oserror(code)), \
            mock.patch.object(importers.prettyoutput, "Log") as log:
        if should_raise:
            with pytest.raises(OSError) as raised:
                importers.LoadOrCreateHistogramCutoffs(filename, **kwargs)
            assert raised.value.errno == code
            log.assert_not_called()
        else:
            assert importers.LoadOrCreateHistogramCutoffs(filename, **kwargs) == {}
            log.assert_called_once()
            assert filename in log.call_args.args[0]


class _Stop(Exception):
    """Raised by the patched helper to end an importer generator early."""


@pytest.mark.parametrize(
    ("module_name", "kwargs", "expected_tolerated"),
    [
        ("dm4", {}, None),
        ("mrc", {"Min": 0.1, "Max": 0.9}, (errno.EROFS, errno.EACCES)),
        ("idoc", {"Min": 0.1, "Max": 0.9}, (errno.EROFS, errno.EACCES)),
    ],
)
def test_importers_route_through_helper(tmp_path: Path, module_name: str, kwargs: dict,
                                        expected_tolerated: tuple[int, ...] | None) -> None:
    """Each importer loads overrides from ImportPath via the helper; idoc and mrc also tolerate EACCES."""
    if module_name == "dm4":
        pytest.importorskip("dm4", reason="dm4 package is not installed")
    module = importlib.import_module(f"nornir_buildmanager.importers.{module_name}")
    helper = mock.Mock(side_effect=_Stop)
    with mock.patch.object(importers, "LoadOrCreateHistogramCutoffs", helper), \
            mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("NORNIR_IMPORT_CACHE_PATH", None)
        with pytest.raises(_Stop):
            next(module.Import(mock.Mock(), str(tmp_path), **kwargs))

    helper.assert_called_once()
    assert helper.call_args.args[0] == os.path.join(str(tmp_path), importers.DefaultHistogramFilename)
    if expected_tolerated is None:
        assert "tolerated_errnos" not in helper.call_args.kwargs
    else:
        assert tuple(helper.call_args.kwargs["tolerated_errnos"]) == expected_tolerated
