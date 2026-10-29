"""Environment switches for the XML-to-SQLite metadata port.

Every port switch is read here and documented in the package README.
The SQLite shadow write defaults to on (user decision 2026-10-29, metadata port stage 6):
every container save also updates ``VolumeData.db``, while XML stays the only store
anything reads. ``0``/``false``/``no``/``off`` turns it off. Every other switch defaults
to off and turns on with ``env_is_truthy`` (``1``/``true``/``yes``/``on``). Case and
surrounding whitespace are ignored, and flags are read on every call so a process (or
test) can switch one without reloading modules.
"""

import os

from nornir_imageregistration.env_flags import env_flag, env_is_truthy, is_falsey

SHADOW_SQLITE_ENV = 'NORNIR_VOLUME_METADATA_SHADOW_SQLITE'
READ_SQLITE_ENV = 'NORNIR_VOLUME_METADATA_READ_SQLITE'
TIMINGS_VOLUME_ENV = 'NORNIR_VOLUME_METADATA_TIMINGS_VOLUME_PATH'


def shadow_sqlite_enabled() -> bool:
    """Return True unless the shadow is switched off: each VolumeData.xml container save also updates
    the volume's SQLite shadow."""
    return not is_falsey(env_flag(SHADOW_SQLITE_ENV))


def read_sqlite_enabled() -> bool:
    """Return True when container loads should build their tree from the volume's SQLite database
    whenever its rows for the container match VolumeData.xml."""
    return env_is_truthy(READ_SQLITE_ENV)


def timings_volume_path() -> str:
    """Return ``NORNIR_VOLUME_METADATA_TIMINGS_VOLUME_PATH`` when set, else an empty string."""
    return os.environ.get(TIMINGS_VOLUME_ENV, '').strip()
