'''
Created on Jan 4, 2013

@author: u0490822
'''
import baseobject
import persistent
import transaction
from nornir_buildmanager.Config import Current;
from ZODB import FileStorage, DB
import os

class Volume(baseobject.PathData):
    '''
    Volume meta-data
    '''
    @property
    def Name(self):
        return self._Name;

    @Name.setter
    def Name(self, value):
        self._Name = value;

    @property
    def Blocks(self):
        return self._Blocks;

    def __init__(self, Name, **extra):
        '''
        Constructor
        '''
        self.Name = Name;
        self._Blocks = persistent.mapping.PersistentMapping();

        super(Volume, self).__init__(Parent = None, **extra);

class Block(baseobject.PathData):
    '''
    Block meta-data
    '''
    @property
    def Name(self):
        return self._Name;

    @Name.setter
    def Name(self, value):
        self._Name = value;

    @property
    def Sections(self):
        return self._Sections;

    def __init__(self, Name, **extra):
        '''
        Constructor
        '''

        super(Block, self).__init__(**extra);

        self.Name = Name;

        self._Sections = persistent.mapping.PersistentMapping();

class Section(baseobject.PathData):

    @property
    def SortKey(self):
        '''The default key used for sorting elements'''
        return self.Number;

    @property
    def Number(self):
        return self._Number;

    @Number.setter
    def Number(self, val):
        self._Number = val;

    @property
    def Description(self):
        return self._Description;

    @Description.setter
    def Description(self, val):
        self._Description = val;

    @property
    def Notes(self):
        return self._Notes;

    @Notes.setter
    def Notes(self, val):
        self._Notes = val;

    @property
    def Channels(self):
        return self._Channels;

    def __init__(self, Number, Path = None, **extra):
        super(Section, self).__init__(**extra);

        self.Number = Number;
        self._Description = "";
        self._Notes = "";

        if Path is None:
            Path = Current.SectionTemplate % self.Number;
            self.Path = Path;

        self._Channels = persistent.mapping.PersistentMapping();

class Channel(baseobject.PathData):

    @property
    def Transforms(self):
        return self._Transforms;

    @property
    def Filters(self):
        return self._Filters;

    def __init__(self, Name, Path = None, **extra):
        super(Channel, self).__init__(**extra);

        self._Filters = persistent.mapping.PersistentMapping();
        self._Transforms = persistent.mapping.PersistentMapping();

class Filter(baseobject.PathData):

    def __init__(self, Name, Path = None, **extra):
        super(Channel, self).__init__(**extra);

        self._ImageSets = persistent.mapping.PersistentMapping();
        self._TilePyramid = persistent.mapping.PersistentMapping();


class ImageSet(baseobject.PathData):

    def __init__(self, Name, Path = None, **extra):
        super(ImageSet, self).__init__(**extra);

        self._Levels = persistent.mapping.PersistentMapping();

class TilePyramid(baseobject.PathData):

    def __init__(self):
        super(TilePyramid, self).__init__(**extra);

class Level(baseobject.PathData):

    @property
    def Level(self):
        return self._Level;

    @Level.setter
    def Level(self, val):
        self._Level = val;

    def __init__(self, Level, Path = None, **extra):
        self.Level = Level;
        super(ImageSet, self).__init__(**extra);

def Open(VolumePath, volname):
    '''Opens a database that should contain a volume or creates it if it does not exist'''
    Utils.Misc.SetupLogging(VolumePath)

    datafileFullPath = os.path.join(VolumePath, 'VolumeData.zodb');
    storage = FileStorage.FileStorage(datafileFullPath);
    db = DB(storage);
    conn = db.open();

    dbroot = conn.root();

    if not dbroot.has_key('volumes'):
        from BTrees.OOBTree import OOBTree
        dbroot['volumes'] = OOBTree();

    volumes = dbroot['volumes'];

    if not volname in volumes:

        print "Creating new volume";

        vol = Volume(volname);

        vol.Name = volname;

        volumes[volname] = vol;

        transaction.commit();
    else:
        vol = volumes[volname];

        print "Loaded existing volume"

    vol.Path = VolumePath;

    return vol;

if __name__ == '__main__':
    import Utils.Misc

    vol = Open('C:/Buildscript/Test/DB/', 'Test Volume')

    blockname = 'TEM'

    if not blockname in vol.Blocks:
        vol.Blocks[blockname] = Block(blockname, Path = blockname, Parent = vol);

    block = vol.Blocks[blockname];

    transaction.commit();

    sectionNumbers = [1, 2, 3, 4, 5];
    for sectionNumber in sectionNumbers:
        if not sectionNumber in block.Sections:
            section = Section(sectionNumber, Parent = block);
            section._BlockObj = block;
            block.Sections[sectionNumber] = section;

    transaction.commit();

    block.Name = 'TSA';

    print vol;

    bObj = vol.Find('Block');

    print bObj;

    print "Done!"

    for s in vol.FindAll("Block[@Name='TSA']/Section[@Number>3]"):
        print s._BlockObj;

    s = vol.Find('Block/Section[@Number=1]');
    print s;





    pass
