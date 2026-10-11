"""catalog.connect closes its sqlite connection when schema setup fails."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from nornir_buildmanager.operations.segmentationtraining import catalog


def test_connect_on_non_sqlite_file_raises_and_releases_file(tmp_path: Path) -> None:
    """A corrupt catalog raises, and the file can be removed while the traceback is alive."""
    db_path = catalog.sqlite_path(tmp_path)
    db_path.write_bytes(b"this is not a sqlite database, just text" * 64)

    with pytest.raises(sqlite3.DatabaseError) as excinfo:
        catalog.connect(tmp_path)

    # excinfo keeps the traceback (and so connect()'s frame locals) alive. On Windows an
    # unclosed connection keeps the file open and the delete fails.
    assert excinfo.value is not None
    os.remove(db_path)
    assert not db_path.exists()


def test_connect_closes_connection_when_schema_step_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any exception from the schema step closes the connection before propagating."""
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def recording_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = real_connect(*args, **kwargs)  # type: ignore[arg-type]
        opened.append(connection)
        return connection

    def failing_schema(connection: sqlite3.Connection) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(catalog.sqlite3, "connect", recording_connect)
    monkeypatch.setattr(catalog, "_ensure_locations_schema", failing_schema)

    with pytest.raises(KeyboardInterrupt):
        catalog.connect(tmp_path)

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_connect_success_returns_open_connection_with_schema(tmp_path: Path) -> None:
    """The success path still returns an open Row connection with the locations table."""
    connection = catalog.connect(tmp_path)
    try:
        assert connection.row_factory is sqlite3.Row
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"locations", "crop_geometry", "source"} <= tables
    finally:
        connection.close()
