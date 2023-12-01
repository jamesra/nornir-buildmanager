'''
Created on May 15, 2015

@author: u0490822

All the code, often throwaway, to migrate from one version to another.
'''

import datetime
import glob
import os
import re

import nornir_buildmanager.volumemanager.inputtransformhandler
import nornir_imageregistration
import nornir_imageregistration.files
import nornir_shared.files


def GetTileNumber(filename):
    (tile_number_str, ext) = os.path.splitext(os.path.basename(filename))
    return int(tile_number_str)


def GetTileFormatString(tilefilename):
    basename = os.path.basename(tilefilename)
    (basename, extension) = os.path.splitext(basename)

    min_digits = len(basename)
    file_format = '%' + '0%dd%s' % (min_digits, extension)
    return file_format


def GetListOfTileNumbers(tile_filenames):
    tile_number_list = []
    for tile_filename in tile_filenames:
        tile_number = GetTileNumber(tile_filename)
        tile_number_list.append(tile_number)

    tile_number_list.sort()
    return tile_number_list


# -------------------------------------
# Begin Fix transform numbering section

def RenumberTransformTilesToStartAtZero(transform_node, **kwargs):
    '''
    
    '''
    (junk, ext) = os.path.splitext(transform_node.FullPath)
    if ext != '.mosaic':
        return

    mFile = nornir_imageregistration.files.MosaicFile.Load(transform_node.FullPath)
    sorted_keys = sorted(mFile.ImageToTransformString.keys())

    firstTileNumber = GetTileNumber(sorted_keys[0])
    # We only make the first number zero if the the first tile is 1
    if firstTileNumber != 1:
        return

    tile_format = GetTileFormatString(sorted_keys[0])

    tile_number_list = GetListOfTileNumbers(sorted_keys)

    for tile_number in tile_number_list:
        original_key = tile_format % tile_number
        new_tile_name = tile_format % (tile_number - 1)

        transform = mFile.ImageToTransformString[original_key]
        del mFile.ImageToTransformString[original_key]
        if new_tile_name in mFile.ImageToTransformString:
            print("Tile %s already exists in mosaic %s" % (new_tile_name, transform_node.FullPath))

        mFile.ImageToTransformString[new_tile_name] = transform

    mFile.Save(transform_node.FullPath)
    print("Updated tile numbering for %s" % transform_node.FullPath)

    # return transform_node.Parent


# End fix transform numbering section
# -----------------------------------

# -------------------------------------------------------
# Begin Fix tile numbering section

def FixFileNumbering(files_list):
    '''
    Ensure filenames start at zero
    '''

    sorted_files = sorted(files_list)
    tile_number = GetTileNumber(sorted_files[0])
    if tile_number == 0:
        return

    dirname = os.path.dirname(sorted_files[0])
    file_format = GetTileFormatString(sorted_files[0])

    tile_number_list = GetListOfTileNumbers(sorted_files)

    # OK, we need to decrement every tile number by one.
    First = True
    for tile_number in tile_number_list:
        InputFilename = os.path.join(dirname, file_format % tile_number)
        adjusted_tile_number = tile_number - 1

        OutputFilename = os.path.join(dirname, file_format % adjusted_tile_number)
        if First:
            print("%s -> %s" % (InputFilename, OutputFilename))
            First = False

        if os.path.exists(OutputFilename):
            print("Cannot rename files %s -> %s" % (InputFilename, OutputFilename))
            return
        else:
            os.rename(InputFilename, OutputFilename)


def MoveTilesToStartAtZero(tile_pyramid_node, **kwargs):
    for level_node in tile_pyramid_node.Levels:
        tile_list = glob.glob(os.path.join(level_node.FullPath, '*' + tile_pyramid_node.ImageFormatExt))

        FixFileNumbering(tile_list)


# End Fix tile numbering section
# -------------------------------------------------------


