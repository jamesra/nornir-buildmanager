'''
Created on Jan 4, 2013

@author: u0490822
'''

import persistent
import nornir_shared
import os
import versions
import re

import traceback

class EntityData(persistent.Persistent):
    '''
    Base class of all meta-data objects.
    '''

    @property
    def SortKey(self):
        '''The default key used for sorting elements'''
        raise Exception("Sort key not defined for object " + str(self.__class__));

    @property
    def Version(self):
        return self._Version;

    @Version.setter
    def Version(self, Value):
        self._Version = Value;

    @property
    def Parent(self):
        return self._Parent;

    @Parent.setter
    def Parent(self, Value):
        self._Parent = Value;

    def _GetCollection(self, CollectionName):

        childObj = None;
        CollectionName = '_' + CollectionName;
        if CollectionName in self.__dict__:
            childObj = self.__dict__[CollectionName];
        else:
            CollectionName = CollectionName + 's';
            if CollectionName in self.__dict__:
                childObj = self.__dict__[CollectionName];

        return childObj;

    def FindAll(self, XPath):
        '''Return every match'''

        if len(XPath) == 0:
            yield self;
        else:
            xpathobj = ParseXPath(XPath);

            if xpathobj.StartFromRoot:
                 root = self.GetRoot();
                 root.FindAll(xpathobj)

            '''Find out if there is a collection matching the ObjectName'''
            ChildCollection = self._GetCollection(xpathobj.CollectionName);
            if ChildCollection is None:
                return;

            for key, o in ChildCollection.items():

                if not xpathobj.Attribute is None:
                    if hasattr(o, xpathobj.Attribute):


                        if xpathobj.Operator is None:
                            for obj in o.FindAll(xpathobj.RemainingXPath):
                                yield obj;
                        else:
                            attr = getattr(o, xpathobj.Attribute)

                            val = xpathobj.ValueToBestType();

                            if xpathobj.Operator == '<':
                                if attr < val:
                                    for obj in o.FindAll(xpathobj.RemainingXPath):
                                        yield obj;
                            elif xpathobj.Operator == '>':
                                if attr > val:
                                    for obj in o.FindAll(xpathobj.RemainingXPath):
                                        yield obj;
                            elif xpathobj.Operator == '=':
                                if attr == val:
                                    for obj in o.FindAll(xpathobj.RemainingXPath):
                                        yield obj;
                            elif xpathobj.Operator == '>=':
                                if attr >= val:
                                    for obj in o.FindAll(xpathobj.RemainingXPath):
                                        yield obj;
                            elif xpathobj.Operator == '<=':
                                if attr <= val:
                                    for obj in o.FindAll(xpathobj.RemainingXPath):
                                        yield obj;
                else:
                    for obj in o.FindAll(xpathobj.RemainingXPath):
                        yield obj;

    def GetRoot(self):

        if self._Parent is None:
            return self;
        else:
            return self._Parent.GetRoot();

    def Find(self, XPath):
        '''Return the first match'''

        return self.FindAll(XPath).next();


    def __str__(self):

        # Figure out which are the attributes and which are the collections
        dictAttrib = {};
        dictCollections = {};

        outstr = "";

        maxNameLen = 0;

        for name in dir(self):
            if name[0] == "_":
                continue;

            if name == "Parent":
                continue;

            if len(name) > maxNameLen:
                maxNameLen = len(name);

            # Properties are the only variables reported
#            if not isinstance(getattr(self.__class__, name), property):
#                continue;
#
            try:
                val = getattr(self, name);
            except Exception, e:

                # inform operator of the name of the task throwing the exception
                # also, intercept the traceback and send to stderr.write() to avoid interweaving of traceback lines from parallel threads

                error_message = "***{0}".format(traceback.format_exc())
                val = error_message.replace('\n', '\n\t\t');

            if isinstance(val, persistent.mapping.PersistentMapping):
                dictCollections[name] = val;
            else:
                dictAttrib[name] = val;

        sortedAttribs = list(dictAttrib.keys());
        sortedAttribs.sort();
        sortedCollections = list(dictCollections.keys());
        sortedCollections.sort();

        attribTemplateStr = "\n  {0:<" + str(maxNameLen) + "}: {1}";
        collectionTemplateStr = "\n  {0:<" + str(maxNameLen) + "}";

        for name in sortedAttribs:
            outstr = outstr + attribTemplateStr.format(str(name), str(dictAttrib[name]));

        for name in sortedCollections:
            collection = dictCollections[name];
            outstr = outstr + collectionTemplateStr.format(str(name).upper());

            collectionStr = '';
            collectionItemNames = collection.keys();
            collectionItemNames.sort();
            for item in collectionItemNames:
                collectionStr = collectionStr + '\n  ' + str(item) + ": " + str(collection[item]);

            collectionStr = collectionStr.replace('\n', '\n' + ' ' * maxNameLen);
            outstr = outstr + collectionStr;

        return outstr;

    def __init__(self, Parent, **extra):
        '''
        Constructor
        '''''

        self._Parent = Parent;
        self._Version = versions.GetLatestVersionForNodeType(self.__class__);

        super(EntityData, self).__init__(**extra);

