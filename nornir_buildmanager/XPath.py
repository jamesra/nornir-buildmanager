'''
Created on Jun 7, 2012

@author: Jamesan
'''

import re
import sys

import nornir_shared.prettyoutput as prettyoutput


class XSubPath(object):
    '''Represents a level of an XPath string, returned by XPathIterator'''

    def __init__(self):
        #        IsAttribute = False;
        #        Name = None;
        #        Operator = None;
        #        Value = None;
        #        Path = None;
        #        RawPath = None;
        return

    def __str__(self):
        s = ""
        for item in list(self.__dict__.items()):
            if item[0].startswith('_'):
                continue

            s = s + (str(item[0]) + " : " + str(item[1]) + '\n')

        return s


def XPathIterator(XPath: str):
    '''A Very limited iterator which takes xpath strings as input and iterates over each subpath.
           Returns an object with the following properties:
           
           The following XPath syntax elements are supported: / .. . [] @ = '''

    # Get the name of the child node we are looking for
    PathParts = XPath.split('/')

    pat = re.compile(r"""
                        
                            (?P<Path>[^\[/\]]+)
                            (\[
                                (?P<IsAttribute>@)?
                                (?P<Name>[^=\]]+)
                                ((?P<Operator>[=<>])
                                (?P<Value>[^\]]+))?                                
                             \])?
                        
                        """, re.VERBOSE)

    # prettyoutput.Log(XPath);

    for subpath in PathParts:
        StartFromRoot = len(subpath) == 0
        if StartFromRoot:
            continue

        # prettyoutput.Log("\n" + subpath);
        matches = pat.match(subpath)
        if matches is None:
            prettyoutput.LogErr("Error in xpath subpath: " + subpath)
            sys.exit()

        # Figure out if Value is a string (Starts with quotes)
        obj = XSubPath()
        obj.RawPath = subpath
        for item in list(matches.groupdict().items()):
            obj.__dict__[item[0]] = item[1]

        obj.IsAttribute = not matches.group('IsAttribute') is None

        if obj.Value is not None:
            if obj.Value[0] == "'" and obj.Value[-1] == "'" and len(obj.Value) >= 3:
                obj.Value = obj.Value[1:-2]
            elif obj.Value[0] == '"' and obj.Value[-1] == '"' and len(obj.Value) >= 3:
                obj.Value = obj.Value[1:-2]
            else:
                try:
                    obj.Value = float(obj.Value)
                except:
                    pass

        yield obj


if __name__ == '__main__':
    x1 = '/Volumes/Section/Channel[@Name=\'TEM\']/Filter/Tag[@Name=\'RAW\']/..'

    iterator = XPathIterator(x1)
    for p in iterator:
        prettyoutput.Log(str(p))
