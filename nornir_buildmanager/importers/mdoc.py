from nornir_buildmanager.VolumeManagerETree import *
from nornir_shared.images import *

import idoc;


class SerialEMMDocImport(idoc.SerialEMIDocImport):

    def SerialEMMDocImport(self):
        pass;


    @classmethod
    def ToMosaic(cls, VolumeObj, InputPath, OutputPath=None, Extension=None, OutputImageExt=None, TileOverlap=None, TargetBpp=None, debug=None, **kwargs):
        '''The mdoc should be paired with a .st file of the same name. 
       The st file is converted to tif's, the mdoc is renamed to an idoc
       and the idoc importer is run.'''

        if(OutputImageExt is None):
            OutputImageExt = 'png';

        if(Extension is None):
            Extension = 'idoc';

        # Default to the directory above ours if an output path is not specified
        if OutputPath is None:
            OutputPath = os.path.join(InputPath, "..");

        if not os.path.exists(OutputPath):
            os.makedirs(OutputPath);

        prettyoutput.CurseString('Stage', "MDoc to IDoc " + str(InputPath))

        mdocFiles = glob.glob(os.path.join(InputPath, '*.' + Extension));
        if(len(mdocFiles) == 0):
            # This shouldn't happen, but just in case
            assert len(mdocFiles) > 0, "ToMosaic called without proper target file present in the path: " + str(InputPath);
            return [None, None];


        # ok, try to find the .st file
        for mdoc in mdocFiles:
            basename = os.path.basename(mdoc);
            [SectionNumber, SectionName, Downsample] = idoc.SerialEMIDocImport.GetSectionInfo(basename);

            MDocImportDir = str(SectionNumber);

            mdocDirname = os.path.dirname(mdoc);
            mdocBasename = os.path.basename(mdoc);
            (mdocRoot, ext) = os.path.splitext(mdocBasename);
            stNameFullPath = os.path.join(mdocDirname, mdocRoot);
            if(not os.path.exists(stNameFullPath)):
                continue;

            MDocImportDirFullPath = os.path.join(mdocDirname, MDocImportDir);

            if not os.path.exists(MDocImportDirFullPath):
                os.makedirs(MDocImportDirFullPath);

            idocFilename = os.path.join(mdocDirname, mdocRoot + '.idoc');
            if(os.path.exists(idocFilename)):
                if(not nornir_shared.Files.OutdatedFile(mdoc, idocFilename)):
                    continue;

            tempDirName = "Unpack" + os.sep;

            tempDirNameFullPath = os.path.join(InputPath, tempDirName);

            if not os.path.exists(tempDirNameFullPath):
                os.makedirs(tempDirNameFullPath);

            # [Image = 10000.tif]
            cmd = "mrc2tif " + stNameFullPath + " " + tempDirNameFullPath;
            prettyoutput.Log(cmd);
            subprocess.call(cmd + " && exit", shell=True);

            tiffFiles = glob.glob(os.path.join(InputPath, tempDirName, '.*.tif'));

            iNumber = 0;
            # images from MRC2TIF appear to be named .###.tif, where ### is the ZLevel
            # Convert these to a name that only includes the ####.tif
            for tiffFile in tiffFiles:
                try:
                    # Figure out the number from the file
                    baseTifName = os.path.basename(tiffFile);
                    [TifRoot, TifExt] = os.path.splitext(baseTifName);

                    iDot = TifRoot.index('.');
                    ZLevelStr = TifRoot[iDot + 1:];
                    ZLevel = int(ZLevelStr);
                    NewFilename = str(ZLevel) + '.tif';

                    NewFilenameFullPath = os.path.join(MDocImportDirFullPath, NewFilename);
                    if os.path.exists(NewFilenameFullPath):
                        os.remove(NewFilenameFullPath);

                    os.rename(tiffFile, NewFilenameFullPath);
                except Exception as e:
                    prettyoutput.LogErr('Could not rename converted tif file: ' + tiffFile);
                    prettyoutput.LogErr(str(e));

            shutil.rmtree(tempDirNameFullPath);

            [mdocroot, mdocExt] = os.path.splitext(mdoc);
            mdocfilenamebase = os.path.basename(mdocroot);
            idocFilename = mdocfilenamebase + '.idoc';
            idocFilenameFullPath = os.path.join(MDocImportDirFullPath, idocFilename);
            cls.ConvertMDocToIDoc(mdoc, idocFilenameFullPath);

            super(SerialEMMDocImport, cls).ToMosaic(VolumeObj, MDocImportDirFullPath, OutputPath, 'idoc', OutputImageExt, TileOverlap, TargetBpp);


    @classmethod
    def ConvertMDocToIDoc(cls, MDocFilename, IDocFilename):
        '''Converts the [ZValue = ...] entries in an mdoc to the
       [Image = ...] entries of an idoc'''
        try:
            mdocFile = open(MDocFilename, 'r');
            mdocLines = mdocFile.readlines();

            idocFile = open(IDocFilename, 'w');


            ImageTemplateStr = '[Image = %d.tif]';
            for line in mdocLines:
                line = line.strip();

                if not line.startswith('[ZValue'):
                    idocFile.write(line + '\n');
                    continue;

                # Determine Z value
                zStart = line.index('=') + 1;
                zEnd = line.index(']');

                zValStr = line[zStart:zEnd];
                zVal = int(zValStr);

                ImageString = ImageTemplateStr % zVal;

                idocFile.write(ImageString + '\n');

        finally:
            if not mdocFile is None:
                mdocFile.close();

            if not idocFile is None:
                idocFile.close();
