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


class LogTileData(object):
    '''Data for each individual tile in a capture'''

    @property
    def totalTime(self):
        '''Total time required to capture the file, based on interval between DoNextPiece calls in the log'''
        if self.endTime is None:
            return None

        return self.endTime - self.startTime

    @property
    def dwellTime(self):
        '''Total time required to acquire the tile after the stage stopped moving'''
        if self.stageStopTime is None:
            return None

        if self.endTime is None:
            return None

        return self.endTime - self.stageStopTime

    @property
    def settleTime(self):
        '''Total time required to for the first drift measurement below threshold after the stage stopped moving'''
        if len(self.driftStamps) == 0:
            return None

        if self.endTime is None:
            return None

        return self.endTime - self.driftStamps[-1]

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
        if not self.totalTime is None:
            text = text + "%.1f" % self.totalTime
        if len(self.driftStamps) > 0:
            text = text + " %.2f " % self.drift
            if not self.driftUnits is None:
                text = text + " " + self.driftUnits

        return text

    def __init__(self, startTime):
        self.startTime = startTime  # Time when DoNextPiece was logged to begin this tile
        self.endTime = None  # Time when DoNextPiece was logged to start the next tile

        self.stageStopTime = None  # Time when the stage stopped moving

        self.driftStamps = []  # Contains tuples of time after stage move completion, measured drift, and measured defocus
        self.number = None  # The tile number in the capture
        self.driftUnits = None  # nm/sec
        self.defocusUnits = None # microns
        self.coordinates = None


class SerialEMLog(object):

    @classmethod
    def __ObjVersion(cls):
        '''Used for knowing when to ignore a pickled file'''

        # Version 2 fixes missing tile drift times for the first tile in a hemisphere
        return 4

    @property
    def TotalTime(self):
        total = self.MontageEnd - self.MontageStart
        return total

    @property
    def AverageTileTime(self):
        if len(self.tileData) == 0:
            return None
        
        if self._avg_tile_time is None:
            TotalTimes = [t.totalTime for t in self.tileData.values()]
            total = sum(TotalTimes)
            self._avg_tile_time = total / len(self.tileData)  
            
        return self._avg_tile_time

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
        return self._HighMagCookDone
    
    @property
    def LowMagCookDone(self):
        return self._LowMagCookDone
    
    @property
    def StableFilamentChecked(self):
        return self._stable_filament_checked
        

    @property
    def Startup(self):
        return self._startup

    @property
    def Version(self):
        return self._version

    def __init__(self):
        self.tileData = {}  # The time required to capture each tile
        self._startup = None  # SerialEM program Startup time, if known
        self._version = None  # SerialEM version, if known
        self.PropertiesVersion = None  # Timestamp of properties file, if known
        self.MontageStart = None  # timestamp when acquire began
        self.MontageEnd = None  # timestamp when acquire ended
        
        # Cached calculations
        self._avg_tile_time = None
        self._avg_tile_drift = None
        self._num_tiles = None
        self._fastestTime = None
        self._maxdrift = None
        self._mindrift = None
        
        self._IS_change_percentage = None
        self._IS_mean_cc = None
        self._IS_min_cc = None
        
        self._HighMagCookDone = False
        self._LowMagCookDone = False
        self._stable_filament_checked = False
        
        self.__SerialEMLogVersion = SerialEMLog._SerialEMLog__ObjVersion()

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
            obj = cls.__PickleLoad(logfullPath)

            if not obj is None:
                return obj

        Data = SerialEMLog()
        NextTile = None  # The tile we are currently moving the stage, focusing on, and setting up an aquisition for.
        AcquiredTile = None  # The tile which we have an image for, but has not been read from the CCD and saved to disk yet

        with open(logfullPath, 'r') as hLog:

            line = hLog.readline(512)

