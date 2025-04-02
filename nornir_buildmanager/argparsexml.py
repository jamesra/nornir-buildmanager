"""

Converts XML argparser definitions from pipelines and importers into argparsers.

"""

import argparse
import copy
import enum
import logging
from typing import Type

from nornir_shared.argparse_helpers import *
import nornir_shared.misc
import nornir_shared.prettyoutput as prettyoutput


def _ConvertValueToPythonType(val: str, type: Type | None = None) -> bool | int | float | str:
    if not isinstance(val, str):
        return val  # Already converted to a python type
    elif type is not None and isinstance(type, enum.EnumType):
        return type[val]
    elif val.lower() == 'true':
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
    """Returns a dictionary that can be added to a parser"""

    attribDictCopy = copy.copy(argNode.attrib)
    Flag = ""

    keys = list(attribDictCopy.keys())

    found_type = None  # The type, if we have determined it.  Needed to detect enums.

    for key in keys:
        val = attribDictCopy[key]

        # Starts as a string, try to convert to bool, int, or float
        if key == 'flag':
            Flag = nornir_shared.misc.ListFromDelimited(val)
            continue

        elif key == 'type':
            if val in __builtins__:
                found_type = __builtins__[val]
            elif val in globals():
                found_type = globals()[val]
            elif '.' in val:  # Try to import the package in case it is an enum
                try:
                    package, typename = val.rsplit('.', maxsplit=1)
                    import importlib
                    module = importlib.import_module(package)
                    found_type = getattr(module, typename)
                    globals()[package] = module

                except:
                    logger = logging.getLogger(__name__ + "._AddArgumentNodeToParser")
                    logger.error(f'Failed to import {val}')
                    prettyoutput.LogErr(f'Failed to import {val}')
                    raise
            else:
                logger = logging.getLogger(__name__ + "._AddArgumentNodeToParser")
                error_str = f'Type {val} not found in __builtins__ or module __dict__'
                logger.error(error_str)
                prettyoutput.LogErr(error_str)
                raise

            attribDictCopy[key] = found_type

            # If the type is an Enum, add the choices to the argument automatically
            if isinstance(found_type, enum.EnumType):
                attribDictCopy['choices'] = list(found_type)  # [e.name for e in found_type]
                if 'default' in attribDictCopy:
                    try:
                        # Convert the default to the matching enum value in case we've already populated the default value
                        attribDictCopy['default'] = _ConvertValueToPythonType(attribDictCopy['default'], found_type)
                    except:
                        logger = logging.getLogger(__name__ + "._AddArgumentNodeToParser")
                        error_str = f'Failed to convert default value {attribDictCopy["default"]} to enum type {found_type}'
                        logger.error(error_str)
                        prettyoutput.LogErr(error_str)
                        raise
        elif key == 'default':
            attribDictCopy[key] = _ConvertValueToPythonType(val, found_type)
        elif key == 'required':
            attribDictCopy[key] = _ConvertValueToPythonType(val)
        elif key == 'choices':
            listOfChoices = nornir_shared.misc.ListFromDelimited(val)
            if len(listOfChoices) < 2:
                raise Exception(f"Flag {attribDictCopy['flag']} does not specify multiple choices.  Must use "
                                f"a comma delimited list to provide multiple choice options.\nCurrent choice "
                                f"string is: {val}")

            attribDictCopy[key] = listOfChoices

    if 'flag' in attribDictCopy:
        del attribDictCopy['flag']

    parser.add_argument(*Flag, **attribDictCopy)


def CreateOrExtendParserForArguments(ArgumentNodes, parser: argparse.ArgumentParser | None = None):
    """
       Converts a list of <Argument> nodes into an argument parser, or extends an existing argument parser
       :param XElement ArgumentNodes: Argument nodes.  Usually found with the xquery "Arguments/Argument" on the pipeline node
       :param argparse.ArgumentParser parser: Existing Argument parser to extend, otherwise a new parser is created
       :returns: An argument parser
       :rtype: argparse.ArgumentParser
    """

    if parser is None:
        parser = argparse.ArgumentParser()

    for argNode in ArgumentNodes:
        _AddArgumentNodeToParser(parser, argNode)

    return parser
