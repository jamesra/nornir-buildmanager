'''

Converts XML argparser definitions from pipelines and importers into argparsers. 

'''

import argparse
import logging
import copy
import nornir_shared.misc
import nornir_shared.prettyoutput as prettyoutput
import re

from nornir_shared.argparse_helpers import *

def _ConvertValueToPythonType(val):
    if val.lower() == 'true':
        return True
    elif val.lower() == 'false':
        return False
    elif '.' in val:
        try:
            return float(val)
        except:
            pass
    else:
        try:
            return int(val)
        except:
            pass

    return val


def _AddArgumentNodeToParser(parser, argNode):
    '''Returns a dictionary that can be added to a parser'''

    attribDictCopy = copy.copy(argNode.attrib)
    Flag = ""

    for key in attribDictCopy:
        val = attribDictCopy[key]

        # Starts as a string, try to convert to bool, int, or float
        if key == 'flag':
            Flag = nornir_shared.misc.ListFromDelimited(val)
            continue

        elif key == 'type':
            if val in __builtins__:
                val = __builtins__[val]
            elif val in globals():
                val = globals()[val]
            else:
                logger = logging.getLogger(__name__ + "._AddArgumentNodeToParser")
                logger.error('Type not found in __builtins__ or module __dict__' + val)
                prettyoutput.LogErr('Type not found in __builtins__ or module __dict__ ' + val)
                raise Exception(message="%s type specified by argument node is not present in __builtins__ or module dictionary.  Must use a standard python type." % val)
                continue

            attribDictCopy[key] = val
        elif key == 'default':
            attribDictCopy[key] = _ConvertValueToPythonType(val)
        elif key == 'required':
            attribDictCopy[key] = _ConvertValueToPythonType(val)
        elif key == 'choices':
            listOfChoices = nornir_shared.misc.ListFromDelimited(val)
            if len(listOfChoices) < 2:
                raise Exception(message="Flag %s does not specify multiple choices.  Must use a comma delimited list to provide multiple choice options.\nCurrent choice string is: %s" % (attribDictCopy['flag'], val))

            attribDictCopy[key] = listOfChoices

    if 'flag' in attribDictCopy:
        del attribDictCopy['flag']

    parser.add_argument(*Flag, **attribDictCopy)


def CreateOrExtendParserForArguments(ArgumentNodes, parser=None):
    '''
       Converts a list of <Argument> nodes into an argument parser, or extends an existing argument parser
       :param XElement ArgumentNodes: Argument nodes.  Usually found with the xquery "Arguments/Argument" on the pipeline node
       :param argparse.ArgumentParser parser: Existing Argument parser to extend, otherwise a new parser is created
       :returns: An argument parser
       :rtype: argparse.ArgumentParser
    '''

    if parser is None:
        parser = argparse.ArgumentParser()

    for argNode in ArgumentNodes:
        _AddArgumentNodeToParser(parser, argNode)

    return parser
