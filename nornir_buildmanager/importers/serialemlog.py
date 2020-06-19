'''
Created on Apr 11, 2019

@author: u0490822

Parses SerialEM log files and plots drift and settle times
'''

import sys
import pickle
import os
import datetime
import nornir_shared.files as files
import nornir_shared.plot as plot
import nornir_shared.prettyoutput as prettyoutput 

from nornir_buildmanager.importers import serialem_utils
import nornir_buildmanager


class LogTileData(object):
    '''Data for each individual tile in a capture'''
    
    @property
    def totalTime(self):
        '''Total time required to capture the file including autofocus'''
        if self.endAcquisitionTime is None:
            return None

        return self.endAcquisitionTime - self.startAcquisitionTime

    @property
    def acquisitionTime(self):
        '''Total time required to capture the file not including autofocus, based on interval between DoNextPiece calls in the log.
           a.k.a, The time it takes to record a tile after it is in focus and drift tolerance is met
        '''
        if self.endAcquisitionTime is None:
            return None

        return self.totalTime - self.settleTime
   
    @property
    def dwellTime(self):
        '''Total time from the stage stopping to the tile being acquired'''
        if self.stageStopTime is None:
            return None

        if self.endAcquisitionTime is None:
            return None

        return self.endAcquisitionTime - self.stageStopTime

    @property
    def settleTime(self):
        '''Total time required to for the first drift measurement below threshold after the stage stopped moving'''
        if len(self.driftStamps) == 0:
            return None

        if self.endAcquisitionTime is None:
            return None

        return self.driftStamps[-1][0]

    @property
    def drift(self):

        if len(self.driftStamps) > 0:
            stamp = self.driftStamps[-1]
            return stamp[1]

        return None

    def __str__(self):
        text = ""
        if not self.number is None:
            text = str(self.number) + ": "
        if not self.acquisitionTime is None:
            text = text + "%.1f" % self.acquisitionTime
        if len(self.driftStamps) > 0:
            text = text + " %.2f " % self.drift
            if not self.driftUnits is None:
                text = text + " " + self.driftUnits

        return text

    def __init__(self, startDoNextPieceStageMove):
        self.startAcquisitionTime = startDoNextPieceStageMove  # Time when DoNextPiece Starting Capture was logged to begin this tile
        self.endAcquisitionTime = None  # Time when DoNextPiece Starting stage move was logged to start the next tile

        self.stageStopTime = None  # Time when the stage stopped moving

        self.driftStamps = []  # Contains tuples of time after stage move completion, measured drift, and measured defocus
        self.number = None  # The tile number in the capture
        self.driftUnits = None  # nm/sec
        self.defocusUnits = None  # microns
        self.coordinates = None


