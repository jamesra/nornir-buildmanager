'''
Created on Jul 2, 2012

@author: Jamesan
'''

from nornir_buildmanager.VolumeManagerETree import *


LatestElementVersions = {'PruneData' : 1.2, 'HistogramData' : 1.1};

CompatibleElementVersions = {'PruneData' : 1.2, 'HistogramData' : 1.1};
#
# def ConvertHistogramData(ParentElement, DeprecatedElement):
#    NewElement = etree.Element("Histogram", {'InputTransformChecksum' : DeprecatedElement.attrib['InputTransformChecksum'],
#                                             'InputTransformType' : "_Thr1.0",
#                                             'Type' : DeprecatedElement.attrib['Type'],
#                                             'CreationDate' : DeprecatedElement.attrib['CreationDate']})
#

# This is a dictionary listing deprecated nodes with a function to call to resolve them when they are encountered.
# Resolution functions should have the form func(ParentElement, DeprecatedElement).  If None is specified the
# element is simply removed when encountered'
# DeprecatedNodes = {'PruneData':None, 'HistogramData' : ConvertHistogramData];
DeprecatedNodes = ['PruneData', 'HistogramData'];

def GetLatestVersionForNodeType(tag):
    return float(LatestElementVersions.get(tag, 1.0));

def IsNodeVersionCompatible(tag, version):
    if tag in CompatibleElementVersions:
        return version >= CompatibleElementVersions[tag];

    return LatestElementVersions.get(tag, 1.0) >= version;

if __name__ == '__main__':
    pass
