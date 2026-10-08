"""Environment switches for the XML-to-SQLite metadata port.

Every port switch is read here and documented in the package README.
Each defaults to off, which keeps XML as the only metadata store.
"""

import os

SHADOW_SQLITE_ENV = 'NORNIR_VOLUME_METADATA_SHADOW_SQLITE'

_TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})


def shadow_sqlite_enabled() -> bool:
    """Return True when each VolumeData.xml container save should also update the volume's SQLite shadow.

    Read on every call so a process (or test) can switch it without reloading modules.
    """
    return os.environ.get(SHADOW_SQLITE_ENV, '').strip().lower() in _TRUE_VALUES
