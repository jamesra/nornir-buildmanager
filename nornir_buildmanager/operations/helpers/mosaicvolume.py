'''
Created on Jan 30, 2014

@author: u0490822
'''

import nornir_imageregistration.volume as volume
import nornir_imageregistration.mosaic as mosaic
import nornir_imageregistration.files.mosaicfile as mosaicfile
import nornir_imageregistration.transforms.factory as factory

class MosaicVolume(volume.Volume):
    '''
    Converts a list of mosaic transforms into a volume object
    '''

    @classmethod
    def Load(cls, TransformNodes):

        vol = MosaicVolume()

        for transform in TransformNodes:
            mosaicObj = mosaic.Mosaic.LoadFromMosaicFile(transform.FullPath)
            Channel = transform.FindParent('Channel')
            Section = transform.FindParent('Section')

            mosaicObj.transformNode = transform

            sectionKey = "%d_%s" % (Section.Number, Channel.Name)

            vol.AddSection(sectionKey, mosaicObj)

        return vol

    def Save(self):

        for key, mosaicObj in self.SectionToVolumeTransforms.items():

            transformNode = mosaicObj.transformNode

            mosaicObj.SaveToMosaicFile(transformNode.FullPath)

            transformNode.ResetChecksum()
            # transformNode.Checksum = mosaicfile.MosaicFile.LoadChecksum(transformNode.FullPath)