def _MapTransformToCurrentType(transform_node, name, old_type, new_type):
    '''Rename a transform if it matches the name parameter.  Adjust both the path and type attributes'''

    if transform_node.Name != name:
        return False

    if transform_node.Type != old_type:
        return False

    (filename, ext) = os.path.splitext(transform_node.Path)
    transform_node.Type = new_type
    new_path = transform_node.Name + new_type + ext

    if os.path.exists(transform_node.FullPath):
        output_transform_path = os.path.join(transform_node.Parent.FullPath, new_path)
        if os.path.exists(output_transform_path):
            os.remove(transform_node.FullPath)
        else:
            os.rename(transform_node.FullPath, os.path.join(transform_node.Parent.FullPath, new_path))
            transform_node.Path = new_path

    return True


def MigrateChannel_1p2_to_1p3(channel_node, **kwargs):
    for transform_node in channel_node.findall('Transform'):
        MigrateTransforms_1p2_to_1p3(transform_node)

    print("Saving %s" % channel_node.Parent.FullPath)
    return channel_node


def MigrateTransforms_1p2_to_1p3(transform_node, **kwargs):
    '''Update the checksums to use the new algorithm.  Then rename grid transforms to use the sorted type name'''

    if not os.path.exists(transform_node.FullPath):
        transform_node.Clean()
        return transform_node.Parent

    original_checksum = transform_node.Checksum
    original_type = transform_node.Type

    transform_node.ResetChecksum()
    transform_node.Locked = True

    _MapTransformToCurrentType(transform_node, name='Grid', old_type='_Cel128_Mes8_sp4_Mes8_Thr0.25',
                               new_type='_Cel128_Mes8_Mes8_Thr0.25_it10_sp4')
    _MapTransformToCurrentType(transform_node, name='Grid', old_type='_Cel96_Mes8_sp4_Mes8_Thr0.5',
                               new_type='_Cel128_Mes8_Mes8_Thr0.25_it10_sp4')

    # All done changing the transforms meta-data.  Now update transforms which depend on us with correct information
    for dependent in nornir_buildmanager.volumemanager.inputtransformhandler.InputTransformHandler.EnumerateTransformDependents(
            transform_node.Parent, original_checksum, original_type, recursive=True):
        if dependent.HasInputTransform:
            dependent.SetTransform(transform_node)

    return transform_node.Parent


#-------------------------------------------------------------------

def reverse_angle_for_rigid_transforms(filename: str):
    """"If the transform is a rigid transform, then we need to invert the angle if it is older than 11/28/2023"""

    cutoff_date = datetime.datetime(2023, 11, 28)
    cutoff_value = cutoff_date.timestamp()

    stos_stats = os.stat(filename)
    if stos_stats.st_mtime >= cutoff_value: #Check if the file has been modified after the cutoff date
        return False, None

    obj = nornir_imageregistration.files.StosFile.Load(filename)
    transform = nornir_imageregistration.transforms.LoadTransform(obj.Transform, pixelSpacing=1)
    #This patch only applies to rigid transforms
    if not isinstance(transform, nornir_imageregistration.transforms.Rigid):
        return False, None

    if isinstance(transform, nornir_imageregistration.transforms.CenteredSimilarity2DTransform):
        updated_transform = nornir_imageregistration.transforms.CenteredSimilarity2DTransform(target_offset=transform.target_offset,
                                                                                 source_rotation_center=transform.source_space_center_of_rotation,
                                                                                 angle=-transform.angle,
                                                                                 scalar=transform.scalar,
                                                                                 flip_ud=transform.flip_ud)
    elif isinstance(transform, nornir_imageregistration.transforms.Rigid):
        updated_transform = nornir_imageregistration.transforms.Rigid(target_offset=transform.target_offset,
                                                         source_rotation_center=transform.source_space_center_of_rotation,
                                                         angle=-transform.angle,
                                                         flip_ud=transform.flip_ud)
    else:
        raise NotImplementedError("Unknown transform type")

    obj.Transform = updated_transform
    obj.Save(filename) #Replace the file we just loaded
    return True, updated_transform