class SerialEMLog(object):

    @staticmethod
    def __ObjVersion():
        '''Used for knowing when to ignore a pickled file'''

        # Version 2 fixes missing tile drift times for the first tile in a hemisphere
        return 6

    @property
    def TotalTime(self):
        '''Total time to capture, including cooking, filaments warmup, acquisition, etc...'''
        return self.TotalTileAcquisitionTime + self.LowMagCookTime + self.HighMagCookTime + self.FilamentStabilizationTime
        

    @property
    def AverageTileTime(self):
        '''
        Average total time spent capturing each tile
        '''
        if len(self.tileData) == 0:
            return None
        
        if self._avg_tile_time is None:
            TotalTimes = [t.totalTime for t in self.tileData.values()]
            total = sum(TotalTimes)
            self._avg_tile_time = total / len(self.tileData)  
            
        return self._avg_tile_time
     
    @property
    def AverageSettleTime(self):
        '''
        The average time it takes for a tile to be in-focus and below drift tolerance after a stage move
        '''
        if len(self.tileData) == 0:
            return None
        
        if self._avg_tile_settle_time is None:
            TotalTimes = [t.settleTime for t in self.tileData.values()]
            total = sum(TotalTimes)
            self._avg_tile_settle_time = total / len(self.tileData)  
            
        return self._avg_tile_settle_time
    
    @property
    def AverageAcquisitionTime(self):
        '''
        The average time it takes to record a tile after it is in focus and drift tolerance is met
        '''
        if len(self.tileData) == 0:
            return None
        
        if self._avg_tile_acquisitionTime_time is None:
            TotalTimes = [t.acquisitionTime for t in self.tileData.values()]
            total = sum(TotalTimes)
            self._avg_tile_acquisitionTime_time = total / len(self.tileData)  
            
        return self._avg_tile_acquisitionTime_time

    @property
    def AverageTileDrift(self):
        if len(self.tileData) == 0:
            return None
        
        if self._avg_tile_drift is None:
            drift_time = [t.drift for t in self.tileData.values() if t is not None]
            self._avg_tile_drift = sum(drift_time) / len(drift_time)
    
        return self._avg_tile_drift

    @property
    def FastestTileTime(self):
        '''Shortest time to capture a tile in seconds'''

        if self._fastestTime is None:
            for t in list(self.tileData.values()):
                if not (t.dwellTime is None or t.drift is None):
                    if self._fastestTime is None:
                        self._fastestTime = t.totalTime
                    else:
                        self._fastestTime = min(self._fastestTime, t.totalTime)

        return self._fastestTime
    
    @property
    def FastestSettleTime(self):
        '''Fastest time to be in focus and under drift limit after stage move'''

        if self._fastest_settle_time is None:
            for t in list(self.tileData.values()):
                if not (t.dwellTime is None or t.drift is None):
                    if self._fastest_settle_time is None:
                        self._fastest_settle_time = t.settleTime
                    else:
                        self._fastest_settle_time = min(self._fastest_settle_time, t.settleTime)

        return self._fastest_settle_time
     
    
    @property
    def FastestAcquisitionTime(self):
        '''Fastest time to capture a tile after it is in focus and under drift limit'''

        if self._fastest_acquisition_time is None:
            for t in list(self.tileData.values()):
                if not (t.dwellTime is None or t.drift is None):
                    if self._fastest_acquisition_time is None:
                        self._fastest_acquisition_time = t.acquisitionTime
                    else:
                        self._fastest_acquisition_time = min(self._fastest_acquisition_time, t.acquisitionTime)

        return self._fastest_acquisition_time
     

    @property
    def MaxTileDrift(self):
        '''Largest drift for a tile in seconds'''
        if self._maxdrift is None:
            self._maxdrift = 0
            for t in list(self.tileData.values()):
                if not (t.dwellTime is None or t.drift is None):
                    self._maxdrift = max(self._maxdrift, t.driftStamps[-1][1])

        return self._maxdrift

    @property
    def MinTileDrift(self):
        '''Largest drift for a tile in seconds'''
        if self._mindrift is None:
            self._mindrift = self.MaxTileDrift + 1
            for t in list(self.tileData.values()):
                if not (t.dwellTime is None or t.drift is None):
                    self._mindrift = min(self._mindrift, t.driftStamps[-1][1])

        return self._mindrift

    @property
    def NumTiles(self): 
        if self._num_tiles is None: 
            IsTile = [not (t.dwellTime is None or t.drift is None) for t in self.tileData.values()]
            self._num_tiles = sum(IsTile)
        
        return self._num_tiles
    
    @property
    def FilamentStabilizationTime(self):
        '''
        :return: # of seconds to stabilize the filament or None if stabilization
        did not occur
        '''
        return self._FilamentStabilizationTime
    
    @property
    def LowMagCookTime(self):
        '''
        :return: # of seconds to stabilize the filament or None if stabilization
        did not occur
        '''
        return self._LowMagCookTime
    
    @property
    def HighMagCookTime(self):
        '''
        :return: # of seconds to stabilize the filament or None if stabilization
        did not occur
        '''
        return self._HighMagCookTime
    
    @property
    def CaptureSetupTime(self):
        '''
        The time from startup to montage start, minus all other known macro activities such as cooking and filament warmup.
        '''
        
        return (self.MontageStart - self.StartupTimeStamp) - (self.LowMagCookTime + self.HighMagCookTime + self.FilamentStabilizationTime)
    
    @property
    def TotalTileAcquisitionTime(self):
        '''Total time it took to acquire tiles after all preparation steps such as cooking'''
        return self.MontageEnd - self.MontageStart
            
    @property
    def ISCalibrationDone(self):
        return self._IS_change_percentage is not None
    
    @property
    def IS_mean_correlation(self):
        return self._IS_mean_cc
    
    @property
    def IS_min_correlation(self):
        return self._IS_min_cc
    
    @property
    def HighMagCookDone(self):
        return self._HighMagCookTime > 0
    
    @property
    def LowMagCookDone(self):
        return self._LowMagCookTime > 0
    
    @property
    def StableFilamentChecked(self):
        return self._FilamentStabilizationTime > 0

    @property
    def StartupDateTime(self):
        '''The datetime string from the "Started  6/11/2020  11:05:54" entry in the log, if it exists'''
        return datetime.datetime.strptime(self._startup, '%m/%d/%Y %H:%M:%S') 
    
    @property
    def StartupTimeStamp(self):
        '''The timestamp in seconds for the "1.938: Microscope Startup succeeded" entry.  Defaults to 0'''
        if self._startup_timestamp is not None:
            return self._startup_timestamp
        return 0

    @property
    def Version(self):
        return self._version

    def __init__(self):
        self.tileData = {}  # The time required to capture each tile
        self._startup = None  # SerialEM program Startup time string, if known
        self._startup_timestamp = 0 #SerialEM 'microscope startup' timestamp, if known
        self._version = None  # SerialEM version, if known
        self.PropertiesVersion = None  # Timestamp of properties file, if known
        self.MontageStart = None  # timestamp when acquire began
        self.MontageEnd = None  # timestamp when acquire ended
          
        # Cached calculations
        self._avg_tile_time = None
        self._avg_tile_settle_time = None
        self._avg_tile_drift = None
        self._avg_tile_acquisitionTime_time = None
        self._num_tiles = None
        self._fastestTime = None
        self._fastest_acquisition_time = None
        self._fastest_settle_time = None
        self._maxdrift = None
        self._mindrift = None
        
        self._IS_change_percentage = None
        self._IS_mean_cc = None
        self._IS_min_cc = None
        
        self._LowMagCookTime = 0
        self._HighMagCookTime = 0
        self._FilamentStabilizationTime = 0
           
        self.__SerialEMLogVersion = SerialEMLog._SerialEMLog__ObjVersion()
        
    @classmethod
    def VersionCheck(cls, loaded):
        if loaded.__SerialEMLogVersion != cls._SerialEMLog__ObjVersion():
            raise nornir_buildmanager.importers.OldVersionException("Loaded version %d expected version %d" % (loaded.__SerialEMLogVersion, SerialEMLog._SerialEMLog__ObjVersion()))
        
        return

    @classmethod
    def __PickleLoad(cls, logfullPath):

        obj = None
        picklePath = logfullPath + ".pickle"

        files.RemoveOutdatedFile(logfullPath, picklePath)

        if os.path.exists(picklePath):
            try:
                with open(picklePath, 'r') as filehandle:
                    obj = pickle.load(filehandle)

                    if obj.__SerialEMLogVersion != SerialEMLog._SerialEMLog__ObjVersion():
                        raise Exception("Version mismatch in pickled file: " + picklePath)
            except Exception:
                try:
                    os.remove(picklePath)
                except Exception:
                    pass

                obj = None

        return obj

    def __PickleSave(self, logfullPath):
 
        picklePath = logfullPath + ".pickle"

        try:
            with open(picklePath, 'w') as filehandle:
                pickle.dump(self, filehandle)
        except:
            try:
                os.remove(picklePath)
            except:
                pass
            
    @staticmethod
    def ReadLine(hLog):
        '''
        Fetch the next line with information from the log
        ''' 
        
        line = hLog.readline(512)
        if line is None or len(line) == 0:
            return (None, None, None)  # No more lines in file
        
        entry = None
        timestamp = None
        
        while entry is None:
            
            (timestamp, entry) = SerialEMLog.TryParseLine(line)        

            if entry is None:
                line = hLog.readline(512)
                if line is None or len(line) == 0:
                    return (None, None, None)  # No more lines in file
                
                continue

            if line[0].isdigit():
                try:
                    (timestamp, entry) = line.split(':', 1)
                    timestamp = float(timestamp)
                except ValueError:
                    pass
            
            entry = entry.strip()
        
            return (line, timestamp, entry)
    
    @staticmethod
    def TryParseLine(line):
        
        line = line.strip()
        
        if len(line) == 0:
            return (None, None)
        
        entry = line
        timestamp = None
        
        if line[0].isdigit():
            try:
                (timestamp, entry) = line.split(':', 1)
                timestamp = float(timestamp)
            except ValueError:
                pass
            
        entry = entry.strip()
        
        return (timestamp, entry)
                
    @classmethod
    def Load(cls, logfullPath, usecache=True):
        '''Parses a SerialEM log file and extracts as much information as possible:
        '''

        # These are some samples of interesting lines this function looks for
        # Last update properties file: Sep 30, 2011
        # SerialEM Version 3.1.1a,  built Nov  9 2011  14:20:16
        # Started  4/23/2012  12:17:25
        # 2912.015: Montage Start
        # 2969.640: DoNextPiece Starting capture with stage move
        # 2980.703: BlankerProc finished stage move
        # 2984.078: SaveImage Saving image at 4 4,  file Z = 0
        # 2985.547: SaveImage Processing
        # 31197.078: Montage Done processing

        # 4839.203: Autofocus Start
        # Measured defocus = -0.80 microns                  drift = 1.57 nm/sec
        # 4849.797: Autofocus Done

        # Captures in SerialEM overlap.  Once the stage is in position the exposure is done,
        # then simultaneously the stage moves while the camera is read.  Generally the stage
        # finishes movement before the image is saved, but we should not count on this behaviour

        # Parsing these logs takes quite a while sometimes

        if usecache:
            obj = serialem_utils.PickleLoad(logfullPath, SerialEMLog.VersionCheck)

            if not obj is None:
                return obj

        Data = SerialEMLog()
        NextTile = None  # The tile we are currently moving the stage, focusing on, and setting up an aquisition for.
        AcquiredTile = None  # The tile which we have an image for, but has not been read from the CCD and saved to disk yet
        nextLine = None  # The next line in the file, if we need to get it
        line = ""  # Set a not None value
        entry = None  # The data portion of the log entry
        timestamp = None  # The timestamp of the log entry
        MontageStart = None
        
        LegacyFilamentWarmupStartTime = None
        
        LastAutofocusStart = None
        LastValidTimestamp = None  # Used in case the log ends abruptly to populate MontageEnd value
        
        LastElapsedTimeReport = None #The value of the previous "1715.55 seconds elapsed time" row
        
        with open(logfullPath, 'r') as hLog:
            while True:
                if nextLine is None:
                    (line, timestamp, entry) = SerialEMLog.ReadLine(hLog)
                    
                else:
                    (timestamp, entry) = SerialEMLog.TryParseLine(nextLine)  # Move the line we had to load into the current line
                    line = nextLine
                    nextLine = None
                    
                #print(line)
                    
                if line is None:  # No more lines in the file
                    break
                
                if entry.startswith('DoNextPiece'):

                    # The very first first stage move is not a capture, so don't save a tile.
                    # However the drift measurements are done on the first tile before we get a capture message.  We want to save those
                    if entry.find('capture') >= 0:
                        # We acquired the tile, prepare the next capture
                        if not NextTile is None:
                            NextTile.endAcquisitionTime = timestamp
                            #
                            assert (AcquiredTile is None)  # We are overwriting an unwritten tile if this assertion fails
                            AcquiredTile = NextTile
                            NextTile = None

                    NextTile = LogTileData(timestamp)

                elif entry.startswith('Autofocus Start'):
                    LastAutofocusStart = timestamp
                elif entry.endswith('seconds elapsed time'): #"1715.55 seconds elapsed time" row
                    LastElapsedTimeReport = float(entry.split()[0])
                elif entry.startswith('Measured defocus'):
                    if not NextTile is None:

                        # The very first tile does not get a 'finished stage move' message.  Use the start of the autofocus to approximate completion of the stage move
                        if NextTile.stageStopTime is None:
                            NextTile.stageStopTime = LastAutofocusStart
                            
                        defocusValueUnits = cls.ParseValueAndUnits(entry, 'defocus')
                        if defocusValueUnits is not None:
                            NextTile.driftUnits = defocusValueUnits[1]
                        else:
                            defocusValueUnits = (None, None)
                            
                        (nextLine, nextTimestamp, nextEntry) = SerialEMLog.ReadLine(hLog)
                        driftTimestamp = None
                        if nextTimestamp is None:
                            driftTimestamp = LastAutofocusStart - NextTile.stageStopTime
                        else:
                            driftTimestamp = nextTimestamp - NextTile.stageStopTime
                                
                        driftValueUnits = cls.ParseValueAndUnits(entry, 'drift')
                        if driftValueUnits is not None: 
                            NextTile.driftStamps.append((driftTimestamp, driftValueUnits[0], defocusValueUnits[0]))
                            NextTile.driftUnits = driftValueUnits[1]
                                
                elif entry.startswith('SaveImage Saving'):
                    assert(not AcquiredTile is None)  # We should have already recorded a capture event and populated AcquiredTile before seeing this line in the log
                    FileNumber = cls.ParseValue(entry, 'Z')
                    if FileNumber is not None:
                        AcquiredTile.number = FileNumber
                        AcquiredTile.endAcquisitionTime = timestamp

                        # Determine the position in the grid
                        iAt = entry.find('at')
                        if iAt >= 0:
                            CoordString = entry[iAt + 2:]
                            iComma = CoordString.find(',')
                            if iComma > 0:
                                CoordString = CoordString[:iComma]
                                Coords = CoordString.split()
                                X = int(Coords[0].strip())
                                Y = int(Coords[1].strip())
                                AcquiredTile.coordinates = (X, Y)

                        Data.tileData[AcquiredTile.number] = AcquiredTile
                        AcquiredTile = None

                elif entry.endswith('finished stage move'):
                    # Save the last tile
                    NextTile.stageStopTime = timestamp

                elif entry.startswith('Last update properties file'):
                    (entry, time) = line.split(':', 1)
                    time = time.strip()
                    Data.PropertiesVersion = time
                elif entry.startswith('Checking for stable filament'):
                    if Data.StartupDateTime >= datetime.datetime.strptime('04/03/2020', '%m/%d/%Y'):
                        continue
                    else: #The old, less correct, cooking macro that did not reset the timespan measurement from high mag cooking
                        LegacyFilamentWarmupStartTime = LastElapsedTimeReport
                        
                elif entry.find('Filament is stable!') >= 0:
                        
                    (nextLine, nextTimestamp, nextEntry) = SerialEMLog.ReadLine(hLog)
                    try:
                        elapsed_str = nextEntry.split()[0]
                        elapsed = float(elapsed_str)
                        
                        if LegacyFilamentWarmupStartTime is not None:
                            Data._FilamentStabilizationTime = elapsed - LegacyFilamentWarmupStartTime
                        else:
                            Data._FilamentStabilizationTime = elapsed
                    except:
                        prettyoutput.LogErr("Could not parse filament stabilization time:\n\t{0}\n\t{1}".format(line, nextLine))
                
                        
                        
                elif entry.find('Cooking done!') >= 0:
                    (nextLine, nextTimestamp, nextEntry) = SerialEMLog.ReadLine(hLog)
                    try:
                        elapsed_str = nextEntry.split()[0]
                        elapsed = float(elapsed_str)
                        Data._HighMagCookTime = elapsed
                    except:
                        prettyoutput.LogErr("Could not parse high mag cook time:\n\t{0}\n\t{1}".format(line, nextLine))
                elif entry.find('BEGIN BURN WOBBLE') >= 0:
                    (Data._LowMagCookTime, nextLine) = Data.ParseLowMagCook(hLog)
                elif entry.startswith('SerialEM Version'):
                    Data._version = entry[len('SerialEM Version') + 1:].strip()
                elif entry.startswith('Montage Start'):
                    Data.MontageStart = timestamp
                elif entry.startswith('Montage Done'):
                    Data.MontageEnd = timestamp
                elif entry.startswith('Started'):
                    Data._startup = entry[len('Started') + 1:].strip()
                elif entry.startswith('Microscope Startup'):
                    Data._startup_timestamp = timestamp
                elif entry.startswith('This is a change of'):  # This is a change of 0.08% from the old directly measured matrix
                    subentry = entry[len('This is a change of') + 1:]
                    Data._IS_change_percentage = float(subentry.split('%')[0])
                elif entry.startswith('The mean cross-correlation coefficient was'):  # The mean cross-correlation coefficient was 0.763 and the minimum was 0.716
                    subentry = entry[len('The mean cross-correlation coefficient was') + 1:]
                    parts = subentry.split()
                    Data._IS_mean_cc = float(parts[0])
                    Data._IS_mean_cc = float(parts[5])
                elif entry.startswith('Captured image equally bright as earlier image.  Filament is stable!'):
                    Data._stable_filament_checked = True
                elif entry.startswith('Montage Precook Start'):
                    Data._HighMagCookDone = True
                elif entry.startswith('BEGIN BURN WOBBLE'):
                    Data._LowMagCookDone = True
                    
                if not timestamp is None:
                    LastValidTimestamp = timestamp

            # If we did not find a MontageEnd value use the last valid timestamp
            if Data.MontageEnd is None and not LastValidTimestamp is None:
                Data.MontageEnd = LastValidTimestamp

        serialem_utils.PickleSave(Data, logfullPath)
        return Data
    
    def ParseLowMagCook(self, hLog):
        '''
        Helper function to determine the amount of time spent in the low mag
        cooking macro "Burn Wobble"
        :return: The low mag cook time, None if there were no entries.  The line parsed from the file so we can parse it again with the main parser
        '''
        #Example log:
        #    BEGIN BURN WOBBLE
        #    Stage is NOT busy
        #    Stage is NOT busy
        #    7.17 seconds elapsed time
        #    Stage is NOT busy
        #    Stage is NOT busy
        #    13.08 seconds elapsed time
        #    ...
        #    450.05 seconds elapsed time
        
        LastElapsedTime = None
        
        while(True):
            (line, timestamp, entry) = SerialEMLog.ReadLine(hLog)
            if line is None:
                break
            
            #No BurnWobble log messages have a timestamp, so if we find one we are done
            if timestamp is not None:
                break

            if entry.find("seconds elapsed time") >= 0:
                try:
                    elapsed_str = entry.split()[0]
                    LastElapsedTime = float(elapsed_str) 
                except:
                    prettyoutput.LogErr("Could not parse low mag cook elapsed time:\n\t{0}".format(line))
                continue
            
            #All low mag cook entries either have 
            if entry.find("Stage") < 0:
                break
        
        return (LastElapsedTime, line)
            
        
        
        
    
    @staticmethod
    def ParseValueAndUnits(entry, propertyname):
        '''For log entries with the form 'propertyname = #### units' returns 
           the value and units'''
        
        iProperty = entry.find(propertyname)
        
        if iProperty > -1:
            entry_parts = entry[iProperty:].split()
            # PropertyStr = ' '.entry_parts[0:3].join()  # example: drift = 1.57 nm/sec
            
            if entry_parts[1] == '=':
                ValueStr = entry_parts[2]  # example 1.57 nm/sec
                ValueStr = ValueStr.strip()
                UnitsStr = entry_parts[3]
                UnitsStr = UnitsStr.strip()
                # (Value, Units) = ValueStr.split()
                # Units = Units.strip()
                # Value = Value.strip()
                floatValue = float(ValueStr)
                
                return (floatValue, UnitsStr)
            
        return None
    
    @staticmethod
    def ParseValue(entry, propertyname):
        '''For log entries with the form 'propertyname = ####' returns 
           the value as a number if possible, or as a string'''
        
        iProperty = entry.find(propertyname)
        
        if iProperty > -1:
            entry_parts = entry[iProperty:].split()
            # PropertyStr = ' '.entry_parts[0:3].join()  # example: drift = 1.57 nm/sec
            
            if entry_parts[1] == '=':
                ValueStr = entry_parts[2]  # example 1.57 nm/sec
                ValueStr = ValueStr.strip()
                Value = ValueStr;
                try:
                        
                    # (Value, Units) = ValueStr.split()
                    # Units = Units.strip()
                    # Value = Value.strip()
                    Value = int(ValueStr)
                except ValueError:
                    try:
                        Value = float(ValueStr)
                    except ValueError:
                        pass
                    pass
                
                return Value
            
        return None