#            lastDriftMeasure = None
            LastAutofocusStart = None
            LastValidTimestamp = None  # Used in case the log ends abruptly to populate MontageEnd value
            while len(line) > 0:
                line = line.strip()

                # print line

                # See if the entry starts with a timestamp
                entry = line
                timestamp = None

                if len(line) == 0:
                    line = hLog.readline(512)
                    continue

                if line[0].isdigit():
                    try:
                        (timestamp, entry) = line.split(':', 1)
                        timestamp = float(timestamp)
                    except ValueError:
                        pass
                entry = entry.strip()

                if entry.startswith('DoNextPiece'):

                    # The very first first stage move is not a capture, so don't save a tile.
                    # However the drift measurements are done on the first tile before we get a capture message.  We want to save those
                    if entry.find('capture') >= 0:
                        # We acquired the tile, prepare the next capture
                        if not NextTile is None:
                            NextTile.endTime = timestamp
                            #
                            assert (AcquiredTile is None)  # We are overwriting an unwritten tile if this assertion fails
                            AcquiredTile = NextTile
                            NextTile = None

                    NextTile = LogTileData(timestamp)

                elif entry.startswith('Autofocus Start'):
                    LastAutofocusStart = timestamp
                elif entry.startswith('Measured defocus'):
                    if not NextTile is None:

                        # The very first tile does not get a 'finished stage move' message.  Use the start of the autofocus to approximate completion of the stage move
                        if NextTile.stageStopTime is None:
                            NextTile.stageStopTime = LastAutofocusStart
                            
                        defocusValueUnits = cls.ParseValueAndUnits(entry, 'defocus')
                        if defocusValueUnits is not None:
                            driftTimestamp = LastAutofocusStart - NextTile.stageStopTime
                            NextTile.driftUnits = defocusValueUnits[1]
                        else:
                            defocusValueUnits = (None, None)
                                
                        driftValueUnits = cls.ParseValueAndUnits(entry, 'drift')
                        if driftValueUnits is not None:
                            driftTimestamp = LastAutofocusStart - NextTile.stageStopTime
                            NextTile.driftStamps.append((driftTimestamp, driftValueUnits[0], defocusValueUnits[0]))
                            NextTile.driftUnits = driftValueUnits[1]
                                
                elif entry.startswith('SaveImage Saving'):
                    assert(not AcquiredTile is None)  # We should have already recorded a capture event and populated AcquiredTile before seeing this line in the log
                    FileNumber = cls.ParseValue(entry, 'Z')
                    if FileNumber is not None:
                        AcquiredTile.number = FileNumber

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
                elif entry.startswith('SerialEM Version'):
                    Data._version = entry[len('SerialEM Version') + 1:].strip()
                elif entry.startswith('Montage Start'):
                    Data.MontageStart = timestamp
                elif entry.startswith('Montage Done'):
                    Data.MontageEnd = timestamp
                elif entry.startswith('Started'):
                    Data._startup = entry[len('Started') + 1:].strip()
                elif entry.startswith('This is a change of'): #This is a change of 0.08% from the old directly measured matrix
                    subentry = entry[len('This is a change of')+1:]
                    Data._IS_change_percentage = float(subentry.split('%')[0])
                elif entry.startswith('The mean cross-correlation coefficient was'): #The mean cross-correlation coefficient was 0.763 and the minimum was 0.716
                    subentry = entry[len('The mean cross-correlation coefficient was')+1:]
                    parts = subentry.split()
                    Data._IS_mean_cc = float(parts[0])
                    Data._IS_mean_cc = float(parts[5])
                elif entry.startswith('Captured image equally bright as earlier image.  Filament is stable!'):
                    Data._stable_filament_checked = True
                elif entry.startswith('Montage Precook Start'):
                    Data._HighMagCookDone = True
                elif entry.startswith('BEGIN BURN WOBBLE'):
                    Data._LowMagCookDone = True
                    
                line = hLog.readline(512)

                if(not timestamp is None):
                    LastValidTimestamp = timestamp

            # If we did not find a MontageEnd value use the last valid timestamp
            if Data.MontageEnd is None and not LastValidTimestamp is None:
                Data.MontageEnd = LastValidTimestamp

        Data.__PickleSave(logfullPath)
        return Data
    
    @staticmethod
    def ParseValueAndUnits(entry, propertyname):
        '''For log entries with the form 'propertyname = #### units' returns 
           the value and units'''
        
        iProperty = entry.find(propertyname)
        
        if iProperty > -1:
            entry_parts = entry[iProperty:].split()
            #PropertyStr = ' '.entry_parts[0:3].join()  # example: drift = 1.57 nm/sec
            
            if entry_parts[1] == '=':
                ValueStr = entry_parts[2]  # example 1.57 nm/sec
                ValueStr = ValueStr.strip()
                UnitsStr = entry_parts[3]
                UnitsStr = UnitsStr.strip()
                #(Value, Units) = ValueStr.split()
                #Units = Units.strip()
                #Value = Value.strip()
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
            #PropertyStr = ' '.entry_parts[0:3].join()  # example: drift = 1.57 nm/sec
            
            if entry_parts[1] == '=':
                ValueStr = entry_parts[2]  # example 1.57 nm/sec
                ValueStr = ValueStr.strip()
                Value = ValueStr;
                try:
                        
                    #(Value, Units) = ValueStr.split()
                    #Units = Units.strip()
                    #Value = Value.strip()
                    Value = int(ValueStr)
                except ValueError:
                    try:
                        Value = float(ValueStr)
                    except ValueError:
                        pass
                    pass
                
                return Value
            
        return None

