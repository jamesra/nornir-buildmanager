'''
Created on Jan 30, 2014

@author: u0490822
'''

import nornir_imageregistration.volume as volume
import nornir_imageregistration.files.stosfile as stosfile
import nornir_imageregistration.transforms.factory as factory

class StosGroupVolume(volume.Volume):
    '''
    Loads all transforms in a StosGroup node into a volume object. 
    '''

    @classmethod
    def Load(cls, StosGroupNode):
        '''Load transforms from a stos group into a volume object.'''

        vol = StosGroupVolume()

        TransformToNodeAndStosObj = {}

        for mappingNode in StosGroupNode.SectionMappings:
            for transformNode in mappingNode.Transforms:
                stosObj = stosfile.StosFile.Load(transformNode.FullPath)
                stosTransform = factory.LoadTransform(stosObj.Transform, StosGroupNode.Downsample)

                sectionKey = "%s_%s" % (transformNode.MappedSectionNumber, transformNode.MappedChannelName)

                stosTransform.transformNode = transformNode
                stosTransform.stosObj = stosObj

                vol.AddOrUpdateSection(sectionKey, stosTransform)

        return vol

    def Save(self):

        for key, transform in self.SectionToVolumeTransforms.items():

            transformNode = transform.transformNode
            originalStos = transform.stosObj

            originalStos.Transform = factory.TransformToIRToolsString(transform)

            originalStos.Save(transformNode.FullPath)
            transformNode.Checksum = originalStos.Checksum
