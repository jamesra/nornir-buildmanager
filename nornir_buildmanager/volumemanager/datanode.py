from __future__ import annotations

import nornir_buildmanager.volumemanager as volumemanager

class DataNode(volumemanager.XFileElementWrapper):
    """Refers to an external file containing data"""

    @classmethod
    def Create(cls, Path: str, attrib: dict = None, **extra) -> DataNode:
        return cls(tag='Data', Path=Path, attrib=attrib, **extra)
