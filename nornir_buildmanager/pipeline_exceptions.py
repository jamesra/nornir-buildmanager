'''
Created on Jan 9, 2019

@author: u0490822
'''

import os
from xml.etree import ElementTree as ElementTree
 
# from nornir_buildmanager.Data import Volumes
# from nornir_buildmanager.Data import Pipelines
class PipelineError(Exception):
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
            s.append("Pipeline Element: {0}\n".format(ElementTree.tostring(self.PipelineNode, encoding='utf-8')))
        if not self.VolumeElem is None:
            s.append("Volume Element: {0}\n".format(ElementTree.tostring(self.VolumeElem, encoding='utf-8')))
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


class PipelineArgumentNotFound(PipelineError):
    '''A select statement could not find the requested node'''

    def __init__(self, argname, **kwargs):
        super(PipelineArgumentNotFound, self).__init__(**kwargs)

        self.argname = argname

    def __CoreErrorList(self):
        s = []
        s.append("Argument Name: " + self.argname)
        s.extend(super(PipelineArgumentNotFound, self).__CoreErrorList())
        return s


class PipelineSearchRootNotFound(PipelineArgumentNotFound):
    '''A select statement could not find the requested node'''

    def __init__(self, argname, **kwargs):
        super(PipelineSearchRootNotFound, self).__init__(**kwargs)

        self.argname = argname

    def __CoreErrorList(self):
        s = []
        s.append("Rootname specified in nornir_buildmanager is not available: " + self.argname)
        s.extend(super(PipelineSearchRootNotFound, self).__CoreErrorList())
        return s


class PipelineRegExSearchFailed(PipelineError):
    '''A regular expression search could not match any nodes'''

    def __init__(self, regex, attribValue, **kwargs):
        super(PipelineRegExSearchFailed, self).__init__(**kwargs)

        self.regex = regex
        self.attribValue = attribValue

    def __CoreErrorList(self):
        s = []
        s.append("A search has failed")
        s.append("Regular Expression: " + self.regex)
        s.append("Attrib value: " + self.attribValue)
        s.extend(super(PipelineError, self).__CoreErrorList())
        return s


class PipelineListIntersectionFailed(PipelineError):
    '''A regular expression search could not match any nodes'''

    def __init__(self, listOfValid, attribValue, **kwargs):

        if not "message" in kwargs:
            kwargs['message'] = '\n'.join(PipelineListIntersectionFailed.GenErrorMessage(list_of_valid=listOfValid, value=attribValue))

        super(PipelineListIntersectionFailed, self).__init__(**kwargs)

        self.listOfValid = listOfValid
        self.attribValue = attribValue


    @classmethod
    def GenErrorMessage(cls, list_of_valid, value):
        s = []
        s.append("Value was not in list")
        s.append("  List: " + str(list_of_valid))
        s.append("  Value: " + str(value))
        return s

    def __CoreErrorList(self):

        s = PipelineListIntersectionFailed.GenErrorMessage(list_of_valid=self.listOfValid, value=self.attribValue)
        s.extend(super(PipelineError, self).__CoreErrorList())
        return s


class PipelineSearchFailed(PipelineError):
    '''A find statement could not match any nodes'''

    def __init__(self, xpath, **kwargs):
        super(PipelineSearchFailed, self).__init__(**kwargs)

        self.xpath = xpath

    def __CoreErrorList(self):
        s = []
        s.append("A search has failed")
        s.append("XPath: " + self.xpath)
        s.extend(super(PipelineError, self).__CoreErrorList())
        return s


class PipelineSelectFailed(PipelineError):
    '''A select statement could not find the requested node.
       This means a variable was not populated and the remaining statements
       in an iteration should not execute or they may use stale data'''

    def __init__(self, xpath, **kwargs):
        super(PipelineSelectFailed, self).__init__(**kwargs)

        self.xpath = xpath

    def __CoreErrorList(self):
        s = []
        s.append("A select statement has failed")
        s.append("XPath: " + self.xpath)
        s.extend(super(PipelineError, self).__CoreErrorList())
        return s