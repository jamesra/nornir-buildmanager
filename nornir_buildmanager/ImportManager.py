'''
Created on Apr 2, 2012

@author: James Anderson
'''

import logging
import os
import sys
import xml.etree.ElementTree

from nornir_buildmanager import Config
import nornir_buildmanager.VolumeManagerETree as VM
from nornir_shared.files import *
from nornir_shared.reflection import *


import nornir_shared.prettyoutput as prettyoutput
import argparse

def DefaultImportPackagePath():

    try:
        path = os.path.dirname(__file__)
    except:
        path = os.getcwd()

    return os.path.join(path, 'importers')

class ExtensionData:
    def __init__(self):
        self.ext = None
        self.classObj = None
        self.ImportFunction = None
        pass

class ImportManager(object):
    '''This class parses the passed directory and all subdirectories
       any files or directories matching a known importer type will
       be processed by the appropriate class'''



    def __init__(self, importerDict=None, extensionInfoDict=None):
        '''
        Constructor
        ImporterDict is a mapping of extensions to an importer class
        '''
        self.ImporterDict = importerDict
        self.ExtensionInfo = extensionInfoDict
        pass

    @classmethod
    def Load(cls, ImporterXmlFile):
        if not os.path.exists(ImporterXmlFile):
            prettyoutput.Log("Provided importer filename does not exist: " + ImporterXmlFile)
            return None

        # Add the pipeline/importers directory to the pythonpath
        ImportDir = DefaultImportPackagePath()
        sys.path.append(ImportDir)

        ImporterDict = dict()
        ImporterETree = xml.etree.ElementTree.parse(ImporterXmlFile)

        ImporterRootElem = ImporterETree.getroot()

        prettyoutput.Log("Import mapping list")
        for entry in ImporterRootElem:
            Data = ExtensionData()

            ext = entry.attrib.get('Extension', None)
            if(ext is None):
                continue

            ImportClassName = entry.attrib.get('ImportClass', None)
            ImportFunctionName = entry.attrib.get('ImportFunction', None)

            Data.ext = ext
            Data.classObj = get_class(ImportClassName)
            if(Data.classObj is None):
                prettyoutput.LogErr("Importer class not found: " + ImportClassName)
                sys.exit()

            Data.ImportFunction = getattr(Data.classObj, ImportFunctionName)
            if(Data.ImportFunction is None):
                prettyoutput.LogErr("Importer function not found: " + ImportFunctionName)
                sys.exit()

            keywordArgs = dict()
            ArgumentsNode = entry.find('Arguments')

            if not ArgumentsNode is None:

                for arg in ArgumentsNode:
                    NameStr = arg.attrib.get('Name', None)
                    if NameStr is None:
                        prettyoutput.LogErr('No name found for argument in importer: ' + str(Data))
                        continue

                    valStr = arg.attrib.get('Value', None)
                    val = None
                    # Try to convert the argument to an int, then double, then settle for a string
                    try:
                        val = int(valStr)
                    except:
                        try:
                            val = float(valStr)
                        except:
                            val = valStr

                    keywordArgs[NameStr] = val

            Data.Args = keywordArgs
            ImporterDict[ext] = Data

        prettyoutput.Log("")

        return ImportManager(ImporterDict)


    def ExtendParser(self, parser):
        parser.add_argument('-overlap',
                        action='store',
                        required=False,
                        default=0.10,
                        type=float,
                        help='Value to use with ir-prune',
                        dest='TileOverlap'
                        )

        parser.add_argument('-bpp',
                        action='store',
                        required=False,
                        default=8,
                        type=int,
                        help='Bits per pixel',
                        dest='TargetBpp'
                        )

        parser.add_argument('-debug',
                        action='store_true',
                        required=False,
                        default=False,
                        help='For debugging purposes exceptions are not handled',
                        dest='debug')

        # parser.add_argument('args', nargs=argparse.REMAINDER)

    # Convert all PMG's in Rootpath and subdirectories to mosaic files
    def ConvertAll(self, RootPath, OutPath, extraargs=None):
        '''This returns an object describing a volume'''

        parser = argparse.ArgumentParser('Import Manager')
        self.ExtendParser(parser)
        (args, extra) = parser.parse_known_args(extraargs)
        TargetBpp = args.TargetBpp
        TileOverlap = args.TileOverlap

        ChannelsForSections = dict()
        ChannelCount = dict()

        # If the user did not supply a value, use a default
        if(TileOverlap is None):
            TileOverlap = 0.10

        VolumeObj = VM.VolumeManager.Load(OutPath, Create=True)

        logger = logging.getLogger(__name__ + '.ConvertAll')

        # Get all of the directories we should consider running PMGToMosaic on.
        for extension in self.ImporterDict.keys():
            DirList = RecurseSubdirectoriesGenerator(RootPath, RequiredFiles="*." + extension)
            ExtenstonDataObj = self.ImporterDict[extension]

            for Path in DirList:
                try:
                    ExtenstonDataObj.ImportFunction(VolumeObj, Path, OutPath, TileOverlap=TileOverlap, Extension=extension, debug=args.debug, TargetBpp=TargetBpp, **ExtenstonDataObj.Args)
                except:
                    logger.error('Could not import: ' + Path)
                    raise

                VM.VolumeManager.Save(OutPath, VolumeObj)

if __name__ == "__main__":

    ImportersXmlFilename = 'D:\Buildscript\Importers.xml'
    ImportManager.Load(ImportersXmlFilename)


