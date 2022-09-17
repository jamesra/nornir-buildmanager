import re
import nornir_buildmanager

from . import xelementwrapper

def SearchCollection(Objects, AttribName: str, RegExStr: str, CaseSensitive: bool = False) -> [
    xelementwrapper.XElementWrapper]:
    """Search a list of object's attributes using a regular express.
       Returns list of objects with matching attributes.
       Returns all entries if RegExStr is None"""

    if RegExStr is None:
        return Objects

    Matches = []

    flags = 0
    if not CaseSensitive:
        flags = re.IGNORECASE

    for MatchObj in Objects:
        if not hasattr(MatchObj, AttribName):
            continue

        attrib = MatchObj.attrib.get(AttribName, None)
        if attrib is None:
            continue

        if RegExStr == '*':
            Matches.append(MatchObj)
            continue

        match = re.match(RegExStr, attrib, flags)
        if match is not None:
            (wrapped, MatchObj) = nornir_buildmanager.volumemanager.WrapElement(MatchObj)
            assert (wrapped is False)
            Matches.append(MatchObj)

    return Matches
