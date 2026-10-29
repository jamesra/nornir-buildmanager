"""Environment switches for the XML-to-SQLite metadata port.

Every port switch is read here and documented in the package README.
Each defaults to off, which keeps XML as the only metadata store.
On/off switches use ``env_is_truthy`` (``1``/``true``/``yes``/``on``, case and
surrounding whitespace ignored) and are read on every call so a process (or test)
can switch a flag without reloading modules.
"""

import os

from nornir_imageregistration.env_flags import env_is_truthy

SHADOW_SQLITE_ENV = 'NORNIR_VOLUME_METADATA_SHADOW_SQLITE'
READ_SQLITE_ENV = 'NORNIR_VOLUME_METADATA_READ_SQLITE'
TIMINGS_VOLUME_ENV = 'NORNIR_VOLUME_METADATA_TIMINGS_VOLUME_PATH'


def shadow_sqlite_enabled() -> bool:
    """Return True when each VolumeData.xml container save should also update the volume's SQLite shadow."""
    return env_is_truthy(SHADOW_SQLITE_ENV)


def read_sqlite_enabled() -> bool:
    """Return True when container loads should build their tree from the volume's SQLite database
    whenever its rows for the container match VolumeData.xml."""
    return env_is_truthy(READ_SQLITE_ENV)


def timings_volume_path() -> str:
    """Return ``NORNIR_VOLUME_METADATA_TIMINGS_VOLUME_PATH`` when set, else an empty string."""
    return os.environ.get(TIMINGS_VOLUME_ENV, '').strip()
