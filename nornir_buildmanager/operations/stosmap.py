'''
Created on Dec 20, 2016

@author: u0490822
'''
import nornir_buildmanager.volumemanager


def ListBlockStosMaps(BlockNode, **kwargs):
    print("")

    print('StosMaps in block {0:s}:'.format(BlockNode.Name))
    for map in BlockNode.StosMaps:
        print('    ' + map.Name)

    print("")

    return


def ListStosMap(BlockNode, StosMapName, ControlSection=None, **kwargs):
    StosMap = BlockNode.GetStosMap(StosMapName)

    print('')

    if StosMap is None:
        print("No stos map found with name {0}".format(StosMapName))
        return

    print("Stosmap Name:   {0:s}".format(StosMap.Name))
    print("Center Section: {0:d}".format(StosMap.CenterSection))
    print("{0:s}{1:s}".format('Control #'.ljust(10), "Mapped #'s"))

    mappings = StosMap.Mappings
    if ControlSection:
        mappings = [m for m in mappings if m.Control == ControlSection]

    for mapping in sorted(mappings, key=lambda m: m.Control):
        print("{0:s}{1:s}".format(repr(mapping.Control).ljust(10),
                                  ', '.join([str(n) for n in sorted(mapping.Mapped)])))

    print('')
    return


def AddMapping(BlockNode, StosMapName, ControlSection, MappedSection, **kwargs):
    StosMap = BlockNode.GetStosMap(StosMapName)

    if StosMap is None:
        print("No stos map found with name {0}".format(StosMapName))
        return

    StosMap.AddMapping(ControlSection, MappedSection)

    ListStosMap(BlockNode, StosMapName, ControlSection)

    return BlockNode


def RemoveMapping(BlockNode, StosMapName, ControlSection, MappedSection, **kwargs):
    StosMap = BlockNode.GetStosMap(StosMapName)
    if StosMap is None:
        print("No stos map found with name {0}".format(StosMapName))
        return

    removed = StosMap.RemoveMapping(ControlSection, MappedSection)

    ListStosMap(BlockNode, StosMapName, ControlSection)

    if removed:
        return BlockNode


def SetCenter(BlockNode: nornir_buildmanager.volumemanager.BlockNode, StosMapName: str, CenterSection: int):
    '''Sets the center section number for a StosMap'''
    StosMap = BlockNode.GetStosMap(StosMapName)
    if StosMap is None:
        print("No stos map found with name {0}".format(StosMapName))
        return

    print("Stosmap Name:   {0:s}".format(StosMap.Name))
    print("Old Center: {0:d}".format(StosMap.CenterSection))
    StosMap.CenterSection = CenterSection
    print("New Center: {0:d}".format(StosMap.CenterSection))

    return BlockNode


if __name__ == '__main__':
    pass
