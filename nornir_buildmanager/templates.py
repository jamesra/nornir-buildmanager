import multiprocessing
import os



class __Config:
    '''A place to store hardcoded values used throughout the buildscripts'''


    def __init__(self):
        # Standard format strings
        self.DefaultImageExt = 'png';
        self.TileFormat = 'Tile%06d';
        self.SectionFormat = '04d';
        self.SectionTemplate = '%04d';

        self.LevelFormat = '%03d';

        self.DownsampleFormat = '%03d';
        # self.GridTileCoordFormat = '03d';  #The optimized tile format string
        self.GridTileCoordFormat = '03d';  # The optimized tile format string
        self.GridTileCoordTemplate = '%' + self.GridTileCoordFormat;  # The optimized tile format string
        self.GridTileNameTemplate = '%(prefix)sX%(X)' + self.GridTileCoordFormat + '_Y%(Y)' + self.GridTileCoordFormat + '%(postfix)s';  # The optimized tile format string
        self.GridTileMatchStringTemplate = '%(prefix)sX%(X)s_Y%(Y)s%(postfix)s';  # The optimized tile format string
        self.TileCoordFormat = '%03d';
        self.__NumProcs = None;
        self.DefaultDownsampleLevels = [1, 2, 4, 8, 16, 32, 64, 128];
        self.DebugFast = True;  # Set to true to skip time consuming tests such as bits-per-pixel when debugging.

    @property
    def NumProcs(self):
        '''Return the number of processors'''
        if self.__NumProcs is None:
            import multiprocessing;
            self.__NumProcs = multiprocessing.cpu_count();
            try:

                from nornir_shared import prettyoutput;
                prettyoutput.CurseString("# of Cores", str(self.__NumProcs));
            except:
                pass;

        return self.__NumProcs;

    def BlobCmd(self):
        return "ir-blob -sh 1 -max 3 -threads " + str(self.NumProcs) + " ";

    def ClaheCmd(self):
        return "ir-clahe -sh 1 -bins 4096 -remap 0 255 -slope 5 -window 8 8 ";

    def FFTCmd(self):
        return "ir-fft -sh 8 -sp 1 -py 3 -ol 0.05 0.3 -clahe 6 ";

    def TranslateCmd(self):
        return "ir-refine-translate -sh 1 -tolerance 0 ";

    def RefineCmd(self):
        return "ir-refine-grid ";

    def AssembleCmd(self):
        return "ir-assemble ";

    def MultipleIntensityAverageCmd(self):
        return "MultipleIntensityAverage";

    def StosBruteCmd(self):
    #    return "ir-stos-brute -sh 1 -clahe 2 -refine -cubic -regularize ";
        return "ir-stos-brute -sh 1 -refine -regularize ";

    def StosGridCmd(self, Spacing = None, Neighborhood = None):
        if(Spacing is None):
            Spacing = 128;
        if(Neighborhood is None):
            Neighborhood = 128;

        return "ir-stos-grid -sh 1 -fft 0 0.25 -grid_spacing " + str(Spacing) + " -neighborhood " + str(Neighborhood) + " -it 10 ";

    def StosAddTransform(self):
        return "ir-add-transforms ";

    # Users calling this library can change this list to adjust the downsample levels used

Current = __Config();

