'''
Created on Dec 20, 2016

@author: u0490822
'''
import nornir_buildmanager.volumemanager
from nornir_buildmanager.exceptions import NornirUserException


def ListBlockStosMaps(BlockNode: nornir_buildmanager.volumemanager.BlockNode, **kwargs):
    print("")

    print('StosMaps in block {0:s}:'.format(BlockNode.Name))
    for map in BlockNode.StosMaps:
        print('    ' + map.Name)

    print("")

    return


def ListStosMap(BlockNode: nornir_buildmanager.volumemanager.BlockNode, StosMapName: str, ControlSection=None, **kwargs):
    StosMap = BlockNode.GetStosMap(StosMapName)

    print('')

    if StosMap is None:
        print("No stos map found with name {0}".format(StosMapName))
        return

    print("Stosmap Name:   {0:s}".format(StosMap.Name))
    print("Center Section: {0}".format(StosMap.CenterSection))
    print("{0:s}{1:s}".format('Control #'.ljust(10), "Mapped #'s"))

    mappings = StosMap.Mappings
    if ControlSection:
        mappings = [m for m in mappings if m.Control == ControlSection]

    for mapping in sorted(mappings, key=lambda m: m.Control or 0):
        print("{0:s}{1:s}".format(repr(mapping.Control).ljust(10),
                                  ', '.join([str(n) for n in sorted(mapping.Mapped)])))

    print('')
    return


def _require_stos_map(BlockNode: nornir_buildmanager.volumemanager.BlockNode,
                      StosMapName: str) -> nornir_buildmanager.volumemanager.StosMapNode:
    """The block's StosMap named *StosMapName*; an unknown name stops the pipeline (non-zero exit).

    RC1 (2026-11-22): AddToStosMap on a missing map printed a line and exited 0, so the next
    pipeline in the chain failed instead.
    """
    StosMap = BlockNode.GetStosMap(StosMapName)
    if StosMap is None:
        names = ', '.join(sorted(m.Name for m in BlockNode.StosMaps)) or 'none'
        raise NornirUserException(f"No stos map named {StosMapName} in block {BlockNode.Name} (maps: {names})")
    return StosMap


def AddMapping(BlockNode: nornir_buildmanager.volumemanager.BlockNode, StosMapName: str, ControlSection: int, MappedSection: int, **kwargs):
    StosMap = _require_stos_map(BlockNode, StosMapName)

    StosMap.AddMapping(ControlSection, MappedSection)

    ListStosMap(BlockNode, StosMapName, ControlSection)

    return BlockNode


def RemoveMapping(BlockNode: nornir_buildmanager.volumemanager.BlockNode, StosMapName: str, ControlSection: int, MappedSection: int, **kwargs):
    StosMap = _require_stos_map(BlockNode, StosMapName)

    removed = StosMap.RemoveMapping(ControlSection, MappedSection)

    ListStosMap(BlockNode, StosMapName, ControlSection)

    if removed:
        return BlockNode


def SetCenter(BlockNode: nornir_buildmanager.volumemanager.BlockNode, StosMapName: str, CenterSection: int):
    '''Sets the center section number for a StosMap'''
    StosMap = _require_stos_map(BlockNode, StosMapName)

    print("Stosmap Name:   {0:s}".format(StosMap.Name))
    print("Old Center: {0}".format(StosMap.CenterSection))
    StosMap.CenterSection = CenterSection
    print("New Center: {0}".format(StosMap.CenterSection))

    return BlockNode


if __name__ == '__main__':
    pass
