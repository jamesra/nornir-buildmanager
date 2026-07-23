"""Tests for NORNIR_TILE_IO_WORKERS resolution in tile assembly."""

from __future__ import annotations

import logging

import pytest

from nornir_buildmanager.operations import tile as tile_ops


@pytest.fixture(autouse=True)
def _clear_tile_io_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tile_ops._TILE_IO_WORKERS_ENV, raising=False)
    monkeypatch.delenv(tile_ops._TILE_ENCODE_WORKERS_ENV, raising=False)
    monkeypatch.delenv(tile_ops._TILE_COPY_WORKERS_ENV, raising=False)
    monkeypatch.delenv(tile_ops._TILE_TWO_STAGE_SAVE_ENV, raising=False)


def test_tile_io_worker_count_default_when_unset() -> None:
    assert tile_ops._tile_io_worker_count() == tile_ops._TILE_IO_WORKERS_DEFAULT


def test_tile_io_worker_count_default_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tile_ops._TILE_IO_WORKERS_ENV, "   ")
    assert tile_ops._tile_io_worker_count() == tile_ops._TILE_IO_WORKERS_DEFAULT


def test_tile_io_worker_count_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tile_ops._TILE_IO_WORKERS_ENV, "32")
    assert tile_ops._tile_io_worker_count() == 32


def test_tile_io_worker_count_invalid_string_warns_and_defaults(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(tile_ops._TILE_IO_WORKERS_ENV, "not-a-number")
    with caplog.at_level(logging.WARNING):
        assert tile_ops._tile_io_worker_count() == tile_ops._TILE_IO_WORKERS_DEFAULT
    assert tile_ops._TILE_IO_WORKERS_ENV in caplog.text
    assert "not-a-number" in caplog.text


def test_tile_io_worker_count_non_positive_warns_and_defaults(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(tile_ops._TILE_IO_WORKERS_ENV, "0")
    with caplog.at_level(logging.WARNING):
        assert tile_ops._tile_io_worker_count() == tile_ops._TILE_IO_WORKERS_DEFAULT
    assert ">= 1" in caplog.text


def test_tile_encode_worker_count_defaults_to_io_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tile_ops._TILE_IO_WORKERS_ENV, "12")
    assert tile_ops._tile_encode_worker_count() == 12


def test_tile_encode_worker_count_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tile_ops._TILE_ENCODE_WORKERS_ENV, "24")
    assert tile_ops._tile_encode_worker_count() == 24


def test_tile_copy_worker_count_default() -> None:
    assert tile_ops._tile_copy_worker_count() == 2
    assert tile_ops._tile_copy_worker_count() == tile_ops._TILE_COPY_WORKERS_DEFAULT


def test_tile_copy_worker_count_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tile_ops._TILE_COPY_WORKERS_ENV, "4")
    assert tile_ops._tile_copy_worker_count() == 4


def test_use_two_stage_tile_save_always_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert tile_ops._use_two_stage_tile_save() is True
    monkeypatch.setenv(tile_ops._TILE_TWO_STAGE_SAVE_ENV, "0")
    assert tile_ops._use_two_stage_tile_save() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "FALSE"])
def test_warn_deprecated_two_stage_save_opt_out(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(tile_ops._TILE_TWO_STAGE_SAVE_ENV, value)
    with caplog.at_level(logging.WARNING):
        tile_ops._warn_deprecated_two_stage_save_opt_out()
    assert tile_ops._TILE_TWO_STAGE_SAVE_ENV in caplog.text
    assert "deprecated" in caplog.text.lower()


def test_assemble_tileset_numpy_does_not_use_monolithic_executor() -> None:
    """Production assemble must always use two-stage save."""
    from pathlib import Path

    source_path = Path(tile_ops.__file__)
    source = source_path.read_text(encoding="utf-8")
    assemble_start = source.find("def AssembleTilesetNumpy(")
    assemble_end = source.find("\ndef BuildImagePyramid(", assemble_start)
    assert assemble_start >= 0 and assemble_end > assemble_start
    assemble_body = source[assemble_start:assemble_end]
    assert "monolithic_executor" not in assemble_body
