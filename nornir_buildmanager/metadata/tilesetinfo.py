'''
Created on Oct 26, 2017

@author: u0490822

The original ir-assemble tool generated an xml file containing metadata for the 
tileset.  TilesetInfo is used to read/write those xml files.
'''

import xml.dom
import xml.etree.ElementTree 


class TilesetInfo(object):
    

    def __init__(self):
        self._GridDimX = None
        self._GridDimY = None
        self._TileDimX = None
        self._TileDimY = None
        self._FilePrefix = None
        self._FilePostfix = None
        self._Downsample = None

    @property
    def GridDimX(self):
        return self._GridDimX

    @GridDimX.setter
    def GridDimX(self, val):
        self._GridDimX = int(val)

    @property
    def GridDimY(self):
        return self._GridDimY

    @GridDimY.setter
    def GridDimY(self, val):
        self._GridDimY = int(val)

    @property
    def TileDimX(self):
        return self._TileDimX

    @TileDimX.setter
    def TileDimX(self, val):
        self._TileDimX = int(val)

    @property
    def TileDimY(self):
        return self._TileDimY

    @TileDimY.setter
    def TileDimY(self, val):
        self._TileDimY = int(val)

    @property
    def FilePrefix(self):
        return self._FilePrefix

    @FilePrefix.setter
    def FilePrefix(self, val):
        self._FilePrefix = val

        if val is None:
            self._FilePrefix = None

        elif len(val) == 0:
            self._FilePrefix = None

    @property
    def FilePostfix(self):
        return self._FilePostfix

    @FilePostfix.setter
    def FilePostfix(self, val):
        self._FilePostfix = val

        if val is None:
            self._FilePostfix = None

        elif len(val) == 0:
            self._FilePostfix = None

    @property
    def Downsample(self):
        return self._Downsample

    @Downsample.setter
    def Downsample(self, val):
        self._Downsample = float(val)

    @classmethod
    def Load(cls, XmlFilePath, Logger=None):
        Info = TilesetInfo()

        try:
            dom = xml.dom.minidom.parse(XmlFilePath)
            levels = dom.getElementsByTagName("Level")
            level = levels[0]

            Info.GridDimX = int(level.getAttribute('GridDimX'))
            Info.GridDimY = int(level.getAttribute('GridDimY'))
            Info.TileDimX = int(level.getAttribute('TileXDim'))
            Info.TileDimY = int(level.getAttribute('TileYDim'))
            fPre = level.getAttribute('FilePrefix')
            fPost = level.getAttribute('FilePostfix')

            Info.FilePrefix = level.getAttribute('FilePrefix')
            Info.FilePostfix = level.getAttribute('FilePostfix')
            Info.Downsample = float(level.getAttribute('Downsample'))
        except Exception as e:
            Logger.warning("Failed to parse XML File: " + XmlFilePath)
            Logger.warning(str(e))
            return

        return Info

    def Save(self, XmlFilePath, Logger=None):
         # Write a new XML file
        if Logger:
            Logger.info('Stage', "WriteTilesetXML : " + XmlFilePath)

        elem = xml.etree.ElementTree.Element('Level')
        elem.set('GridDimY', '%d' % self.GridDimY)
        elem.set('GridDimX', '%d' % self.GridDimX)
        elem.set('TileXDim', '%d' % self.TileDimX)
        elem.set('TileYDim', '%d' % self.TileDimY)
        elem.set('Downsample', '%d' % self.Downsample)

        if self.FilePrefix:
            elem.set('FilePrefix', '%d' % self.FilePrefix)

        if self.FilePostfix:
            elem.set('FilePostfix', '%d' % self.FilePostfix)

        with open(XmlFilePath, 'w') as newXML:
            newXML.write('<?xml version="1.0" ?> \n')
            out = xml.etree.ElementTree.tostring(elem)
            newXML.write(out)
# 
#         with  open(XmlFilePath, 'w') as newXML:
#             newXML.write('<?xml version="1.0" ?> \n')
# 
#             template = '<Level GridDimX="%(GridDimX)d" GridDimY="%(GridDimY)d" TileXDim="%(TileXDim)d" TileYDim="%(TileYDim)d" Downsample="%(Downsample)d" FilePrefix="%(Prefix)s" FilePostFix="%(Postfix)s"/>'
# 
#             outstring = template % {'GridDimX' : self.GridDimX,
#                                     'GridDimY' : self.GridDimY,
#                                     'TileXDim' : self.TileDimX,
#                                     'TileYDim' : self.TileDimY,
#                                     'Downsample' : self.Downsample,
#                                     'Prefix' : self.FilePrefix,
#                                     'Postfix' : self.FilePostfix}
# 
#             newXML.write(outstring)

#            newXML.write('<Level GridDimX=\"' + '%d' % XDim + '\" GridDimY=\"' + '%d' % YDim + 
#                         '\" TileXDim=\"' + '%d' % TileXDim + '\" TileYDim=\"' + '%d' % TileYDim + 
#                        '\" Downsample=\"' + '%d' % DownsampleTarget + '\" FilePrefix=\"' + 
#                         FilePrefix + '\" FilePostfix=\"' + FilePostfix + '\" /> \n')
        return

#def WriteTilesetXML(XMLOutputPath, XDim, YDim, TileXDim, TileYDim, DownsampleTarget, FilePrefix, FilePostfix=".png"):
   



#def __LoadAssembleTilesXML(XmlFilePath, Logger=None):
    

    