def __argToSerialEMLog(arg, usecache=True):
    Data = None
    if arg is None:
        Data = SerialEMLog.Load(sys.argv[1], usecache)
    elif isinstance(arg, str):
        Data = SerialEMLog.Load(arg, usecache)
    elif isinstance(arg, SerialEMLog):
        Data = arg
    else:
        raise Exception("Invalid argument type to PlotDrifGrid")

    return Data


def PlotDriftSettleTime(DataSource, OutputImageFile):
    '''Create a poly line plot showing how each tiles drift rate changed over time'''

    Data = __argToSerialEMLog(DataSource)

    lines = []
    maxdrift = 0
    max_time = 0
    NumTiles = int(0)
    for t in list(Data.tileData.values()):
        if not (t.dwellTime is None or t.drift is None):
            time = []
            drift = []

            for s in t.driftStamps:
                time.append(s[0])
                drift.append(s[1])

            maxdrift = max(maxdrift, t.driftStamps[-1][1])
            max_time = max(max_time, t.driftStamps[-1][0])
            lines.append((time, drift))
            NumTiles = NumTiles + 1

    plot.PolyLine(lines,
                   Title="Stage settle time, max drift %g" % maxdrift,
                   XAxisLabel='Dwell time (sec)',
                   YAxisLabel="Drift (nm/sec)",
                   xlim=(0,max_time+(max_time*0.05)),
                   OutputFilename=OutputImageFile)

    return


