"""Training-loss table ``location_scores``. Not the ``locations.sam2*`` columns."""

from __future__ import annotations

import sqlite3

_SCORE_SQL = """
CREATE TABLE IF NOT EXISTS location_scores (
    location_id INTEGER NOT NULL,
    image_key TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    score REAL NOT NULL,
    PRIMARY KEY (location_id, image_key, epoch)
)
"""


def ensure_location_scores_schema(connection: sqlite3.Connection) -> None:
    """Create per-window scores, or copy a location-only table under an empty image key.

    Older catalogs stored one score per location per epoch. Those rows stay
    readable with ``image_key=''`` until a later epoch writes the window.
    """
    connection.execute(_SCORE_SQL)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(location_scores)")}
    if "image_key" in columns:
        return
    connection.execute(
        """
        CREATE TABLE location_scores_window (
            location_id INTEGER NOT NULL,
            image_key TEXT NOT NULL,
            epoch INTEGER NOT NULL,
            score REAL NOT NULL,
            PRIMARY KEY (location_id, image_key, epoch)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO location_scores_window (location_id, image_key, epoch, score)
        SELECT location_id, '', epoch, score FROM location_scores
        """
    )
    connection.execute("DROP TABLE location_scores")
    connection.execute("ALTER TABLE location_scores_window RENAME TO location_scores")
