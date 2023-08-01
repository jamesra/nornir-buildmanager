import csv
import os
from typing import NamedTuple

import nornir_buildmanager
from . import idoc, serialemlog, shared


class ContrastValue(NamedTuple):
    Section: int
    Min: int
    Max: int
    Gamma: int = 1.0


DefaultHistogramFilename = "ContrastOverrides.txt"


class OldVersionException(Exception):

    def __init__(self, text):
        self.text = text
        super(OldVersionException, self).__init__()

    def __str__(self):
        return repr(self.text)


def CreateDefaultHistogramCutoffFile(histogramFilename: str):
    with open(histogramFilename, 'w+') as histogramFilehandle:
        histogramFilehandle.write("#Section Min Max Gamma")
        histogramFilehandle.close()


def LoadHistogramCutoffs(filename: str) -> dict[int, ContrastValue]:
    """
    Return a dictionary of section numbers containing named tuples of min,max,gamma values for raw data import
    The file is space delimited, and if a value in a column is not a number then the default calculated value
    will be used instead of an override
    :param str filename: Filename of histogram values to open
    """

    Values = {}
    if not os.path.exists(filename):
        return Values

    with open(filename, 'r') as contrastFile:
        csvReader = csv.reader(contrastFile, delimiter=' ', skipinitialspace=True, )
        for (line_number, line) in enumerate(csvReader):
            if len(line) == 0:
                continue

            if line[0].startswith("#"):
                continue

            try:
                sectionNumber = int(line[0])

                try:
                    MinCutoff = float(line[1])
                except ValueError:
                    MinCutoff = None

                try:
                    MaxCutoff = float(line[2])
                except ValueError:
                    MaxCutoff = None

                try:
                    Gamma = None if len(line) < 3 else float(line[3])
                except ValueError:
                    Gamma = None

                Values[sectionNumber] = ContrastValue(sectionNumber, MinCutoff, MaxCutoff, Gamma)
            except:
                print(f"Could not parse histogram line #{line_number}: {', '.join(line)}")

    return Values


def SaveHistogramCutoffs(filename: str, SectionToContrast: dict[int, ContrastValue]):
    """
    :param filename:
    :param SectionToContrast:
    """

    with open(filename, 'w+') as histogramFilehandle:
        histogramFilehandle.write("#Section Min Max Gamma\n")
        sortedKeys = sorted(SectionToContrast.keys())
        for sectionNumber in sortedKeys:
            values = SectionToContrast[sectionNumber]
            histogramFilehandle.write("%d %d %d %g\n" % (sectionNumber, values.Min, values.Max, values.Gamma))

        histogramFilehandle.close()

    return


def GetFlipList(path: str) -> list[int]:
    FlippedSections = list()

    flipFileName = os.path.join(path, 'FlipList.txt')
    if not os.path.exists(flipFileName):
        return FlippedSections

    with open(flipFileName, 'r') as flipFile:
        lines = flipFile.readlines()
        flipFile.close()

    for line in lines:
        sectionNumber = int(line)
        FlippedSections.append(sectionNumber)

    return FlippedSections


def GetFileNameForTileNumber(tile_number: int, ext: str) -> str:
    if ext[0] == '.':
        ext = ext[1:]

    return f"{nornir_buildmanager.templates.Current.TileCoordFormat % tile_number}.{ext}"
