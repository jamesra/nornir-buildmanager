from typing import Generator, Iterable
import re
import nornir_buildmanager
import xml.etree

from nornir_buildmanager.volumemanager import XElementWrapper

def SearchCollection(Objects: Iterable[XElementWrapper | xml.etree.ElementTree.Element], AttribName: str, RegExStr: str, CaseSensitive: bool = False) -> Generator[XElementWrapper, None, None]:
    """
    Search a list of object's attributes using a regular express.
    Returns a generator of objects with matching attributes.
    Returns all entries if RegExStr is None
    """

    if RegExStr is None:
        yield from Objects

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
            #Matches.append(MatchObj)
            yield MatchObj
            continue

        match = re.match(RegExStr, attrib, flags)
        if match is not None:
            (wrapped, MatchObj) = nornir_buildmanager.volumemanager.WrapElement(MatchObj)
            assert (wrapped is False)
            yield MatchObj
            #Matches.append(MatchObj)

    #return Matches
    return
