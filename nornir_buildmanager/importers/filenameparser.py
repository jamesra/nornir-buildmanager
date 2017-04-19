'''
Created on Apr 15, 2013

@author: u0490822
'''

from _abcoll import Mapping
import os
import types


# import logging
class FilenameInfo(object):

    def __init__(self, **kwargs):
        if not kwargs is None:
            self.__dict__.update(kwargs);

class mapping():

    def __init__(self, attribname, typefunc, **kwargs):
        self.attribute = attribname
        self.typefunc = typefunc

        if 'default' in kwargs:
            self.default = kwargs['default']

    def __str__(self):
        outstr = self.attribute
        if hasattr(self, 'default'):
            outstr += '=' + str(self.default)

        outstr += " " + str(self.typefunc)
        return outstr


def __MappingUsageString(mapping):
    outstr = "Expected the following format for parse filename:\n"
    outstr += __MappingFormatString(mapping)
    return outstr


def __MappingFormatString(mappingList):
    outstr = ""
    for m in mappingList:
        outstr += m.attribute

        if isinstance(m.typefunc, int):
            outstr += '#'

        outstr += "_"

    if len(outstr) > 0:
        outstr = outstr[:-1]

    return outstr

def __NumRequriedArgsForMapping(mappingList):

    NumRequiredArgs = 0
    for m in mappingList:
        if not hasattr(m, 'default'):
            NumRequiredArgs = NumRequiredArgs + 1

    return NumRequiredArgs

def ParseFilename(filePath, mappinglist):

 #   Logger = logging.getLogger("Parse filename");

    fileBase = os.path.basename(filePath);

    # Make sure there are no spaces in the filename

    (fileName, ext) = os.path.splitext(fileBase);

    parts = fileName.split("_");

    Output = FilenameInfo();

    if len(parts) < __NumRequriedArgsForMapping(mappinglist):
        print __MappingUsageString(mappinglist)
        raise Exception("Insufficient arguments in filename " + fileName);
    elif len(parts) > len(mappinglist) + 1:
        print __MappingUsageString(mappinglist)
        raise Exception("Too many underscores in filename " + fileName);

    for mappingObj in mappinglist:
        try:
            value = parts[0]
            mapfunc = mappingObj.typefunc
            convValueList = map(mapfunc, [value]);

            ConvValue = convValueList[0];

            setattr(Output, mappingObj.attribute, ConvValue);

            del parts[0]
        except:
            if not hasattr(mappingObj, 'default'):
                print __MappingUsageString(mappinglist)
                raise Exception("Cannot parse parameter \"" + mappingObj.attribute + "\" from PMG filename:\n" + fileName)
            else:
                defaultVal = mappingObj.default
                if callable(mappingObj.default):
                    defaultVal = defaultVal()

                setattr(Output, mappingObj.attribute, defaultVal)

    return Output
