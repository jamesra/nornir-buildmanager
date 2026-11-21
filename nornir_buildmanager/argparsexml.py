"""

Converts XML argparser definitions from pipelines and importers into argparsers.

"""

import argparse
import builtins
import copy
import enum
import logging
import re
from typing import Type

from nornir_shared.argparse_helpers import *
import nornir_shared.misc
import nornir_shared.prettyoutput as prettyoutput


_ARGPARSE_HELP_PERCENT_PATTERN = re.compile(r'(?<!%)%(?!\(|%)')


def _EscapeLiteralPercentInHelp(help_string: str) -> str:
    """Escape literal percent signs while preserving argparse placeholders.

    Argparse help strings use %-formatting with mappings (for example `%(default)s`).
    A literal `%` in XML help text (for example `99%`) must be escaped as `%%`.
    """

    return _ARGPARSE_HELP_PERCENT_PATTERN.sub('%%', help_string)


def _ConvertValueToPythonType(val: str, type: Type | None = None) -> bool | int | float | str:
    if not isinstance(val, str):
        return val  # Already converted to a python type
    elif type is not None and isinstance(type, enum.EnumType):
        return type[val]  # type: ignore[return-value]
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


class _EnumNameParser:
    """argparse ``type`` for an Enum: accepts a member name (any case), optionally as ``EnumType.Name``."""

    _enum_type: enum.EnumType

    def __init__(self, enum_type: enum.EnumType) -> None:
        self._enum_type = enum_type
        self.__name__ = enum_type.__name__  # argparse names the type in "invalid <name> value" errors

    def __call__(self, val: str) -> enum.Enum:
        if isinstance(val, self._enum_type):
            return val
        name = str(val).strip().rsplit('.', maxsplit=1)[-1]
        for member in self._enum_type:  # type: ignore[attr-defined]
            if member.name.lower() == name.lower():
                return member
        raise argparse.ArgumentTypeError(
            f"invalid {self._enum_type.__name__} value: {val!r} (choose from "
            f"{', '.join(e.name for e in self._enum_type)})")  # type: ignore[attr-defined]


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
            builtin_type = getattr(builtins, val, None)
            if builtin_type is not None:
                found_type = builtin_type
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
                # argparse calls type(value); Enum(value) looks up by value (1, 2), so no command-line
                # string matched. Parse by member name instead, and show names in the help.
                attribDictCopy[key] = _EnumNameParser(found_type)
                attribDictCopy['choices'] = list(found_type)
                attribDictCopy.setdefault('metavar', '{' + ','.join(e.name for e in found_type) + '}')
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
        elif key == 'help':
            attribDictCopy[key] = _EscapeLiteralPercentInHelp(val)

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
