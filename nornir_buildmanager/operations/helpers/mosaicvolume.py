'''
Created on Jan 30, 2014

@author: u0490822
'''

import nornir_imageregistration.volume as volume
import nornir_imageregistration.mosaic as mosaic
import nornir_imageregistration.files.mosaicfile as mosaicfile
import nornir_imageregistration.transforms.factory as factory
import nornir_pools

class MosaicVolume(volume.Volume):
    '''
    Converts a list of mosaic transforms into a volume object
    '''

    @classmethod
    def Load(cls, TransformNodes):

        vol = MosaicVolume()
        
        pool = nornir_pools.GetThreadPool("MosaicVolumeReader", num_threads=2)        
        tasks = []
        for transform in TransformNodes:
            task = pool.add_task("Load %s" % transform.FullPath, mosaic.Mosaic.LoadFromMosaicFile, transform.FullPath)
            
            Channel = transform.FindParent('Channel')
            Section = transform.FindParent('Section')
            task.transformNode = transform
            task.sectionKey = "%d_%s" % (Section.Number, Channel.Name) 
            #mosaicObj.transformNode = transform
            #sectionKey = "%d_%s" % (Section.Number, Channel.Name)
            
            tasks.append(task)

        for task in tasks:
            #mosaicObj = #mosaic.Mosaic.LoadFromMosaicFile(transform.FullPath)
            mosaicObj = task.wait_return()
            mosaicObj.transformNode = task.transformNode

            vol.AddSection(task.sectionKey, mosaicObj)

        return vol

    def Save(self):

        for key, mosaicObj in self.SectionToVolumeTransforms.items():

            transformNode = mosaicObj.transformNode

            mosaicObj.SaveToMosaicFile(transformNode.FullPath)

            transformNode.ResetChecksum()
            # transformNode.Checksum = mosaicfile.MosaicFile.LoadChecksum(transformNode.FullPath)
