import os

import nornir_buildmanager.volumemanager
from nornir_buildmanager.volumemanager import XElementWrapper
import nornir_shared.reflection as reflection


def SetElementParent(Element: XElementWrapper, ParentElement: XElementWrapper = None):
    """
    An internal use only helper method that sets change flags on the parent element
    and updates the parent child relationship of the elements involved
    :param Element:
    :param ParentElement:
    :return:
    """
    Element.SetParentNoChangeFlag(ParentElement)

    for i in range(len(Element) - 1, -1, -1):
        e = Element[i]
        if e.tag in nornir_buildmanager.volumemanager.versions.DeprecatedNodes:
            del Element[i]

    for i in range(0, len(Element)):
        e = Element[i]
        if isinstance(e, nornir_buildmanager.volumemanager.XElementWrapper):
            # Find out if we have an override class defined
            e.OnParentChanged()

    return


def WrapElement(e) -> XElementWrapper:
    """
    Returns a new class that represents the passed XML element
    :param ElementTree.Element e: Element to be represented by a class
    :return: (bool, An object inheriting from XElementWrapper) Returns true if the element had to be wrapped
    """

    assert (e.tag.endswith('_Link') is False), "Cannot wrap a link element that has not been loaded"

    OverrideClassName = e.tag + 'Node'
    OverrideClass = reflection.get_module_class('nornir_buildmanager.volumemanager', OverrideClassName,
                                                LogErrIfNotFound=False)

    if OverrideClass is None:
        if "Path" in e.attrib:
            if os.path.isfile(e.attrib.get("Path")):
                # TODO: Do we ever hit this path and do we need to make the os.path.isfile check anymore?
                OverrideClass = nornir_buildmanager.volumemanager.XFileElementWrapper
            else:
                OverrideClass = nornir_buildmanager.volumemanager.XContainerElementWrapper
        else:
            OverrideClass = nornir_buildmanager.volumemanager.XElementWrapper

    if isinstance(e, OverrideClass):
        return False, e
    else:
        return True, OverrideClass.wrap(e)
