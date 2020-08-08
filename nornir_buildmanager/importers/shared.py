'''
Created on Apr 18, 2019

@author: u0490822
'''

import os
import sys
import shutil
import glob
from nornir_buildmanager.VolumeManagerETree import NotesNode
import nornir_shared.prettyoutput as prettyoutput
import collections
import re

FilenameMetadata = collections.namedtuple('SectionInfo', 'fullpath number version name downsample extension')
MinMaxGamma = collections.namedtuple('MinMaxGamma', 'min max gamma')  

#Global instance of our parser for filenames that is initialized upon first use
_InputFileRegExParser = None

def GetSectionInfo(fullpath):
    '''Given a path or filename returns the meta data we can determine from the name
       :returns: A named tuple with (fullpath number version name downsample extension)
    '''
    fileName = os.path.basename(fullpath)
    
    d = ParseMetadataFromFilename(fileName)
    
    return FilenameMetadata(fullpath, d['Number'], d['Version'], d['Name'], d['Downsample'], d['Extension'])

def FileMetaDataStrHeader():
    output = "{0:<22}\t{1:<6}{2:<5}{3:<16}{4:<5}{5}\n".format("Path", "#", "Ver", "Name", "Ds", "ext")
    return output
    
def FileMetaDataStr(data):
    '''Provides a pretty string for a FilenameMetadata tuple'''
    v = data.version
    if v == '\0':
        v = None
    output = "{0:<22}\t{1:<6}{2:<5}{3:<16}{4:<5}{5}\n".format(os.path.basename(data.fullpath), str(data.number), str(v), str(data.name), str(data.downsample), str(data.extension))
    return output


def TryAddNotes(containerObj, InputPath, logger):
    '''Check the path for a notes.txt file.  If found, add a <Notes> element to the passed containerObj'''
    
    NotesFiles = glob.glob(os.path.join(InputPath, '*.txt'))
    NotesAdded = False
    if len(NotesFiles) > 0:
        for filename in NotesFiles:
            
            if os.path.basename(filename) == 'ContrastOverrides.txt':
                continue 
            
            if os.path.basename(filename) == 'Timing.txt':
                continue 
            
            try:
                from xml.sax.saxutils import escape

                NotesFilename = os.path.basename(filename)
                CopiedNotesFullPath = os.path.join(containerObj.FullPath, NotesFilename)
                if not os.path.exists(CopiedNotesFullPath):
                    os.makedirs(containerObj.FullPath, exist_ok=True)
                    shutil.copyfile(filename, CopiedNotesFullPath)
                    NotesAdded = True

                with open(filename, 'r') as f:
                    notesTxt = f.read()
                    (base, ext) = os.path.splitext(filename)
                    encoding = "utf-8"
                    ext = ext.lower()
                    # notesTxt = notesTxt.encode(encoding)

                    notesTxt = notesTxt.replace('\0', '')

                    if len(notesTxt) > 0:
                        # XMLnotesTxt = notesTxt
                        # notesTxt = notesTxt.encode('utf-8')
                        XMLnotesTxt = escape(notesTxt)

                        # Create a Notes node to save the notes into
                        NotesNodeObj = NotesNode.Create(Text=XMLnotesTxt, SourceFilename=NotesFilename)
                        containerObj.RemoveOldChildrenByAttrib('Notes', 'SourceFilename', NotesFilename)
                        [added, NotesNodeObj] = containerObj.UpdateOrAddChildByAttrib(NotesNodeObj, 'SourceFilename')

                        if added:
                            # Try to copy the notes to the output dir if we created a node
                            if not os.path.exists(CopiedNotesFullPath):
                                shutil.copyfile(filename, CopiedNotesFullPath)

                        NotesNodeObj.text = XMLnotesTxt
                        NotesNodeObj.encoding = encoding

                        NotesAdded = NotesAdded or added

            except:
                (etype, evalue, etraceback) = sys.exc_info()
                prettyoutput.Log("Attempt to include notes from " + filename + " failed.\n" + evalue.message)
                prettyoutput.Log(etraceback)

    return NotesAdded

def ParseMetadataFromFilename(string):
    '''
    Parses the filename of an input file to determine
        Number : Section Number
        Version : A letter indicating whether this is a recapture of the same section. In increasing alphabetical order.  'B' would be a recapture of 'A'
         
    '''
    global _InputFileRegExParser
    if _InputFileRegExParser is None:
        _InputFileRegExParser = re.compile(r"""
            (?P<Number>\d+)?                                           #Section Number
            \s?                                                        #Possible space between section number and version
            (?P<Version>[^_|^\s](?=[\s\|_|\.]))?                       #Version letter
            [_|\s]*                                                    #Divider between section number/version and name
            (?P<Name>
              (
                [a-zA-Z0-9]                                             #Any letters
                |
                [ ](?![0-9]+\.)
              )+                                       #Any spaces not followed by numbers and a period (The downsample value) 
            )?                                                         #Name
            [_|\s]*                                                    #Divider between name and downsample/extension
            (?P<Downsample>\d+)?                                       #Downsample level if present
            (?P<Extension>\.\w+)?                                     #Extension if present
            """, re.VERBOSE) 
    
    m = _InputFileRegExParser.match(string)
    if m is not None:
        
        d = m.groupdict()
        section_number = d.get('Number',None)
        if section_number is not None:
            d['Number'] = int(section_number)
            
        version = d.get('Version',None)
        if version is None:
            version = '\0' #Assign a letter that will sort earlier than 'A' in case someone names the first recapture A instead of B...
            d['Version'] = version
            
        ds = d.get('Downsample',None)
        if ds is not None:
            d['Downsample'] = int(ds)
            
        return d
    
    else:
        return None
    
                       

if __name__ == "__main__":
    pass
        