class PathData(EntityData):
    '''Meta-data for an object that refers to a file on disk'''

    @property
    def SortKey(self):
        '''The default key used for sorting elements'''
        return self.Path;

    @property
    def Path(self):
        return self._Path;

    @Path.setter
    def Path(self, value):
        self._Path = value;

        if hasattr(self, '__fullpath'):
            del self.__dict__['__fullpath'];

    def __init__(self, Path = None, **extra):
        self._Path = Path;

        if Path is None:
            if 'Name' in extra:
                self.Path = extra['Name'];

        super(PathData, self).__init__(**extra);


    @property
    def FullPath(self):

        FullPathStr = self.__dict__.get('__fullpath', None)

        try:
            if FullPathStr is None:
                FullPathStr = self.Path;

                if(not hasattr(self, '_Parent')):
                    return FullPathStr;

                IterElem = self.Parent;

                while not IterElem is None:
                    if hasattr(IterElem, 'FullPath'):
                        FullPathStr = os.path.join(IterElem.FullPath, FullPathStr);
                        IterElem = None;
                        break;

                    elif hasattr(IterElem, '_Parent'):
                        IterElem = IterElem._Parent;
                    else:
                        raise Exception("FullPath could not be generated for resource");

                # if os.path.isdir(FullPathStr): #Don't create a directory for files
                    # if not os.path.exists(FullPathStr):
                        # os.makedirs(FullPathStr);

                self.__dict__['__fullpath'] = FullPathStr;

            return FullPathStr;
        except:
            return "Error creating full path";

class DirectoryData(PathData):
    '''Describes a directory on a file system'''

    def __init__(self, **extra):
        super(DirectoryData, self).__init__(**extra);

    pass;

class FileData(PathData):

    @property
    def Checksum(self):
        '''Checksum of the file resource when the node was last updated'''
        checksum = self.get('Checksum', None);
        if checksum is None:
            if os.path.exists(self.FullPath):
                checksum = nornir_shared.Checksum.FileChecksum(self.FullPath)
                self.attrib['Checksum'] = str(checksum);

        return checksum;

    def __init__(self, **extra):
        super(FileData, self).__init__(**extra);


class TransformData(FileData):

    @property
    def InputTransform(self):
        return self._InputTransform;

    @InputTransform.setter
    def InputTransform(self, val):
        self._InputTransform = val;

    def __init__(self, **extra):
        super(TransformData, self).__init__(**extra);

        self._InputTransform = None;



def ParseXPath(XPath):
    '''Parses XPath and returns object with:
       StartFromRoot = True/False
       CollectionName = String
       Attribute = Property name on collection
       Operator = Comparison operator
       Value = Value of attribute'''

    class XPathObj():

        def __init__(self):
            self.StartFromRoot = False;
            self.Chunks = [];
            self.Criteria = None;

        def __str__(self):
            return str(self.__dict__);

        def ValueToBestType(self):
            '''Convert a string to a float or int if possible, otherwise return a string'''

            if self.IsString is None:
                v = float(self.NumericValue);
            else:
                v = self.StringValue;

            return v;

        pass;

    obj = XPathObj();

    obj.StartFromRoot = False;

    if len(XPath) == 0:
        i = 5;

    if XPath[0] == '/':
        obj.StartFromRoot = True;
        XPath = XPath[1:];

    obj.Chunks = XPath.split('/');

    if len(obj.Chunks) == 0:
        return;

    # Regular expression to parse XPath
    # String values must be enclosed with ''
    reCompiled = re.compile(r"""
                             (?P<CollectionName>\w+)
                             (?P<Brackets>\[)?
                             (?(Brackets)
                                 @(?P<Attribute>\w+)
                                     (?P<Operator>[=<>]|<=|>=)
                                     (?P<IsString>')?
                                         (?(IsString)
                                             (?P<StringValue>[\w\d]+)
                                             '?
                                             |
                                             (?P<NumericValue>[\d]*\.?[\d]+)
                                         )
                                 \])
                             """, re.VERBOSE);

    # Try to match the XPath
    match = reCompiled.search(obj.Chunks[0]);

    obj.RemainingXPath = '/'.join(obj.Chunks[1:]);

    obj.__dict__.update(match.groupdict());

    return obj;


if __name__ == '__main__':

    import re

    testStr = "Filter[@Name='Raw8']/TilePyramid[@Name='Raw8']";

    # reCompile = re.compile(r"""
                            # (?P<RootSlash>/?)
                            # (?P<CollectionName>\s+)
                            # /
                            # (?P<RemaingXPath>\s*)""", re.VERBOSE)

    # testStr = "Filter[@Name='Raw8']";

    reCompiled = re.compile(r"""
                             (?P<CollectionName>\w+)
                             (?P<Brackets>\[)?
                             (?(Brackets)
                                 @(?P<Attribute>\w+)
                                 (?P<Operator>[=<>])
                                 '(?P<Value>[\w\d]+)'
                                 \])
                             """);

    testStr = "Filter[@Name=8.2]";
    reCompiled = re.compile(r"""
                             (?P<CollectionName>\w+)
                             (?P<Brackets>\[)?
                             (?(Brackets)
                                 @(?P<Attribute>\w+)
                                     (?P<Operator>[=<>]|<=|>=)
                                     (?P<IsString>')?
                                         (?(IsString)
                                             (?P<StringValue>[\w\d]+)
                                             '?
                                             |
                                             (?P<NumericValue>[\d]*\.?[\d]+)
                                         )
                                 \])
                             """, re.VERBOSE);

    # testStr = "abababab";

    # reCompiled = re.compile(r"""(?P<groupab>ab)?""");

    # reCompiled = re.compile(r"""
                             # #(?P<CollectionName>\w+)
                             # (\Z|\[@#(?P<PropertyName>\w+)
                                   # (?P<Operator>[=<>])
                                   # (?P<Value>'.+')\])""", re.VERBOSE)

    # reCompile = re.compile(r"""
                            # (?P<RootSlash>/)?
                            # (?P<CollectionName>\w+)
                            # (?P<Query>\[\w+=[\w\d]+\])?
                            # (?P<RemainingXPath>/?.*)
                            # """, re.VERBOSE)

    obj = ParseXPath(testStr);

    print str(obj);

    matches = reCompiled.search(testStr);

    print str(matches.groupdict())


