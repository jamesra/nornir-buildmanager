import os
import shutil


def try_remove_spaces_from_dirname(sectionDir):
    ''':return: Renamed directory if there were spaced in the filename, otherwise none'''
    sectionDirNoSpaces = sectionDir.replace(' ', '_')
    ParentDir = os.path.dirname(sectionDir)
    if(sectionDirNoSpaces != sectionDir):
        sectionDirNoSpacesFullPath = os.path.join(ParentDir, sectionDirNoSpaces)
        shutil.move(sectionDir, sectionDirNoSpacesFullPath)

        sectionDir = sectionDirNoSpaces
        return sectionDir 
    
    return None

def _Update_idoc_path_on_rename(idocFileFullPath, new_section_dir):
    '''Return the correct paths if we move the directory a section lives in'''
    
    idocFilename = os.path.basename(idocFileFullPath)
    (ParentDir, sectionDir) = GetDirectories(idocFileFullPath)
    
    sectionDir = os.path.join(ParentDir, new_section_dir)
    idocFileFullPath = os.path.join(sectionDir, idocFilename)
    
    return idocFileFullPath

def GetDirectories(idocFileFullPath):
    '''
    :return: (ParentDir, SectionDir) The directory holding the section directory and the section directory in a tuple 
    '''
    sectionDir = os.path.dirname(idocFileFullPath)
    ParentDir = os.path.dirname(sectionDir)
    return (ParentDir, sectionDir)

def GetIDocPathWithoutSpaces(idocFileFullPath):
    sectionDir = os.path.dirname(idocFileFullPath)
    fixed_sectionDir = try_remove_spaces_from_dirname(sectionDir)
    if fixed_sectionDir is None:
        return idocFileFullPath
    else:
        return _Update_idoc_path_on_rename(idocFileFullPath, fixed_sectionDir)

def GetSectionInfo(fileName):
    fileName = os.path.basename(fileName)

    # Make sure extension is present in the filename
    [fileName, ext] = os.path.splitext(fileName)

    SectionNumber = -1
    Downsample = 1
    parts = fileName.split("_")
    try:
        SectionNumber = int(parts[0])
    except:
        # We really can't recover from this, so maybe an exception should be thrown instead
        SectionNumber = -1

    try:
        SectionName = parts[1]
    except:
        SectionName = str(SectionNumber)

    # If we don't have a valid downsample value we assume 1
    try:
        DownsampleStrings = parts[2].split(".")
        Downsample = int(DownsampleStrings[0])
    except:
        Downsample = 1

    return [SectionNumber, SectionName, Downsample]