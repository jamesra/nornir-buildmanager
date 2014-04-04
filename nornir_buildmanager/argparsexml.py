'''

Converts XML argparser definitions from pipelines and importers into argparsers. 

'''

import argparse
import logging
import copy
import nornir_shared.misc
import nornir_shared.prettyoutput as prettyoutput
import re


def _IsNumberRange(argstr):
    '''Return true if the string has a hypen with two numbers between'''
    match = re.match(r'\d+\-\d+', argstr)
    return match

def _NumberRangeToList(argstr):
    '''
    :param argstr: Pair of number seperated by a hyphen defining a range, inclusive.  Example: 1-3 = [1,2,3]
    '''

    numbers = []
    try:
        (start, delimiter, end) = argstr.partition('-')
        start_int = int(start)
        end_int = int(end)

        numbers = range(start_int, end_int + 1)

    except ValueError as ve:
        raise argparse.ArgumentTypeError()

    return numbers

def NumberList(argstr):
    '''Return a list of numbers based on a range defined by a string 
       :param argstr:  A string defining a list of numbers.  Commas seperate values and hyphens define ranges.  Ex: 1, 3, 5-8, 11 = [1,3,5,6,7,8,11]
       :rtype: List of integers
    '''

    listNums = []
    argstr = argstr.replace(' ', '')

    for entry in argstr.strip().split(','):
        entry = entry.strip()

        if(_IsNumberRange(entry)):
            addedIntRange = _NumberRangeToList(entry)
            listNums.extend(addedIntRange)
        else:
            try:
                val = int(entry)
                listNums.append(val)
            except ValueError as ve:
                raise argparse.ArgumentTypeError()




    return listNums


def _ConvertValueToPythonType(val):
    if val.lower() == 'true':
        return True
    elif val.lower() == 'false':
        return False
    else:
        try:
            return int(val)
        except:
            try:
                return float(val)
            except:
                pass

    return val


def _AddArgumentNodeToParser(parser, argNode):
    '''Returns a dictionary that can be added to a parser'''

    attribDictCopy = copy.deepcopy(argNode.attrib)
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
                logger = logging.getLogger("PipelineManager")
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