def PlotDriftGrid(DataSource, OutputImageFile):

    Data = __argToSerialEMLog(DataSource)

    lines = []
    maxdrift = -1
    NumTiles = int(0)
    fastestTime = float('inf')
    colors = ['black', 'blue', 'green', 'yellow', 'orange', 'red', 'purple']

    DriftGrid = []
    c = []
    for t in list(Data.tileData.values()):
        if not (t.dwellTime is None or t.drift is None):
            time = []
            drift = []

            for s in t.driftStamps:
                time.append(s[0])
                drift.append(s[1])

            colorVal = 'black'
            numPoints = len(t.driftStamps)
            if  numPoints < len(colors):
                colorVal = colors[numPoints]

            c.append(colorVal)

            DriftGrid.append((t.coordinates[0], t.coordinates[1], t.dwellTime)) #pow(t.dwellTime, 1.5)))
            maxdrift = max(maxdrift, t.driftStamps[-1][1])
            if fastestTime is None:
                fastestTime = t.acquisitionTime
            else:
                fastestTime = min(fastestTime, t.acquisitionTime)

            lines.append((time, drift))
            NumTiles = NumTiles + 1

#    print "Fastest Capture: %g" % fastestTime
#

    # PlotHistogram.PolyLinePlot(lines, Title="Stage settle time, max drift %g" % maxdrift, XAxisLabel='Dwell time (sec)', YAxisLabel="Drift (nm/sec)", OutputFilename=None)

    x = []
    y = []
    s = []
    for d in DriftGrid:
        x.append(d[0])
        y.append(d[1])
        s.append(d[2])

    title = "Drift recorded at each capture position in mosaic\nradius = dwell time ^ 2, color = # of tries"

    plot.Scatter(x, y, s, c=c, marker='s', Title=title, XAxisLabel='X', YAxisLabel='Y', OutputFilename=OutputImageFile)

    return


