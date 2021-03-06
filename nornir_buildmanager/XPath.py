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
        s = "";
        for item in list(self.__dict__.items()):
            if item[0].startswith('_'):
                continue;

            s = s + (str(item[0]) + " : " + str(item[1]) + '\n');

        return s;


def XPathIterator(XPath):
        '''A Very limited iterator which takes xpath strings as input and iterates over each subpath.
           Returns an object with the following properties:
           
           The following XPath syntax elements are supported: / .. . [] @ = '''

        # Get the name of the child node we are looking for
        PathParts = XPath.split('/');

        pat = re.compile(r"""
                        
                            (?P<Path>[^\[/\]]+)
                            (\[
                                (?P<IsAttribute>@)?
                                (?P<Name>[^=\]]+)
                                ((?P<Operator>[=<>])
                                (?P<Value>[^\]]+))?                                
                             \])?
                        
                        """, re.VERBOSE);

        # prettyoutput.Log(XPath);

        for subpath in PathParts:
            StartFromRoot = len(subpath) == 0;
            if(StartFromRoot):
                continue;

            # prettyoutput.Log("\n" + subpath);
            matches = pat.match(subpath);
            if matches is None:
                prettyoutput.LogErr("Error in xpath subpath: " + subpath);
                sys.exit();

            # Figure out if Value is a string (Starts with quotes)
            Obj = XSubPath();
            Obj.RawPath = subpath;
            for item in list(matches.groupdict().items()):
                Obj.__dict__[item[0]] = item[1];

            Obj.IsAttribute = not matches.group('IsAttribute') is None;

            if(Obj.Value is not None):
                if Obj.Value[0] == "'" and Obj.Value[-1] == "'" and len(Obj.Value) >= 3:
                    Obj.Value = Obj.Value[1:-2];
                elif Obj.Value[0] == '"' and Obj.Value[-1] == '"'  and len(Obj.Value) >= 3:
                    Obj.Value = Obj.Value[1:-2];
                else:
                    try:
                        Obj.Value = float(Obj.Value);
                    except:
                        pass;

            yield Obj;


if __name__ == '__main__':
    x1 = '/Volumes/Section/Channel[@Name=\'TEM\']/Filter/Tag[@Name=\'RAW\']/..';

    iterator = XPathIterator(x1);
    for p in iterator:
        prettyoutput.Log(str(p));

