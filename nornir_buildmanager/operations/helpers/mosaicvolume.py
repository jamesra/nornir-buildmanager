'''
Created on Jan 30, 2014

@author: u0490822
'''

import nornir_buildmanager.operations.block
import nornir_imageregistration.mosaic as mosaic
import nornir_imageregistration.volume as volume
import nornir_pools


class MosaicVolume(volume.Volume):
    '''
    Converts a list of mosaic transforms into a volume object
    '''

    @classmethod
    def LoadVolume(cls, StosMapNode, StosGroupNode, BlockNode, ChannelsRegEx, TransformsRegEx):
        StosMosaicTransformNodes = nornir_buildmanager.operations.block.FetchVolumeTransforms(StosMapNode,
                                                                                              ChannelsRegEx=ChannelsRegEx,
                                                                                              TransformRegEx=TransformsRegEx)
        # Load takes transform nodes, not paths: it reads FullPath itself and needs the
        # Section and Channel parents to key each section. Passing paths raised
        # AttributeError: 'str' object has no attribute 'FullPath'.
        return MosaicVolume.Load(StosMosaicTransformNodes)

    @classmethod
    def Load(cls, TransformNodes):

        vol = MosaicVolume()

        TransformNodes = list(TransformNodes)
        # Two callers independently passed [tnode.FullPath for tnode in ...] here. That
        # only failed once the loop reached transform.FullPath, as an AttributeError on
        # str that says nothing about the contract, so name it up front instead.
        strings = [t for t in TransformNodes if isinstance(t, str)]
        if strings:
            raise TypeError(
                f"MosaicVolume.Load takes transform nodes, not paths; got {len(strings)} "
                f"str of {len(TransformNodes)} entries, starting with {strings[0]!r}. "
                "Load reads FullPath itself and needs each node's Section and Channel "
                "parents to key the section, which a path cannot supply.")

        pool = nornir_pools.GetThreadPool("MosaicVolumeReader", num_threads=2)
        tasks = []
        for transform in TransformNodes:
            task = pool.add_task("Load %s" % transform.FullPath, mosaic.Mosaic.LoadFromMosaicFile, transform.FullPath)

            Channel = transform.FindParent('Channel')
            Section = transform.FindParent('Section')
            task.transformNode = transform  # type: ignore[attr-defined]
            task.sectionKey = "%d_%s" % (Section.Number, Channel.Name)  # type: ignore[attr-defined]
            # mosaicObj.transformNode = transform
            # sectionKey = "%d_%s" % (Section.Number, Channel.Name)

            tasks.append(task)

        for task in tasks:
            # mosaicObj = #mosaic.Mosaic.LoadFromMosaicFile(transform.FullPath)
            mosaicObj = task.wait_return()
            mosaicObj.transformNode = task.transformNode

            vol.AddSection(task.sectionKey, mosaicObj)

        return vol

    def Save(self):

        for key, mosaicObj in list(self.SectionToVolumeTransforms.items()):
            transformNode = mosaicObj.transformNode

            mosaicObj.SaveToMosaicFile(transformNode.FullPath)

            transformNode.ResetChecksum()
            # transformNode.Checksum = mosaicfile.MosaicFile.LoadChecksum(transformNode.FullPath)
