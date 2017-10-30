import collections
import csv
import os


ContrastValues = collections.namedtuple('ContrastValues', ('Section', 'Min', 'Max', 'Gamma'))
DefaultHistogramFilename = "ContrastOverrides.txt"

def CreateDefaultHistogramCutoffFile(histogramFilename):
    with open(histogramFilename, 'w+') as histogramFilehandle:
        histogramFilehandle.write("#Section Min Max Gamma")
        histogramFilehandle.close()
        
def LoadHistogramCutoffs(filename):
    '''Return a dictionary of section numbers containing named tuples of min,max,gamma values for raw data import
    :param filename str: Filename of histogram values to open
    '''
    
    Values = {}
    if not os.path.exists(filename):
        return Values
    
    with open(filename, 'rb') as contrastFile:
        csvReader = csv.reader(contrastFile, delimiter=' ', skipinitialspace=True,)
        for line in csvReader:
            if len(line) == 0:
                continue
            
            if line[0].startswith("#"):
                continue
            
            try: 
                sectionNumber = int(line[0])
                MinCutoff = float(line[1])
                MaxCutoff = float(line[2])
                Gamma = float(line[3])
                
                Values[sectionNumber] = ContrastValues(sectionNumber, MinCutoff, MaxCutoff, Gamma) 
            except: 
                print("Could not parse histogram line: %s" % ', '.join(line))
        
    return Values

def SaveHistogramCutoffs(filename, SectionToContrast):
    '''
    :param dict values: Dictionary mapping SectionNumber to ContrastValues
    '''

    with open(filename, 'w+') as histogramFilehandle:
        histogramFilehandle.write("#Section Min Max Gamma\n")
        sortedKeys = sorted(SectionToContrast.keys())
        for sectionNumber in sortedKeys:
            values = SectionToContrast[sectionNumber] 
            histogramFilehandle.write("%d %d %d %g\n" % (sectionNumber, values.Min, values.Max, values.Gamma))

        histogramFilehandle.close()

    return
    

def GetFlipList(path):
    FlippedSections = list()

    flipFileName = os.path.join(path, 'FlipList.txt')
    if os.path.exists(flipFileName) == False:
        return FlippedSections

    lines = []
    with open(flipFileName, 'r') as flipFile:
        lines = flipFile.readlines()
        flipFile.close()

    for line in lines:
        sectionNumber = int(line)
        FlippedSections.append(sectionNumber)

    return FlippedSections
