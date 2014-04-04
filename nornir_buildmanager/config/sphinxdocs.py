'''

Prune
----------

    .. argparse::
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_Prune
       :prog: nornir_build -pipeline TEMPrepare
       
SetPruneCutoff
--------------

    .. argparse::
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_SetPruneCutoff
       :prog: nornir_build -pipeline SetPruneCutoff


ShadeCorrect
--------------------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_ShadeCorrect
       :prog: nornir_build -pipeline ShadeCorrect

AdjustContrast
--------------------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_AdjustContrast
       :prog: nornir_build -pipeline AdjustContrast
       
SetContrast
--------------

    .. argparse::
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_SetContrast
       :prog: nornir_build -pipeline SetContrast
       
SetFilterLock
-------------

    .. argparse::
        :module: nornir_buildmanager.config.sphinxdocs
        :func: _doc_arguments_SetFilterLock
        :prog: nornir_build -pipeline SetFilterLock
 
Mosaic
----------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_Mosaic
       :prog: nornir_build -pipeline Mosaic


Assemble
----------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_Assemble
       :prog: nornir_build -pipeline Assemble
       
ExportImages
------------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_ExportImages
       :prog: nornir_build -pipeline ExportImages

MosaicReport
--------------------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_MosaicReport
       :prog: nornir_build -pipeline MosaicReport

StosReport
----------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_StosReport
       :prog: nornir_build -pipeline StosReport

CreateVikingXML
--------------------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_CreateVikingXML
       :prog: nornir_build -pipeline CreateVikingXML

CreateBlobFilter
--------------------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_CreateBlobFilter
       :prog: nornir_build -pipeline CreateBlobFilter
       
AlignSections
--------------------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_AlignSections
       :prog: nornir_build -pipeline AlignSections

RefineSectionAlignment
------------------------------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_RefineSectionAlignment
       :prog: nornir_build -pipeline RefineSectionAlignment

SliceToVolume
--------------------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_SliceToVolume
       :prog: nornir_build -pipeline SliceToVolume

ScaleVolumeTransforms
------------------------------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_ScaleVolumeTransforms
       :prog: nornir_build -pipeline ScaleVolumeTransforms

VolumeImage
--------------------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_VolumeImage
       :prog: nornir_build -pipeline VolumeImage

MosaicToVolume
--------------------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_MosaicToVolume
       :prog: nornir_build -pipeline MosaicToVolume

RenameFilter
--------------------

    .. argparse:: 
       :module: nornir_buildmanager.config.sphinxdocs
       :func: _doc_arguments_RenameFilter
       :prog: nornir_build -pipeline RenameFilter

'''

import nornir_buildmanager.pipelinemanager
import nornir_buildmanager.build
import os

def _doc_arguments(pipelinename):
    configpath = nornir_buildmanager.build.ConfigDataPath()
    manager = nornir_buildmanager.pipelinemanager.PipelineManager.Load(os.path.join(configpath, 'Pipelines.xml'), pipelinename)
    return manager.GetArgParser(parser=None, IncludeGlobals=False)

def _doc_arguments_Prune():
    return _doc_arguments('Prune')

def _doc_arguments_SetPruneCutoff():
    return _doc_arguments('SetPruneCutoff')

def _doc_arguments_ShadeCorrect():
    return _doc_arguments('ShadeCorrect')

def _doc_arguments_AdjustContrast():
    return _doc_arguments('AdjustContrast')

def _doc_arguments_SetContrast():
    return _doc_arguments('SetContrast')

def _doc_arguments_SetFilterLock():
    return _doc_arguments('SetFilterLock')

def _doc_arguments_RenameFilter():
    return _doc_arguments('RenameFilter')

def _doc_arguments_Mosaic():
    return _doc_arguments('Mosaic')

def _doc_arguments_Assemble():
    return _doc_arguments('Assemble')

def _doc_arguments_ExportImages():
    return _doc_arguments('ExportImages')

def _doc_arguments_MosaicReport():
    return _doc_arguments('MosaicReport')

def _doc_arguments_StosReport():
    return _doc_arguments('StosReport')

def _doc_arguments_CreateVikingXML():
    return _doc_arguments('CreateVikingXML')

def _doc_arguments_CreateBlobFilter():
    return _doc_arguments('CreateBlobFilter')

def _doc_arguments_AlignSections():
    return _doc_arguments('AlignSections')

def _doc_arguments_RefineSectionAlignment():
    return _doc_arguments('RefineSectionAlignment')

def _doc_arguments_SliceToVolume():
    return _doc_arguments('SliceToVolume')

def _doc_arguments_ScaleVolumeTransforms():
    return _doc_arguments('ScaleVolumeTransforms')

def _doc_arguments_VolumeImage():
    return _doc_arguments('VolumeImage')

def _doc_arguments_MosaicToVolume():
    return _doc_arguments('MosaicToVolume')


if __name__ == '__main__':
    pass