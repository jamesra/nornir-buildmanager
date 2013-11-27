'''
Created on Apr 2, 2012

@author: James Anderson
'''

import copy
import logging
import os
import re
import sys
import traceback
import xml.etree

from nornir_buildmanager import VolumeManagerETree
import nornir_shared.prettyoutput as prettyoutput
import nornir_shared.reflection


# from nornir_buildmanager.Data import Volumes
# from nornir_buildmanager.Data import Pipelines
class PipelineError(Exception):
    '''An expected node did not exist'''

    def __init__(self, VolumeElem, PipelineNode, message=None, **kwargs):
        super(PipelineError, self).__init__(**kwargs)

        self.PipelineNode = PipelineNode
        self.VolumeElem = VolumeElem
        self.message = message

    @property
    def __ErrorHeader(self):
        return "*"*80

    @property
    def __ErrorFooter(self):
        return "*"*80

    def __CoreErrorList(self):
        '''return a list of error strings'''
        s = []
        s.append("Pipeline Element: " + xml.etree.ElementTree.tostring(self.PipelineNode, encoding='utf-8') + '\n')
        s.append("Volume Element: " + xml.etree.ElementTree.tostring(self.VolumeElem, encoding='utf-8') + '\n');
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
        self.PipelineData = pipelineData
        self.defaultArgs = dict()
        self.PipelineRoot = pipelinesRoot 
        
    @classmethod
    def ToElementString(self, element):
        strList = xml.etree.ElementTree.tostringlist(element)

        if element.tag == 'IterateVolumeElements':
            outStr = element.attrib['XPath']
            return outStr

        outStr = ""
        for s in strList:
            outStr = outStr + " " + s.decode('Utf-8')
            if s == '>':
                break

        return outStr

    @classmethod
    def Load(cls, PipelineXmlFile, PipelineName=None):

        if not os.path.exists(PipelineXmlFile):
            PipelineManager.logger.critical("Provided pipeline filename does not exist: " + PipelineXmlFile)
            prettyoutput.Log("Provided pipeline filename does not exist: " + PipelineXmlFile)
            sys.exit()
            return None

        print PipelineXmlFile

        XMLDoc = xml.etree.ElementTree.parse(PipelineXmlFile)
        # PipelineData = Pipelines.CreateFromDOM(XMLDoc)

        SelectedPipeline = None
        cls.logger.info("Enumerating available pipelines")
        prettyoutput.Log("Enumerating available pipelines")
        for pipeline in XMLDoc.getroot():
            cls.logger.info('  ' + pipeline.attrib.get('Name', ""))
            prettyoutput.Log('  ' + pipeline.attrib.get('Name', ""))
            prettyoutput.Log('    ' + pipeline.attrib.get('Description', ""))

        if PipelineName is None:
            SelectedPipeline = XMLDoc.getroot()[0]
        else:
            SelectedPipeline = XMLDoc.find("Pipeline[@Name='" + PipelineName + "']")
            if(SelectedPipeline is None):
                PipelineManager.logger.critical("No pipeline found named " + PipelineName)
                prettyoutput.LogErr("No pipeline found named " + PipelineName)
                return None

        if(PipelineName is None):
            NodeName = SelectedPipeline.get('Name', None)
            PipelineManager.logger.warning("No pipeline name specified.  Choosing " + NodeName)
            prettyoutput.Log("No pipeline name specified.  Choosing " + NodeName)

        return PipelineManager(pipelinesRoot=XMLDoc.getroot(), pipelineData=SelectedPipeline)


    def Execute(self, parser, passedArgs):
        '''This executes the loaded pipeline on the specified volume of data.
           parser is an instance of the argparser class which should be 
           extended with any pipeline specific arguments args are the parameters from the command line'''

        # DOM = self.PipelineData.toDOM()
        # PipelineElement = DOM.firstChild
        PipelineElement = self.PipelineData

        defaultDargs = dict()
        defaultDargs.update(self.defaultArgs)

        PipelineManager.AddArguments(parser, self.PipelineRoot)
        PipelineManager.AddAttributes(defaultDargs, PipelineElement)
        PipelineManager.AddParameters(defaultDargs, PipelineElement)

        PipelineManager.AddArguments(parser, PipelineElement)

        (args, unused) = parser.parse_known_args(passedArgs)

        # Load the Volume.XML file in the output directory
        self.VolumeTree = VolumeManagerETree.VolumeManager.Load(args.volumepath, Create=True)

        if(self.VolumeTree is None):
            PipelineManager.logger.critical("Could not load or create volume.xml " + args.outputpath)
            prettyoutput.LogErr("Could not load or create volume.xml " + args.outputpath)
            sys.exit()

        defaultDargs.update(args.__dict__)

        # dargs = copy.deepcopy(defaultDargs)
        
        self.ExecuteChildPipelines(defaultDargs, self.VolumeTree, PipelineElement)


    def AddVariable(self, PipelineNode, VolumeElem, dargs):
        '''Adds a variable to our dictionary passed to functions'''
        if 'VariableName' in PipelineNode.attrib:
            dargs[PipelineNode.attrib['VariableName']] = VolumeElem

            outStr = VolumeElem.ToElementString()
            if(dargs['verbose']):
                prettyoutput.Log(PipelineNode.attrib['VariableName'] + " = " + outStr)
            PipelineManager.logger.info(PipelineNode.attrib['VariableName'] + " = " + outStr)
        elif PipelineNode.tag == "Select":
            PipelineManager.logger.error("Variable name attribute required on Select Element")

    def RemoveVariable(self, PipelineNode, dargs):
        '''Adds a variable to our dictionary passed to functions'''
        if 'VariableName' in PipelineNode.attrib:
            del dargs[PipelineNode.attrib['VariableName']]

    @classmethod
    def __extractXPathFromNode(cls, PipelineNode, dargs):
        xpath = PipelineNode.attrib['XPath']
        xpath = PipelineManager.SubstituteStringVariables(xpath, dargs)
        return xpath

    @classmethod
    def GetSearchRoot(cls, VolumeElem, PipelineNode, dargs):
        RootIterNodeName = PipelineNode.get('Root', None)
        RootForSearch = VolumeElem
        if(not RootIterNodeName is None):
            RootForSearch = dargs[RootIterNodeName]
            if not RootIterNodeName in dargs:
                raise PipelineSearchRootNotFound(argname=RootIterNodeName, PipelineNode=PipelineNode, VolumeElem=VolumeElem)
