'''
Created on Jan 9, 2019

@author: u0490822
'''

import os

class Build(Exception):
    '''An expected node did not exist'''


    def __init__(self, VolumeElem=None, PipelineNode=None, message=None, **kwargs):
        super(PipelineError, self).__init__(**kwargs)

        self.PipelineNode = PipelineNode
        self.VolumeElem = VolumeElem
        self.message = message

    @property
    def __ErrorHeader(self):
        return os.linesep + "*"*80 + os.linesep

    @property
    def __ErrorFooter(self):
        return os.linesep + "*"*80 + os.linesep

    def __CoreErrorList(self):
        '''return a list of error strings'''
        s = []
        if not self.PipelineNode is None:
            s.append("Pipeline Element: " + ElementTree.tostring(self.PipelineNode, encoding='utf-8') + '\n')
        if not self.VolumeElem is None:
            s.append("Volume Element: " + ElementTree.tostring(self.VolumeElem, encoding='utf-8') + '\n')
        return s

    def ErrorList(self):
        s = []
        s.extend([self.__ErrorHeader])
        if not self.message is None:
            s.extend([self.message])

        s.extend(self.__CoreErrorList())
        s.extend([self.__ErrorFooter])

        return s

    def __str__(self):

        return "\n".join(self.ErrorList())