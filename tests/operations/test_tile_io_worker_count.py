"""Tests for NORNIR_TILE_IO_WORKERS resolution in tile assembly."""

from __future__ import annotations

import logging

import pytest

from nornir_buildmanager.operations import tile as tile_ops


@pytest.fixture(autouse=True)
def _clear_tile_io_workers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tile_ops._TILE_IO_WORKERS_ENV, raising=False)


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