if __name__ == "__main__":
    
    datapath = sys.argv[1]

    basename = os.path.basename(datapath)
    (outfile, ext) = os.path.splitext(basename)
    outdir = os.path.dirname(datapath)

    Data = __argToSerialEMLog(datapath, usecache=False)

    dtime = datetime.timedelta(seconds=round(Data.MontageEnd - Data.MontageStart,1))
    
    total_time = datetime.timedelta(seconds=round((Data.MontageEnd - Data.MontageStart) + Data.LowMagCookTime + Data.HighMagCookTime + Data.FilamentStabilizationTime,1))

    print("Parsed %d tiles" % len(Data.tileData))
    
    if Data.LowMagCookDone:
        low_mag_time = datetime.timedelta(seconds=round(Data.LowMagCookTime))
        print("Low mag cook: %s" % str(low_mag_time))
    else:
        print("No Low mag cook macro")
        
    if Data.HighMagCookDone:
        high_mag_time = datetime.timedelta(seconds=round(Data.HighMagCookTime))
        print("High mag cook: %s" %  str(high_mag_time))
    else:
        print("No High mag cook")
        
    if Data.FilamentStabilizationTime > 0:
        filament_time = datetime.timedelta(seconds=round(Data.FilamentStabilizationTime))
        print("Filament stabilization: %s" %  str(filament_time))
    else:
        print("No Filament stabilization")
         
    setup_time = datetime.timedelta(seconds=round(Data.CaptureSetupTime))
    print("Setup Time: %s" %  str(setup_time))

    print("Average drift: %g nm/sec" % Data.AverageTileDrift)
    print("Min drift: %g nm/sec" % Data.MinTileDrift)
    print("Max drift: %g nm/sec" % Data.MaxTileDrift)
    print("Fastest tile acquisition time: %g sec" % round(Data.FastestTileTime,2))
    print("Average tile settle: %g sec" % round(Data.AverageSettleTime,2))
    print("Average tile acquisition: %g sec" % round(Data.AverageAcquisitionTime,2))
    print("Average total time/tile: %g sec" % round(Data.AverageTileTime,2))
    
    #print("Average drift: %g nm/sec" % Data.AverageTileDrift)
    print("Total Tile Acquisition time: %s" % str(dtime))
    print("Total time: %s" % str(total_time))
    print("Total tiles: %d" % Data.NumTiles)

    PlotDriftGrid(datapath, os.path.join(outdir, outfile + "_driftgrid.svg"))
    PlotDriftSettleTime(datapath, os.path.join(outdir, outfile + "_settletime.svg"))