def __argToSerialEMLog(arg):
    Data = None
    if arg is None:
        Data = SerialEMLog.Load(sys.argv[1])
    elif isinstance(arg, str):
        Data = SerialEMLog.Load(arg)
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
    NumTiles = int(0)
    for t in list(Data.tileData.values()):
        if not (t.dwellTime is None or t.drift is None):
            time = []
            drift = []

            for s in t.driftStamps:
                time.append(s[0])
                drift.append(s[1])

            maxdrift = max(maxdrift, t.driftStamps[-1][1])
            lines.append((time, drift))
            NumTiles = NumTiles + 1

    plot.PolyLine(lines, Title="Stage settle time, max drift %g" % maxdrift, XAxisLabel='Dwell time (sec)', YAxisLabel="Drift (nm/sec)", OutputFilename=OutputImageFile)

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

            DriftGrid.append((t.coordinates[0], t.coordinates[1], pow(t.dwellTime, 2)))
            maxdrift = max(maxdrift, t.driftStamps[-1][1])
            if fastestTime is None:
                fastestTime = t.totalTime
            else:
                fastestTime = min(fastestTime, t.totalTime)

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

    plot.Scatter(x, y, s, c=c, Title=title, XAxisLabel='X', YAxisLabel='Y', OutputFilename=OutputImageFile)

    return




if __name__ == "__main__":

    datapath = sys.argv[1]

    basename = os.path.basename(datapath)
    (outfile, ext) = os.path.splitext(basename)
    outdir = os.path.dirname(datapath)

    Data = __argToSerialEMLog(datapath)

    dtime = datetime.timedelta(seconds=(Data.MontageEnd - Data.MontageStart))

    print("%d tiles" % len(Data.tileData))

    print("Average drift: %g nm/sec" % Data.AverageTileDrift)
    print("Min drift: %g nm/sec" % Data.MinTileDrift)
    print("Max drift: %g nm/sec" % Data.MaxTileDrift)
    print("Average tile time: %g sec" % Data.AverageTileTime)
    print("Fastest tile time: %g sec" % Data.FastestTileTime)
    print("Total time: %s" % str(dtime))
    print("Total tiles: %d" % Data.NumTiles)

    PlotDriftGrid(datapath, os.path.join(outdir, outfile + "_driftgrid.svg"))
    PlotDriftSettleTime(datapath, os.path.join(outdir, outfile + "_settletime.svg"))
