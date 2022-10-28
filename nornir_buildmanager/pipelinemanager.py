'''
Created on Apr 2, 2012

'''

import collections.abc
import copy
import logging
import re
import sys
import traceback
import platform

import nornir_buildmanager

from .pipeline_exceptions import *

import nornir_pools
import nornir_shared.misc
import nornir_shared.reflection
from inspect import isgenerator

from . import argparsexml
import nornir_shared.prettyoutput as prettyoutput
from xml.etree import ElementTree


# import xml.etree
class ArgumentSet:
    '''Collection of arguments from each source'''

    @property
    def Arguments(self):
        return self._Arguments

    @property
    def Attribs(self):
        return self._Attribs

    @property
    def Parameters(self):
        return self._Parameters

    @property
    def Variables(self):
        return self._Variables

    def __init__(self, PipelineName=None):
        self._Arguments = {}
        self._Attribs = {}
        self._Parameters = {}
        self._Variables = {}
        self.PipelineName = PipelineName

    def SubstituteStringVariables(self, xpath):
        '''Replace all instances of # in a string with the variable names'''

        iStart = xpath.find("#")
        while iStart >= 0:
            xpath = self.ReplaceVariable(xpath, iStart)

            iStart = xpath.find("#")

        return xpath

    def TryGetValueForKey(self, key):
        if key in self.Arguments:
            return self.Arguments[key]

        if key in self.Variables:
            return self.Variables[key]

        raise KeyError(str(key) + " not found")

    def ReplaceVariable(self, xpath, iStart):
        '''Replace # variable names in an xpath with variable string values'''

        # Find the next # if it exists
        iStart = iStart + 1  # Skip the # symbol
        iEndVar = xpath[iStart + 1:].find('#')
        if iEndVar < 0:
            # OK, just check to the end of the string
            iEndVar = len(xpath)
        else:
            iEndVar = iStart + iEndVar + 1

        while iEndVar > iStart:
            keyCheck = xpath[iStart:iEndVar]

            try:
                value = self.TryGetValueForKey(keyCheck)
                xpath = xpath[:iStart - 1] + str(value) + xpath[iEndVar:]
                return xpath
            except KeyError as e:
                pass

            iEndVar = iEndVar - 1

        # logger = logging.getLogger(__name__ + ".ReplaceVariable")
        # logger.error("nornir_buildmanager XPath variable not defined.\nXPath: " + xpath)
        prettyoutput.LogErr("nornir_buildmanager XPath variable not defined.\nXPath: " + xpath)
        sys.exit()

    def TryGetSubstituteObject(self, val):
        '''Place an object directly into a dictionary. 
        :param str val: The key to lookup
        :return: Tuple (bool, value) Returns true on success with the value or None for failure.  Not if the value is found and the value is None then (true, None) is returned.'''
        if val is None:
            return False, None

        if len(val) == 0:
            return False, None

        if val[0] == '#':
            if val[1:].find('#') >= 0:
                # If there are two '#' signs in the string it is a string not an object
                return False, None

            # Find the existing entry in the dargs
            key = val[1:]

            try:
                # If no object found then return None
                return True, self.TryGetValueForKey(key)
            except KeyError as e:
                return False, None

        return False, None

    def AddArguments(self, args):
        '''Add arguments from the command line'''

        if isinstance(args, dict):
            self._Arguments.update(args)
        else:
            self._Arguments.update(args.__dict__)

    def KeyWordArgs(self):

        kwargs = {}
        kwargs.update(self.Variables)
        kwargs.update(self.Attribs)
        kwargs['Parameters'] = self.Parameters

        return kwargs

    def AddAttributes(self, Node):
        '''Add attributes from an element node to the dargs dictionary.
           Parameter values preceeded by a '#' are keys to existing
           entries in the dictionary whose values are copied to the 
           entry for the attribute'''

        for key in Node.attrib:
            if key in self.Attribs:
                raise PipelineError(PipelineNode=Node,
                                    message="%s attribute already present in arguments.  Remove duplicate use from pipelines.xml" % key)
            val = Node.attrib[key]

            if len(val) == 0:
                self.Attribs[key] = val
                continue

            (found, subObj) = self.TryGetSubstituteObject(val)
            if found:
                self.Attribs[key] = subObj
                continue

            val = self.SubstituteStringVariables(val)

            self.Attribs[key] = val
            try:
                self.Attribs[key] = int(val)
                continue
            except ValueError:
                pass

            try:
                self.Attribs[key] = float(val)
                continue
            except ValueError:
                pass

    def RemoveAttributes(self, Node):
        '''Remove attributes present in the node from the attrib dictionary'''

        for key in Node.attrib:
            if key in self.Attribs:
                val = Node.attrib[key]
                # If we assign a variable to an attribute with the same name to ourselves do not overwrite
                if val[0] == '#':
                    if val[1:] == key:
                        continue

                del self.Attribs[key]

    def ClearAttributes(self):
        self.Attribs.clear()

    def AddParameters(self, Node, dargsKeyname=None):
        '''Add entries from a dictionary node to the dargs dictionary.
           Parameter values preceeded by a '#' are keys to existing
           entries in the dictionary whose values are copied to the 
           entry for the attribute. 
           If dargsKeyname is none all parameters are added directly 
           to dargs dictionary.  Otherwise they are added as a dictionary
           under a key=dargsKeyname'''

        ParamNodes = Node.findall('Parameters')
        NewParameters = {}

        for PN in ParamNodes:
            for entryNode in PN:
                if entryNode.tag == 'Entry':
                    name = entryNode.attrib['Name']
                    val = entryNode.attrib.get('Value', '')

                    (found, subObj) = self.TryGetSubstituteObject(val)
                    if found:
                        NewParameters[name] = subObj
                        continue

                    val = self.SubstituteStringVariables(val)

                    if len(val) == 0:
                        NewParameters[name] = val
                        continue

                    NewParameters[name] = val
                    try:
                        NewParameters[name] = int(val)
                        continue
                    except ValueError:
                        pass

                    try:
                        NewParameters[name] = float(val)
                        continue
                    except ValueError:
                        pass

        if dargsKeyname is None:
            self.Parameters.update(NewParameters)
        else:
            self.Parameters[dargsKeyname] = NewParameters

    def RemoveParameters(self, Node):
        """
        :param Node:
        :return:
        Remove entries from a dargs dictionary.
        """

        # print "Remove Parameters"

        ParamNodes = Node.findall('Parameters')

        for PN in ParamNodes:
            for entryNode in PN:
                if entryNode.tag == 'Entry':
                    name = entryNode.attrib['Name']
                    if 'Value' not in entryNode.attrib:
                        continue

                    val = entryNode.attrib['Value']
                    if name in list(self.Parameters.keys()):
                        if val[0] == '#':
                            if val[1:] == name:
                                continue

                        # print "Removing " + name
                        del self.Parameters[name]

    def ClearParameters(self):
        self.Parameters.clear()

    def AddVariable(self, key, value):
        self.Variables[key] = value

    def RemoveVariable(self, key):
        del self.Variables[key]


