import sys
import os
import shutil
import glob
import pickle
from . import serialemlog
from nornir_buildmanager.VolumeManagerETree import DataNode
import nornir_shared.prettyoutput as prettyoutput

from nornir_shared import files



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

def _Update_path_on_rename(file_fullpath, new_section_dir):
    '''Return the correct paths if we move the directory a section lives in'''
    
    idocFilename = os.path.basename(file_fullpath)
    (ParentDir, sectionDir) = GetDirectories(file_fullpath)
    
    sectionDir = os.path.join(ParentDir, new_section_dir)
    file_fullpath = os.path.join(sectionDir, idocFilename)
    
    return file_fullpath

def GetDirectories(idocFileFullPath):
    '''
    :return: (ParentDir, SectionDir) The directory holding the section directory and the section directory in a tuple 
    '''
    sectionDir = os.path.dirname(idocFileFullPath)
    ParentDir = os.path.dirname(sectionDir)
    return (ParentDir, sectionDir)

def GetPathWithoutSpaces(idocFileFullPath):
    sectionDir = os.path.dirname(idocFileFullPath)
    fixed_sectionDir = try_remove_spaces_from_dirname(sectionDir)
    if fixed_sectionDir is None:
        return idocFileFullPath
    else:
        return _Update_path_on_rename(idocFileFullPath, fixed_sectionDir)


def TryAddLogs(containerObj, InputPath, logger):
    '''Copy log files to output directories, and store select meta-data in the containerObj if it exists'''
    LogsFiles = glob.glob(os.path.join(InputPath, '*.log'))
    LogsAdded = False
    if len(LogsFiles) == 0:
        print("NO LOG FILE FOUND FOR CAPTURE: " % InputPath)   
    elif len(LogsFiles) > 0:
        for filename in LogsFiles:

            NotesFilename = os.path.basename(filename)
            CopiedLogsFullPath = os.path.join(containerObj.FullPath, NotesFilename)
            if not os.path.exists(CopiedLogsFullPath):
                os.makedirs(containerObj.FullPath, exist_ok=True)
                
                shutil.copyfile(filename, CopiedLogsFullPath)
                LogsAdded = True

            # OK, try to parse the logs
            try:
                LogData = serialemlog.SerialEMLog.Load(filename)
                if LogData is None:
                    continue
                
                if LogData.NumTiles == 0:
                    continue

                # Create a Notes node to save the logs into
                LogNodeObj = DataNode.Create(Path=NotesFilename, attrib={'Name':'Log'})
                
                containerObj.RemoveOldChildrenByAttrib('Data', 'Name', 'Log')
                [added, LogNodeObj] = containerObj.UpdateOrAddChildByAttrib(LogNodeObj, 'Name')
                LogsAdded = LogsAdded or added
                LogNodeObj.AverageTileTime = '%g' % LogData.AverageTileTime
                LogNodeObj.AverageTileDrift = '%g' % LogData.AverageTileDrift
                LogNodeObj.CaptureTime = '%g' % (LogData.MontageEnd - LogData.MontageStart)
                
                LogNodeObj.HighMagCookDone = '%i' % (LogData.HighMagCookDone)
                LogNodeObj.LowMagCookDone = '%i' % (LogData.LowMagCookDone)
                LogNodeObj.StableFilamentChecked = '%i' % (LogData.StableFilamentChecked)
                LogNodeObj.ISCalibrationDone = '%i' % (LogData.ISCalibrationDone)

            except:
                (etype, evalue, etraceback) = sys.exc_info()
                prettyoutput.Log("Attempt to include logs from " + filename + " failed.\n" + str(evalue))
                prettyoutput.Log(str(etraceback))

    return LogsAdded

class OldVersionException(Exception):

    def __init__(self, text):
        self.text = text
        super(OldVersionException, self).__init__()
        
    def __str__(self):
        return repr(self.text)
 
 
def PickleLoad(logfullPath, version_func):
    '''
    :param version_func func: A callable function that raises an exception if the loaded object is the correct version or not.  Returning false deletes the cached .pickle file
    '''

    obj = None
    picklePath = logfullPath + ".pickle"

    files.RemoveOutdatedFile(logfullPath, picklePath)

    if os.path.exists(picklePath):
        try:
            with open(picklePath, 'rb') as filehandle:
                obj = pickle.load(filehandle)

                version_func(obj) #Should raise OldVersionException if the file is stale
                #if obj.__SerialEMLogVersion != SerialEMLog._SerialEMLog__ObjVersion():
                #    raise OldVersionException("Version mismatch in pickled file: " + picklePath)
        except OldVersionException as e:
            try:
                prettyoutput.Log("Removing stale .pickle file: " + str(e))
                os.remove(picklePath)
            except Exception:
                pass

            obj = None
        
        except Exception as e:
            try:
                prettyoutput.Log("Unexpected exception loading .pickle file: " + picklePath)
                prettyoutput.Log(str(e))
                os.remove(picklePath)
            except Exception:
                pass

            obj = None

    return obj


def PickleSave(obj, logfullPath):
    
    picklePath = logfullPath + ".pickle"
    
    try:
        with open(picklePath, 'wb') as filehandle:
            pickle.dump(obj, filehandle)
    except Exception as e:
        prettyoutput.LogErr(str.format("Could not cache {0}: {1}", picklePath, str(e) ) )
        try:
            os.remove(picklePath)
        except:
            pass