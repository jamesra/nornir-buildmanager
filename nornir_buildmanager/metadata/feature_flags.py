"""Environment switches for the XML-to-SQLite metadata port.

Every port switch is read here and documented in the package README.
Each defaults to off, which keeps XML as the only metadata store.
"""

import os

SHADOW_SQLITE_ENV = 'NORNIR_VOLUME_METADATA_SHADOW_SQLITE'
READ_SQLITE_ENV = 'NORNIR_VOLUME_METADATA_READ_SQLITE'
TIMINGS_VOLUME_ENV = 'NORNIR_VOLUME_METADATA_TIMINGS_VOLUME_PATH'

_TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})


def _enabled(name: str) -> bool:
    """Read on every call so a process (or test) can switch a flag without reloading modules."""
    return os.environ.get(name, '').strip().lower() in _TRUE_VALUES


def shadow_sqlite_enabled() -> bool:
    """Return True when each VolumeData.xml container save should also update the volume's SQLite shadow."""
    return _enabled(SHADOW_SQLITE_ENV)


def read_sqlite_enabled() -> bool:
    """Return True when container loads should build their tree from the volume's SQLite database
    whenever its rows for the container match VolumeData.xml."""
    return _enabled(READ_SQLITE_ENV)
