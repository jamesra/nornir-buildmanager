import glob
import os
import subprocess
from collections.abc import Generator

import nornir_buildmanager.importers.shared as shared
from nornir_shared import prettyoutput
from nornir_shared.files import OutdatedFile, rmtree
from nornir_shared.images import *
from . import idoc


def _run_mrc2tif(st_path: str, output_dir: str) -> None:
    """Invoke ``mrc2tif`` without a shell so paths with spaces stay intact."""
    cmd = ['mrc2tif', st_path, output_dir]
    prettyoutput.Log(' '.join(cmd))
    subprocess.run(cmd, check=False)


def _list_mrc2tif_outputs(directory: str) -> list[str]:
    """List ``mrc2tif`` TIFF outputs; prefer ``.###.tif`` names; match case-insensitively."""
    if not os.path.isdir(directory):
        return []
    dotted: list[str] = []
    plain: list[str] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            lower = entry.name.lower()
            if not (lower.endswith('.tif') or lower.endswith('.tiff')):
                continue
            if entry.name.startswith('.'):
                dotted.append(entry.path)
            else:
                plain.append(entry.path)
    return dotted if dotted else plain


class SerialEMMDocImport(idoc.SerialEMIDocImport):

    def SerialEMMDocImport(self):
        pass

    @classmethod
    def ToMosaic(cls, VolumeObj, InputPath, OutputPath=None, Extension=None, OutputImageExt=None, TileOverlap=None,
                 TargetBpp=None, debug=None, **kwargs) -> Generator:
        '''The mdoc should be paired with a .st file of the same name.
       The st file is converted to tif's, the mdoc is renamed to an idoc
       and the idoc importer is run.'''

        if OutputImageExt is None:
            OutputImageExt = 'png'

        if Extension is None:
            Extension = 'mdoc'

        # Default to the directory above ours if an output path is not specified
        if OutputPath is None:
            OutputPath = os.path.join(InputPath, "..")

        os.makedirs(OutputPath, exist_ok=True)

        prettyoutput.CurseString('Stage', "MDoc to IDoc " + str(InputPath))

        mdocFiles = glob.glob(os.path.join(InputPath, '*.' + Extension))
        if len(mdocFiles) == 0:
            # This shouldn't happen, but just in case
            assert (len(mdocFiles) > 0), "ToMosaic called without proper target file present in the path: " + str(
                InputPath)
            return

        # ok, try to find the .st file
        for mdoc in mdocFiles:
            basename = os.path.basename(mdoc)
            meta = shared.GetSectionInfo(basename)
            SectionNumber = meta.number
            SectionName = meta.name
            Downsample = meta.downsample

            MDocImportDir = str(SectionNumber)

            mdocDirname = os.path.dirname(mdoc)
            mdocBasename = os.path.basename(mdoc)
            (mdocRoot, ext) = os.path.splitext(mdocBasename)
            stNameFullPath = os.path.join(mdocDirname, mdocRoot)
            if not os.path.exists(stNameFullPath):
                continue

            MDocImportDirFullPath = os.path.join(mdocDirname, MDocImportDir)

            os.makedirs(MDocImportDirFullPath, exist_ok=True)

            idocFilename = os.path.join(mdocDirname, mdocRoot + '.idoc')
            if os.path.exists(idocFilename):
                if not OutdatedFile(mdoc, idocFilename):
                    continue

            tempDirName = "Unpack" + os.sep

            tempDirNameFullPath = os.path.join(InputPath, tempDirName)

            os.makedirs(tempDirNameFullPath, exist_ok=True)

            # [Image = 10000.tif]
            _run_mrc2tif(stNameFullPath, tempDirNameFullPath)

            # mrc2tif may emit either .###.tif or ###.tif depending on version/options.
            tiffFiles = _list_mrc2tif_outputs(tempDirNameFullPath)

            iNumber = 0
            # images from MRC2TIF appear to be named .###.tif, where ### is the ZLevel
            # Convert these to a name that only includes the ####.tif
            for tiffFile in tiffFiles:
                try:
                    # Figure out the number from the file
                    baseTifName = os.path.basename(tiffFile)
                    [TifRoot, TifExt] = os.path.splitext(baseTifName)

                    if '.' in TifRoot:
                        iDot = TifRoot.index('.')
                        ZLevelStr = TifRoot[iDot + 1:]
                    else:
                        ZLevelStr = TifRoot
                    ZLevel = int(ZLevelStr)
                    NewFilename = str(ZLevel) + '.tif'

                    NewFilenameFullPath = os.path.join(MDocImportDirFullPath, NewFilename)
                    if os.path.exists(NewFilenameFullPath):
                        os.remove(NewFilenameFullPath)

                    os.rename(tiffFile, NewFilenameFullPath)
                except Exception as e:
                    prettyoutput.LogErr('Could not rename converted tif file: ' + tiffFile)
                    prettyoutput.LogErr(str(e))

            rmtree(tempDirNameFullPath)

            [mdocroot, mdocExt] = os.path.splitext(mdoc)
            mdocfilenamebase = os.path.basename(mdocroot)
            idocFilename = mdocfilenamebase + '.idoc'
            idocFilenameFullPath = os.path.join(MDocImportDirFullPath, idocFilename)
            cls.ConvertMDocToIDoc(mdoc, idocFilenameFullPath)

            # ContrastCutoffs are 0-1 fractions, not percentages: shared.py levels
            # with AutoLevel(cutoffs[0], 1.0 - cutoffs[1]). (0.0, 1.0) is the
            # "trim nothing" pair this call has always meant. Passing (0.0, 100.0)
            # asked for AutoLevel(0.0, -99.0), which returns an inverted range.
            yield from super(SerialEMMDocImport, cls).ToMosaic(
                VolumeObj, idocFilenameFullPath,
                ContrastCutoffs=(0.0, 1.0),
                OutputImageExt=OutputImageExt,
                TargetBpp=TargetBpp,
            )

    @classmethod
    def ConvertMDocToIDoc(cls, MDocFilename, IDocFilename):
        '''Converts the [ZValue = ...] entries in an mdoc to the
       [Image = ...] entries of an idoc'''
        mdocFile = None
        idocFile = None
        try:
            mdocFile = open(MDocFilename, 'r')
            mdocLines = mdocFile.readlines()

            idocFile = open(IDocFilename, 'w')

            ImageTemplateStr = '[Image = %d.tif]'
            for line in mdocLines:
                line = line.strip()

                if not line.startswith('[ZValue'):
                    idocFile.write(line + '\n')
                    continue

                # Determine Z value
                zStart = line.index('=') + 1
                zEnd = line.index(']')

                zValStr = line[zStart:zEnd]
                zVal = int(zValStr)

                ImageString = ImageTemplateStr % zVal

                idocFile.write(ImageString + '\n')

        finally:
            if mdocFile is not None:
                mdocFile.close()

            if idocFile is not None:
                idocFile.close()