class ExtensionData:
    def __init__(self):
        self.ext = None
        self.classObj = None
        self.ImportFunction = None
        self.defaultArgs = dict()
        pass


class PipelineManager(object):
    logger = logging.getLogger('PipelineManager')

    '''Responsible for the execution of a pipeline specified in an XML file following the buildscript.xsd specification'''

    def __init__(self, pipelinesRoot, pipelineData):
        self.VolumeTree = None
        self.PipelineData = pipelineData
        self.defaultArgs = dict()
        self.PipelineRoot = pipelinesRoot

        if 'Description' in pipelineData.attrib:
            self._description = pipelineData.attrib['Description']

        if 'Help' in pipelineData.attrib:
            self._help = pipelineData.attrib['Help']

        if 'Epilog' in pipelineData.attrib:
            self._epilog = pipelineData.attrib['Epilog']

    @property
    def Description(self):
        if hasattr(self, '_description'):
            return self._description

        return None

    @property
    def Help(self):
        if hasattr(self, '_help'):
            return self._help

        return None

    @property
    def Epilog(self):
        if hasattr(self, '_epilog'):
            return self._epilog

        return None

    @classmethod
    def ToElementString(cls, element):
        if element.tag == 'Iterate':
            return "Iterate: " + element.attrib['XPath']

        outStr = ""
        strList = [s.decode('Utf-8') for s in ElementTree.tostringlist(element)]
        for s in strList:
            outStr = outStr + " " + s
            if s == '>':
                break

        return outStr

    @classmethod
    def PrintPipelineEnumeration(cls, PipelineXML):
        PipelineXML = cls.LoadPipelineXML(PipelineXML)

        cls.logger.info("Enumerating available pipelines")
        prettyoutput.Log("Enumerating available pipelines")
        for pipeline in PipelineXML.getroot():
            cls.logger.info('  ' + pipeline.attrib.get('Name', ""))
            prettyoutput.Log('  ' + pipeline.attrib.get('Name', ""))
            prettyoutput.Log('    ' + pipeline.attrib.get('Description', "") + '\n')

    @classmethod
    def _CheckPipelineXMLExists(cls, PipelineXmlFile):
        if not os.path.exists(PipelineXmlFile):
            PipelineManager.logger.critical("Provided pipeline filename does not exist: " + PipelineXmlFile)
            prettyoutput.LogErr("Provided pipeline filename does not exist: " + PipelineXmlFile)
            sys.exit()

        return True

    @classmethod
    def LoadPipelineXML(cls, PipelineXML):
        # Python 3 switched to unicode always so the encoding should not be necessary for non-english character sets
        if int(platform.python_version_tuple()[0]) < 3:
            if isinstance(PipelineXML, str):
                PipelineXML = PipelineXML.encode(sys.getdefaultencoding())

        if isinstance(PipelineXML, str):
            if cls._CheckPipelineXMLExists(PipelineXML):
                return ElementTree.parse(PipelineXML)

        elif isinstance(PipelineXML, ElementTree.ElementTree):
            return PipelineXML

        raise Exception("Invalid argument: " + str(PipelineXML))

    @classmethod
    def ListPipelines(cls, PipelineXML):

        PipelineXML = cls.LoadPipelineXML(PipelineXML)

        assert (isinstance(PipelineXML, ElementTree.ElementTree))

        PipelineNodes = PipelineXML.findall("Pipeline")

        PipelineNames = [p.attrib['Name'] for p in PipelineNodes]
        return sorted(PipelineNames)

    @classmethod
    def __PrintPipelineArguments(cls, PipelineNode):
        pass

    @classmethod
    def Load(cls, PipelineXml, PipelineName=None):

        # PipelineData = Pipelines.CreateFromDOM(XMLDoc)

        SelectedPipeline = None
        XMLDoc = cls.LoadPipelineXML(PipelineXml)

        if PipelineName is None:
            PipelineManager.logger.warning("No pipeline name specified.")
            prettyoutput.Log("No pipeline name specified")
            cls.PrintPipelineEnumeration(XMLDoc)
            return None
        else:
            SelectedPipeline = XMLDoc.find("Pipeline[@Name='" + PipelineName + "']")
            if SelectedPipeline is None:
                PipelineManager.logger.critical("No pipeline found named " + PipelineName)
                prettyoutput.LogErr("No pipeline found named " + PipelineName)
                cls.PrintPipelineEnumeration(XMLDoc)
                return None

        return PipelineManager(pipelinesRoot=XMLDoc.getroot(), pipelineData=SelectedPipeline)

    @classmethod
    def RunPipeline(cls, PipelineXmlFile, PipelineName, args):

        # PipelineData = Pipelines.CreateFromDOM(XMLDoc)
        Pipeline = cls.Load(PipelineXmlFile, PipelineName)

        Pipeline.Execute(args)

    def GetArgParser(self, parser=None, IncludeGlobals=True):
        '''Create the complete argument parser for the pipeline
        :param parser:
        :param bool IncludeGlobals: Arguments common to all pipelines are included if this flag is set to True.  True by default.  False is used to create documentation
        '''

        if IncludeGlobals:
            parser = argparsexml.CreateOrExtendParserForArguments(self.PipelineRoot.findall('Arguments/Argument'),
                                                                  parser)

        parser = argparsexml.CreateOrExtendParserForArguments(self.PipelineData.findall('Arguments/Argument'), parser)

        PipelineManager._AddParserDescription(self.PipelineData, parser)

        return parser

    @classmethod
    def _AddParserDescription(cls, PipelineNode, parser):

        if PipelineNode is None:
            return

        # print str(PipelineNode.attrib)

        # parser.prog = PipelineNode.attrib['Name'];

        if 'Help' in PipelineNode.attrib:
            parser.help = PipelineNode.attrib['Help']

        if 'Description' in PipelineNode.attrib:
            parser.description = PipelineNode.attrib['Description']

        if 'Epilog' in PipelineNode.attrib:
            parser.epilog = PipelineNode.attrib['Epilog']

    @classmethod
    def __extractXPathFromNode(cls, PipelineNode, ArgSet):
        xpath = PipelineNode.attrib['XPath']
        xpath = ArgSet.SubstituteStringVariables(xpath)
        return xpath

    @classmethod
    def GetSearchRoot(cls, VolumeElem, PipelineNode, ArgSet):
        RootIterNodeName = PipelineNode.get('Root', None)
        RootForSearch = VolumeElem
        if RootIterNodeName is not None:
            if RootIterNodeName not in ArgSet.Variables:
                raise PipelineSearchRootNotFound(argname=RootIterNodeName, PipelineNode=PipelineNode,
                                                 VolumeElem=VolumeElem)

            RootForSearch = ArgSet.Variables[RootIterNodeName]
        return RootForSearch

    IndentLevel = 0

    def Execute(self, args):
        '''This executes the loaded pipeline on the specified volume of data.
           parser is an instance of the argparser class which should be 
           extended with any pipeline specific arguments args are the parameters from the command line'''

        # DOM = self.PipelineData.toDOM()
        # PipelineElement = DOM.firstChild
        ArgSet = ArgumentSet()

        PipelineElement = self.PipelineData

        prettyoutput.Log("Adding pipeline arguments")
        # parser = self.GetArgParser(parser)
        # (args, unused) = parser.parse_known_args(passedArgs)

        ArgSet.AddArguments(args)

        ArgSet.AddParameters(PipelineElement)

        # Load the Volume.XML file in the output directory
        self.VolumeTree = nornir_buildmanager.volumemanager.VolumeManager.Load(args.volumepath, Create=True)

        if self.VolumeTree is None:
            PipelineManager.logger.critical("Could not load or create volume.xml " + args.outputpath)
            prettyoutput.LogErr("Could not load or create volume.xml " + args.outputpath)
            sys.exit()

        # dargs = copy.deepcopy(defaultDargs)

        self.ExecuteChildPipelines(ArgSet, self.VolumeTree, PipelineElement)

        nornir_pools.WaitOnAllPools()

    def ExecuteChildPipelines(self, ArgSet, VolumeElem, PipelineNode):
        '''Run all of the child pipeline elements on the volume element'''

        PipelineManager.logger.info(PipelineManager.ToElementString(PipelineNode))
        # prettyoutput.Log(PipelineManager.ToElementString(PipelineNode))

        PipelinesRun = 0
        try:
            self.AddPipelineNodeVariable(PipelineNode, VolumeElem, ArgSet)

            for ChildNode in PipelineNode:
                try:
                    prettyoutput.IncreaseIndent()
                    self.ProcessStageElement(VolumeElem, ChildNode, ArgSet)
                    PipelinesRun += 1
                except PipelineSelectFailed as e:
                    if ArgSet.Arguments["debug"]:
                        PipelineManager.logger.info(str(e))
                    PipelineManager.logger.info("Select statement did not match.  Skipping to next iteration\n")
                    break
                except PipelineSearchFailed as e:
                    PipelineManager.logger.debug(str(e))
                    PipelineManager.logger.info("Search statement did not match.  Skipping to next iteration\n")
                    break
                except PipelineListIntersectionFailed as e:
                    PipelineManager.logger.info(
                        "Node attribute was not in the list of desired values.  Skipping to next iteration.\n" + e.message)
                    break
                except PipelineRegExSearchFailed as e:
                    PipelineManager.logger.info(
                        "Regular expression did not match.  Skipping to next iteration.\n" + str(e.attribValue))
                    break
                except PipelineError as e:
                    errStr = "Unexpected error, exiting pipeline\n" + str(e)
                    PipelineManager.logger.error(errStr)
                    prettyoutput.LogErr(errStr)
                    sys.exit()
                finally:
                    prettyoutput.DecreaseIndent()

        finally:
            self.RemovePipelineNodeVariable(ArgSet, PipelineNode)

        # To prevent later calls from being able to access variables from earlier steps be sure to remove the variable from the dargs
        return PipelinesRun

    def ProcessStageElement(self, VolumeElem, PipelineNode, ArgSet=None):

        outStr = PipelineManager.ToElementString(PipelineNode)
        # prettyoutput.CurseString('Section', outStr)

        # prettyoutput.Log("Processing Stage Element: " + outStr)

        # Copy dargs so we do not modify what the parent passed us
        # dargs = copy.copy(dargs)

        if PipelineNode.tag == 'Select':
            self.ProcessSelectNode(ArgSet, VolumeElem, PipelineNode)

        elif PipelineNode.tag == 'Iterate':
            self.ProcessIterateNode(ArgSet, VolumeElem, PipelineNode)

        elif PipelineNode.tag == 'RequireSetMembership':
            self.RequireSetMembership(ArgSet, VolumeElem, PipelineNode)

        elif PipelineNode.tag == 'RequireMatch':
            self.ProcessRequireMatchNode(ArgSet, VolumeElem, PipelineNode)

        elif PipelineNode.tag == 'PythonCall':
            self.ProcessPythonCall(ArgSet, VolumeElem, PipelineNode)

        elif PipelineNode.tag == 'Arguments':
            pass

        else:
            raise Exception("Unexpected element name in Pipeline.XML: " + PipelineNode.tag)

    @staticmethod
    def RequireSetMembership(ArgSet, VolumeElem, PipelineNode):
        '''If the attribute value is not present in the provided list the element is skipped.
           If the provided list is none we do not skip.'''

        RootForMatch = PipelineManager.GetSearchRoot(VolumeElem, PipelineNode, ArgSet)

        AttribName = PipelineNode.attrib.get("Attribute", "Name")
        AttribName = ArgSet.SubstituteStringVariables(AttribName)

        listVariable = PipelineNode.attrib.get("List", None)
        if listVariable is None:
            raise PipelineError(VolumeElem=VolumeElem,
                                PipelineNode=PipelineNode,
                                message="List attribute missing on <RequireSetMembership> node")

        (found, listOfValid) = ArgSet.TryGetSubstituteObject(listVariable)
        if not found:
            # No set to compare with.  We allow it.
            return
        elif listOfValid is None:
            # No set to compare with.  We allow it.
            return

        Attrib = getattr(RootForMatch, AttribName, None)
        if Attrib is None:
            raise PipelineArgumentNotFound(VolumeElem=VolumeElem,
                                           PipelineNode=PipelineNode,
                                           argname=AttribName)

        if Attrib not in listOfValid:
            raise PipelineListIntersectionFailed(VolumeElem=VolumeElem, PipelineNode=PipelineNode,
                                                 listOfValid=listOfValid, attribValue=Attrib)

        return

    @staticmethod
    def ProcessRequireMatchNode(ArgSet, VolumeElem, PipelineNode):
        '''If the regular expression does not match the attribute an exception is raised.
           This skips the current iteration of an enclosing <iterate> element'''

        RootForMatch = PipelineManager.GetSearchRoot(VolumeElem, PipelineNode, ArgSet)
        AttribName = PipelineNode.attrib.get("Attribute", "Name")
        AttribName = ArgSet.SubstituteStringVariables(AttribName)
        RegExStr = PipelineNode.attrib.get("RegEx", None)
        RegExStr = ArgSet.SubstituteStringVariables(RegExStr)

        if RegExStr is None:
            raise PipelineArgumentNotFound(VolumeElem=VolumeElem,
                                           PipelineNode=PipelineNode,
                                           argname="RegEx",
                                           message="Match node missing RegEx attribute")

        Attrib = RootForMatch.attrib.get(AttribName, None)
        if Attrib is None:
            raise PipelineArgumentNotFound(VolumeElem=VolumeElem,
                                           PipelineNode=PipelineNode,
                                           argname=AttribName)

        if RegExStr == '*':
            return

        match = re.match(RegExStr, Attrib)
        if match is None:
            raise PipelineRegExSearchFailed(VolumeElem=VolumeElem, PipelineNode=PipelineNode, regex=RegExStr,
                                            attribValue=Attrib)

        return

    def ProcessSelectNode(self, ArgSet, VolumeElem, PipelineNode):

        xpath = PipelineManager.__extractXPathFromNode(PipelineNode, ArgSet)

        RootForSearch = PipelineManager.GetSearchRoot(VolumeElem, PipelineNode, ArgSet)

        SelectedVolumeElem = None
        while SelectedVolumeElem is None:

            SelectedVolumeElem = RootForSearch.find(xpath)
            if SelectedVolumeElem is None:
                raise PipelineSelectFailed(PipelineNode=PipelineNode, VolumeElem=RootForSearch, xpath=xpath)

            # Containers will be tested at load time.  The load linked element code checks containers
            if not isinstance(SelectedVolumeElem,
                              nornir_buildmanager.volumemanager.xcontainerelementwrapper.XContainerElementWrapper):
                if SelectedVolumeElem.NeedsValidation:
                    (IsValid, Reason) = SelectedVolumeElem.IsValid()
                    if not IsValid:
                        # Check if the node is locked, otherwise clean it and look for another node
                        if 'Locked' in SelectedVolumeElem.attrib:
                            if SelectedVolumeElem.Locked:
                                PipelineManager.logger.info(
                                    "Did not clean locked element {0}\n".format(SelectedVolumeElem.FullPath))
                                break

                        SelectedVolumeElem.Clean(Reason)
                        PipelineManager._SaveNodes(SelectedVolumeElem.Parent)
                        SelectedVolumeElem = None

        if SelectedVolumeElem is not None:
            self.AddPipelineNodeVariable(PipelineNode, SelectedVolumeElem, ArgSet)

    def ProcessIterateNode(self, ArgSet, VolumeElem, PipelineNode):

        xpath = PipelineManager.__extractXPathFromNode(PipelineNode, ArgSet)

        RootForSearch = PipelineManager.GetSearchRoot(VolumeElem, PipelineNode, ArgSet)

        # TODO: Update args from the element
        VolumeElemIter = RootForSearch.findall(xpath)
        
        validate = True
        if 'Validate' in PipelineNode.attrib:
            validate_value = PipelineNode.attrib.get('Validate', 'True').lower()
            validate = validate_value == 'true' or validate_value == '1' or validate_value == 'y' or validate_value == 'yes'

        # Make sure downstream activities do not corrupt the dictionary for the caller
        CopiedArgSet = copy.copy(ArgSet)

        NumProcessed = 0
        for VolumeElemChild in VolumeElemIter:
            if validate and VolumeElem.NeedsValidation:
                (cleaned, reason) = VolumeElemChild.CleanIfInvalid()
                if cleaned:
                    prettyoutput.Log(f"Cleaned invalid element during search: {VolumeElemChild}\nReason: {reason}")
                    PipelineManager._SaveNodes(VolumeElemChild.Parent)
                    continue

            NumProcessed += self.ExecuteChildPipelines(CopiedArgSet, VolumeElemChild, PipelineNode)

        if NumProcessed == 0:
            raise PipelineSearchFailed(PipelineNode=PipelineNode, VolumeElem=RootForSearch, xpath=xpath)

    @classmethod
    def _SaveNodes(cls, NodesToSave):
        if NodesToSave is not None:
            if isinstance(NodesToSave, collections.abc.Iterable) or isgenerator(NodesToSave):
                for node in NodesToSave:
                    if node is None:
                        continue

                    nornir_buildmanager.volumemanager.volumemanager.VolumeManager.Save(node)
            else:
                nornir_buildmanager.volumemanager.volumemanager.VolumeManager.Save(NodesToSave)

    def ProcessPythonCall(self, ArgSet, VolumeElem, PipelineNode):
        # Try to find a stage for the element we encounter in the pipeline.
        PipelineModule = 'nornir_buildmanager.operations'  # This should match the default in the xsd file, but pyxb doesn't seem to emit the default valuef
        PipelineModule = PipelineNode.get("Module", "nornir_buildmanager.operations")

        PipelineFunction = PipelineNode.get('Function', PipelineNode.tag)

        stageFunc = nornir_shared.reflection.get_module_class(str(PipelineModule), str(PipelineFunction))

        if ArgSet.Arguments['verbose']:
            prettyoutput.Log("CALL " + str(PipelineModule) + "." + str(PipelineFunction))

        # PipelineManager.logger.info("CALL " + str(PipelineModule) + "." + str(PipelineFunction))

        if stageFunc is None:
            errorStr = "Stage implementation not found: " + str(PipelineModule) + "." + str(PipelineFunction)
            PipelineManager.logger.error(errorStr + ElementTree.tostring(PipelineNode, encoding='utf-8'))
            raise PipelineError(VolumeElem=VolumeElem, PipelineNode=PipelineNode, message=errorStr)
        else:
            # prettyoutput.CurseString('Stage', PipelineModule + "." + PipelineFunction)

            # TODO: Update args from the element

            # Update dargs with the attributes

            ArgSet.AddAttributes(PipelineNode)
            ArgSet.AddParameters(PipelineNode)

            try:
                # PipelineManager.AddAttributes(dargs, PipelineNode)

                # Check for parameters under the function node and load them into the dictionary
                # PipelineManager.AddParameters(dargs, PipelineNode, dargsKeyname='Parameters')

                kwargs = ArgSet.KeyWordArgs()

                kwargs["Logger"] = PipelineManager.logger.getChild(PipelineFunction)
                # kwargs["CallElement"] = PipelineNode
                kwargs["VolumeElement"] = VolumeElem
                kwargs["VolumeNode"] = self.VolumeTree

                # Add an empty dictionary if no parameters set
                if 'Parameters' not in kwargs:
                    kwargs['Parameters'] = {}

                NodesToSave = None

                if not ArgSet.Arguments["debug"]:
                    try:
                        NodesToSave = stageFunc(**kwargs)
                    except:
                        errorStr = '\n' + '-' * 60 + '\n'
                        errorStr = errorStr + str(PipelineModule) + '.' + str(PipelineFunction) + " Exception\n"
                        errorStr = errorStr + '-' * 60 + '\n'
                        errorStr = errorStr + traceback.format_exc()
                        errorStr = errorStr + '-' * 60 + '\n'
                        PipelineManager.logger.error(errorStr)
                        # prettyoutput.LogErr(errorStr)

                        self.VolumeTree = nornir_buildmanager.volumemanager.volumemanager.VolumeManager.Load(
                            self.VolumeTree.attrib["Path"], UseCache=False)
                        return


                else:
                    # In debug mode we do not want to catch any exceptions
                    # stage functions can return None,True, or False to indicate they did work.
                    # if they return false we do not need to run the expensive save operation
                    print(str(PipelineModule) + '.' + str(PipelineFunction))

                    NodesToSave = stageFunc(**kwargs)

                PipelineManager._SaveNodes(NodesToSave)

            finally:
                ArgSet.ClearAttributes()
                ArgSet.ClearParameters()

            # prettyoutput.CurseString('Stage', PipelineModule + "." + PipelineFunction + " completed")

    #           PipelineManager.RemoveParameters(dargs, PipelineNode)
    #           PipelineManager.RemoveAttributes(dargs, PipelineNode)

    @staticmethod
    def AddPipelineNodeVariable(PipelineNode, VolumeElem, ArgSet):
        '''Adds a variable to our dictionary passed to functions'''
        if 'VariableName' in PipelineNode.attrib:
            key = PipelineNode.attrib['VariableName']

            # if key in ArgSet.Variables:
            # raise PipelineError(PipelineNode=PipelineNode, VolumeElem=VolumeElem, message=str(key) + " is a duplicate variable name")

            ArgSet.AddVariable(key, VolumeElem)

            outStr = VolumeElem.ToElementString()
            # if(self.Parameters['verbose']):
            # prettyoutput.Log(PipelineNode.attrib['VariableName'] + " = " + outStr)

            PipelineManager.logger.info(PipelineNode.attrib['VariableName'] + " = " + outStr)

        elif PipelineNode.tag == "Select":
            raise PipelineError(PipelineNode=PipelineNode, message="VariableName attribute required on Select Element")

    @staticmethod
    def RemovePipelineNodeVariable(ArgSet, PipelineNode):
        '''Adds a variable to our dictionary passed to functions'''
        if 'VariableName' in PipelineNode.attrib:
            del ArgSet.Variables[PipelineNode.attrib['VariableName']]


def _GetVariableName(PipelineNode):
    if 'VariableName' in PipelineNode.attrib:
        return PipelineNode.attrib['VariableName']
        # self.Variables[PipelineNode.attrib['VariableName']] = VolumeElem

        # outStr = VolumeElem.ToElementString()
        # if(self.Parameters['verbose']):
        # prettyoutput.Log(PipelineNode.attrib['VariableName'] + " = " + outStr)
        # PipelineManager.logger.info(PipelineNode.attrib['VariableName'] + " = " + outStr)
    elif PipelineNode.tag == "Select":
        raise PipelineError(PipelineNode=PipelineNode, message="Variable name attribute required on Select Element")


if __name__ == "__main__":
    XmlFilename = 'D:\Buildscript\Pipelines.xml'
    PipelineManager.Load(XmlFilename)