#                PipelineManager.logger.error("*"*80)
#                PipelineManager.logger.error("Rootname specified in nornir_buildmanager is not available: " + RootIterNodeName + "\n" + str(PipelineNode))
#                PipelineManager.logger.error("nornir_buildmanager Element: " + xml.etree.ElementTree.tostring(PipelineNode, encoding = 'utf-8'))
#                PipelineManager.logger.error("VolumeElement: " + xml.etree.ElementTree.tostring(VolumeElem, encoding = 'utf-8'))
#                PipelineManager.logger.error("*"*80)
#                sys.exit()

        return RootForSearch


    IndentLevel = 0
    
    def ExecuteChildPipelines(self, dargs, VolumeElem, PipelineNode):
        '''Run all of the child pipeline elements on the volume element'''
    
    
        PipelineManager.logger.info(PipelineManager.ToElementString(PipelineNode))
        #prettyoutput.Log(PipelineManager.ToElementString(PipelineNode))
        
        PipelinesRun = 0
        try:
            self.AddVariable(PipelineNode, VolumeElem, dargs)
            
            for ChildNode in PipelineNode:
                
                
                try:
                    self.ProcessStageElement(VolumeElem, ChildNode, dargs)
                    PipelinesRun += 1
                except PipelineSelectFailed as e:
                    PipelineManager.logger.error(str(e))
                    PipelineManager.logger.info("Select statement did not match.  Skipping further iteration and continuing")
                    break
                except PipelineSearchFailed as e:
                    PipelineManager.logger.error(str(e))
                    PipelineManager.logger.info("Search statement did not match.  Skipping further iteration and continuing")
                    break
                except PipelineRegExSearchFailed as e:
                    PipelineManager.logger.info("Regular expression did not match.  Skipping further iteration. " + str(e.attribValue))
                    break
                except PipelineError as e:
                    PipelineManager.logger.error(str(e))
                    PipelineManager.logger.error("Undexpected error, exiting pipeline")
                    sys.exit()
        finally: 
            self.RemoveVariable(PipelineNode, dargs)
                
        # To prevent later calls from being able to access variables from earlier steps be sure to remove the variable from the dargs
        return PipelinesRun
        
     
    
    
    def ProcessStageElement(self, VolumeElem, PipelineNode, dargs=None):

        outStr = PipelineManager.ToElementString(PipelineNode)
        prettyoutput.CurseString('Section', outStr)

        prettyoutput.IncreaseIndent()
        # prettyoutput.Log("Processing Stage Element: " + outStr)

        # Copy dargs so we do not modify what the parent passed us
        # dargs = copy.copy(dargs)


        if(PipelineNode.tag == 'Select'):
            self.ProcessSelectNode(dargs, VolumeElem, PipelineNode)

        elif(PipelineNode.tag == 'Iterate'):
            self.ProcessIterateNode(dargs, VolumeElem, PipelineNode)

        elif(PipelineNode.tag == 'RequireMatch'):
            self.ProcessRequireMatchNode(dargs, VolumeElem, PipelineNode)

        elif PipelineNode.tag == 'PythonCall':
            self.ProcessPythonCall(dargs, VolumeElem, PipelineNode)

        elif PipelineNode.tag == 'Arguments':
            pass

        else:
            raise Exception("Unexpected element name in Pipeline.XML: " + PipelineNode.tag)

        prettyoutput.DecreaseIndent()

    def ProcessRequireMatchNode(self, dargs, VolumeElem, PipelineNode):
        '''If the regular expression does not match the attribute an exception is raised.
           This skips the current iteration of an enclosing <iterate> element'''

        RootForMatch = PipelineManager.GetSearchRoot(VolumeElem, PipelineNode, dargs)
        AttribName = PipelineNode.attrib.get("Attribute", "Name")
        AttribName = PipelineManager.SubstituteStringVariables(AttribName, dargs)
        RegExStr = PipelineNode.attrib.get("RegEx", None)
        RegExStr = PipelineManager.SubstituteStringVariables(RegExStr, dargs)

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
            raise PipelineRegExSearchFailed(VolumeElem=VolumeElem, PipelineNode=PipelineNode, regex=RegExStr, attribValue=Attrib)

        return

    def ProcessSelectNode(self, dargs, VolumeElem, PipelineNode):

        xpath = PipelineManager.__extractXPathFromNode(PipelineNode, dargs)

        RootForSearch = PipelineManager.GetSearchRoot(VolumeElem, PipelineNode, dargs)

        SelectedVolumeElem = None
        while SelectedVolumeElem is None:

            SelectedVolumeElem = RootForSearch.find(xpath)
            if(SelectedVolumeElem is None):
                raise PipelineSelectFailed(PipelineNode=PipelineNode, VolumeElem=RootForSearch, xpath=xpath)

            if SelectedVolumeElem.CleanIfInvalid():
                SelectedVolumeElem = None

        if not SelectedVolumeElem is None:
            self.AddVariable(PipelineNode, SelectedVolumeElem, dargs)
            
    
            
    def ProcessIterateNode(self, dargs, VolumeElem, PipelineNode):

        xpath = PipelineManager.__extractXPathFromNode(PipelineNode, dargs)

        RootForSearch = PipelineManager.GetSearchRoot(VolumeElem, PipelineNode, dargs)

        # TODO: Update args from the element
        VolumeElemIter = RootForSearch.findall(xpath)

        # Make sure downstream activities do not corrupt the dictionary for the caller
        dargs = copy.copy(dargs)

        NumProcessed = 0
        for VolumeElemChild in VolumeElemIter:

            if VolumeElemChild.CleanIfInvalid():
                continue
            
            NumProcessed += self.ExecuteChildPipelines(dargs, VolumeElemChild, PipelineNode)

        if(NumProcessed == 0):
            raise PipelineSearchFailed(PipelineNode=PipelineNode, VolumeElem=RootForSearch, xpath=xpath)

    def ProcessPythonCall(self, dargs, VolumeElem, PipelineNode):
        # Try to find a stage for the element we encounter in the pipeline.
        PipelineModule = 'nornir_buildmanager.operations'  # This should match the default in the xsd file, but pyxb doesn't seem to emit the default valuef
        PipelineModule = PipelineNode.get("Module", "nornir_buildmanager.operations")

        PipelineFunction = PipelineNode.get('Function', PipelineNode.tag)

        stageFunc = nornir_shared.reflection.get_module_class(str(PipelineModule), str(PipelineFunction))

        if(dargs['verbose']):
            prettyoutput.Log("CALL " + str(PipelineModule) + "." + str(PipelineFunction))

        PipelineManager.logger.info("CALL " + str(PipelineModule) + "." + str(PipelineFunction))

        if stageFunc is None:
            errorStr = "Stage implementation not found: " + str(PipelineModule) + "." + str(PipelineFunction)
            PipelineManager.logger.error(errorStr + xml.etree.ElementTree.tostring(PipelineNode, encoding='utf-8'))
            raise PipelineError(VolumeElem=VolumeElem, PipelineNode=PipelineNode, message=errorStr)
        else:
            prettyoutput.CurseString('Stage', PipelineModule + "." + PipelineFunction)

            # TODO: Update args from the element

            # Update dargs with the attributes

            PipelineManager.AddAttributes(dargs, PipelineNode)

            # Check for parameters under the function node and load them into the dictionary
            PipelineManager.AddParameters(dargs, PipelineNode, dargsKeyname='Parameters')

            dargs["Logger"] = PipelineManager.logger.getChild(PipelineFunction)
            dargs["CallElement"] = PipelineNode
            dargs["VolumeElement"] = VolumeElem
            dargs["VolumeNode"] = self.VolumeTree

            # Add an empty dictionary if no parameters set
            if 'Parameters' not in dargs:
                dargs['Parameters'] = {}

            NodesToSave = None

            if not dargs["debug"]:
                try:
                    NodesToSave = stageFunc(**dargs)
                except:
                    errorStr = '\n' + '-' * 60 + '\n'
                    errorStr = errorStr + str(PipelineModule) + '.' + str(PipelineFunction) + " Exception\n"
                    errorStr = errorStr + '-' * 60 + '\n'
                    errorStr = errorStr + traceback.format_exc()
                    errorStr = errorStr + '-' * 60 + '\n'
                    PipelineManager.logger.error(errorStr)
                    prettyoutput.LogErr(errorStr)

                    self.VolumeTree = VolumeManagerETree.VolumeManager.Load(self.VolumeTree.attrib["Path"], UseCache=False)

                    # Continue so we do not write an updated XML file
                    prettyoutput.DecreaseIndent()
                    return
            else:
                # In debug mode we do not want to catch any exceptions
                # stage functions can return None,True, or False to indicate they did work.
                # if they return false we do not need to run the expensive save operation
                NodesToSave = stageFunc(**dargs)

            if not NodesToSave is None:
                if isinstance(NodesToSave, list):
                    for node in NodesToSave:
                        VolumeManagerETree.VolumeManager.Save(dargs["volumepath"], node)
                else:
                    VolumeManagerETree.VolumeManager.Save(dargs["volumepath"], NodesToSave)

            del dargs["Logger"]
            del dargs["CallElement"]
            del dargs["VolumeElement"]
            del dargs["VolumeNode"]
            del dargs["Parameters"]

            PipelineManager.RemoveParameters(dargs, PipelineNode)
            PipelineManager.RemoveAttributes(dargs, PipelineNode)

    @classmethod
    def RemoveAttributes(cls, dargs, Node):
        # print "Remove Attributes"

        for key in Node.attrib:
            if key in dargs.keys():

                val = Node.attrib[key]
                # If we assign a variable to an attribute with the same name to ourselves do not overwrite
                if val[0] == '#':
                    if val[1:] == key:
                        continue

                # print "Removing " + key
                del dargs[key]

    @classmethod
    def AddAttributes(cls, dargs, Node):
        '''Add attributes from an element node to the dargs dictionary.
           Parameter values preceeded by a '#' are keys to existing
           entries in the dictionary whose values are copied to the 
           entry for the attribute'''

        for key in Node.attrib:
            val = Node.attrib[key]

            if len(val) == 0:
                dargs[key] = val
                continue

            subObj = cls.TryGetSubstituteObject(val, dargs)
            if not subObj is None:
                dargs[key] = subObj
                continue

            val = cls.SubstituteStringVariables(val, dargs)

            dargs[key] = val
            try:
                dargs[key] = int(val)
                continue
            except:
                pass

            try:
                dargs[key] = float(val)
                continue
            except:
                pass

    @classmethod
    def RemoveParameters(cls, dargs, Node):
        '''Remove entries from a dargs dictionary.'''

        # print "Remove Parameters"

        ParamNodes = Node.findall('Parameters')
        Parameters = {}

        for PN in ParamNodes:
            for entryNode in PN:
                 if entryNode.tag == 'Entry':
                    name = entryNode.attrib['Name']
                    if not 'Value' in entryNode.attrib:
                        continue

                    val = entryNode.attrib['Value']
                    if name in dargs.keys():
                        if val[0] == '#':
                            if val[1:] == name:
                                continue

                        # print "Removing " + name
                        del dargs[name]

    @classmethod
    def AddParameters(cls, dargs, Node, dargsKeyname=None):
        '''Add entries from a dictionary node to the dargs dictionary.
           Parameter values preceeded by a '#' are keys to existing
           entries in the dictionary whose values are copied to the 
           entry for the attribute. 
           If dargsKeyname is none all parameters are added directly 
           to dargs dictionary.  Otherwise they are added as a dictionary
           under a key=dargsKeyname'''

        ParamNodes = Node.findall('Parameters')
        Parameters = {}

        for PN in ParamNodes:
            for entryNode in PN:
                if entryNode.tag == 'Entry':
                    name = entryNode.attrib['Name']
                    val = entryNode.attrib.get('Value', '')

                    subObj = cls.TryGetSubstituteObject(val, dargs)
                    if not subObj is None:
                        Parameters[name] = subObj
                        continue

                    val = cls.SubstituteStringVariables(val, dargs)

                    if len(val) == 0:
                        Parameters[name] = val
                        continue

                    Parameters[name] = val
                    try:
                        Parameters[name] = int(val)
                        continue
                    except:
                        pass

                    try:
                        Parameters[name] = float(val)
                        continue
                    except:
                        pass

        if dargsKeyname is None:
            dargs.update(Parameters)
        else:
            dargs[dargsKeyname] = Parameters

    @classmethod
    def AddArguments(cls, parser, Node):

        ArgumentNodes = Node.findall('Arguments/Argument')

        prettyoutput.Log("Adding pipeline arguments")

        for argNode in ArgumentNodes:
            attribDictCopy = copy.deepcopy(argNode.attrib)
            Flag = ""

            for key in attribDictCopy:
                val = attribDictCopy[key]
                # Starts as a string, try to convert to bool, int, or float
                if key == 'flag':
                    Flag = val
                    continue


                if key == 'type':
                    if not key in __builtins__:
                        logger = logging.getLogger("PipelineManager")
                        logger.error('Type not found in __builtins__ ' + key)
                        prettyoutput.LogErr('Type not found in __builtins__ ' + key)
                        continue

                    val = __builtins__[val]
                    attribDictCopy[key] = val
                elif val == 'True':
                    attribDictCopy[key] = True
                elif val == 'False':
                    attribDictCopy[key] = False
                else:
                    try:
                        attribDictCopy[key] = int(val)
                    except:
                        try:
                            attribDictCopy[key] = float(val)
                        except:
                            pass

            if 'flag' in attribDictCopy:
                del attribDictCopy['flag']


            parser.add_argument(Flag, **attribDictCopy)
            helpstr = attribDictCopy.get('help', '')
            typestr = attribDictCopy.get('type', 'string')
            prettyoutput.Log('\t' + Flag + " [" + str(typestr) + "], " + helpstr)


    @classmethod
    def ReplaceVariable(cls, xpath, dargs, iStart):
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

            if keyCheck in dargs:
                value = dargs[keyCheck]
                xpath = xpath[:iStart - 1] + str(value) + xpath[iEndVar:]
                return xpath

            iEndVar = iEndVar - 1

        logger = logging.getLogger("PipelineManager")
        logger.error("nornir_buildmanager XPath variable not defined.\nXPath: " + xpath)
        prettyoutput.LogErr("nornir_buildmanager XPath variable not defined.\nXPath: " + xpath)
        sys.exit()

    @classmethod
    def SubstituteStringVariables(cls, xpath, dargs):
        '''Replace all instances of # in a string with the variable names'''

        iStart = xpath.find("#")
        while iStart >= 0:
            xpath = cls.ReplaceVariable(xpath, dargs, iStart)

            iStart = xpath.find("#")

        return xpath

    @classmethod
    def TryGetSubstituteObject(cls, val, dargs):
        '''Place an object directly into a dictionary, return true on success'''

        if val is None:
            return None

        if len(val) == 0:
            return None

        if val[0] == '#':
            if val[1:].find('#') >= 0:
                # If there are two '#' signs in the string is is a string not an object
                return None

            # Find the existing entry in the dargs
            key = val[1:]
            if not key in dargs:
                raise Exception("Key not found in dargs: " + key)

            return dargs[key]

        return None


if __name__ == "__main__":

    XmlFilename = 'D:\Buildscript\Pipelines.xml'
    PipelineManager.Load(XmlFilename)


