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

SectionInfo = collections.namedtuple('SectionInfo', 'number name downsample')
MinMaxGamma = collections.namedtuple('MinMaxGamma', 'min max gamma')  

def GetSectionInfo(fileName):
    fileName = os.path.basename(fileName)

    # Make sure extension is present in the filename
    [fileName, _] = os.path.splitext(fileName)

    SectionNumber = -1
    Downsample = 1
    parts = fileName.split("_")
    try:
        SectionNumber = int(parts[0])
    except:
        # We really can't recover from this, so maybe an exception should be thrown instead
        raise ValueError("Could not parse section number from input {0}.  Should begin with a section number and then an underscore".format(fileName))

    try:
        SectionName = parts[1]
    except:
        SectionName = str(SectionNumber)

    # If we don't have a valid downsample value we assume 1
    try:
        DownsampleStrings = parts[2].split(".")
        Downsample = int(DownsampleStrings[0])
    except:
        Downsample = 1

    return SectionInfo(SectionNumber, SectionName, Downsample)


def TryAddNotes(containerObj, InputPath, logger):
    '''Check the path for a notes.txt file.  If found, add a <Notes> element to the passed containerObj'''
    
    NotesFiles = glob.glob(os.path.join(InputPath, '*.txt'))
    NotesAdded = False
    if len(NotesFiles) > 0:
        for filename in NotesFiles:
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