def ReverseRigidTransformAngles(transform_node: nornir_buildmanager.volumemanager.TransformNode, **kwargs):
    """
    Negate the angle value stored in .stos files for .stos files made before 11/28/2023.
    Then update InputTransformChecksum for any dependent transforms
    :param transform_node:
    :param kwargs:
    :return:
    """
    original_checksum = transform_node.Checksum

    try:
        updated, updated_transform = reverse_angle_for_rigid_transforms(transform_node.FullPath)
    except FileNotFoundError:
        transform_node.Clean("File not found")
        return transform_node.Parent
        pass

    if not updated:
        return None

    if updated:
        transform_node.ResetChecksum()

    # All done changing the transforms meta-data.  Now update transforms which depend on us with correct information
    block_node = transform_node.FindParent('Block')
    if block_node is None:
        raise ValueError("Transform node %s is not in a block" % transform_node.FullPath)

    save_block = False
    for dependent in nornir_buildmanager.volumemanager.inputtransformhandler.InputTransformHandler.EnumerateTransformDependents(
            block_node, original_checksum, transform_node.Type, recursive=False, child_element_name='StosGroup/SectionMappings/Transform'):
        if dependent.HasInputTransform and dependent.InputTransformChecksum == original_checksum:
            dependent.SetTransform(transform_node)
            save_block = True
            
    if save_block:
        return block_node
    else:
        return transform_node.Parent


    #-----------------------------------------------------------------------------

def RepairCroppedXMLFilesInElement(volume_element: nornir_buildmanager.volumemanager.VolumeNode, **kwargs):
    for child_folder, matches in nornir_shared.files.RecurseSubdirectoriesGenerator(volume_element.FullPath,
                                                                                    ExcludedFiles=[],
                                                                                    ExcludeNames=nornir_shared.files.DefaultLevelStrings):
        print(child_folder)
        TryRepairXMLFileAppendError(os.path.join(child_folder, "VolumeData.xml"))

_XMLHeadTagParser = re.compile(r'''
                                   (<[?].+[?]>)? # Ignore standard header if it exists 
                                   \s*<\s* #Ignore whitespace and opening
                                   (?P<Tag>[a-zA-Z0-9]+) # Tag name
                                   \s* # Optional whitespace
                                   (?P<Attributes>[a-zA-Z0-9]+\s*=\s*".*"\s*)? 
                                   (/>|>)  # closing
                                   (?P<Remaining>.+)? # Remaining text
                                   ''', re.VERBOSE)

def TryRepairXMLFileAppendError(filename: str):
    """Attempt to repair an XML file that has excess text after the closing element"""

    try:
        with open(filename, 'r') as f:
            data = f.read()
            f.close()

        match = _XMLHeadTagParser.match(data)
        if match is None:
            print(f'File {filename} does not appear to be an XML file')
            return False

        tag_name = match['Tag']
        #Find where the end tag is
        end_tag = '</' + tag_name + '>'
        end_tag_index = data.find(end_tag)
        if end_tag_index < 0:
            print(f'File {filename} does not appear to have an endtag')
            return False

        #Find where the remaining text is
        expected_length = end_tag_index + len(end_tag)
        if expected_length == len(data):
            return False #No data to crop
        
        remaining_text = data[expected_length:]

        #trim the excess text after the endtag
        fixed_data = data[0:expected_length]
        
        name, ext = os.path.splitext(filename)
        backup_filename = name + '.bak'
        with open(backup_filename, 'w') as f:
            f.write(data)
            f.close()

        with open(filename, 'w') as f:
            f.write(fixed_data)
            f.close()

        print(f'Removed excess text in {filename}: {remaining_text}')
        return True

    except FileNotFoundError:
        return False


