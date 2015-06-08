'''
Created on Mar 21, 2013

@author: u0490822
'''
import unittest
import os
import glob
import logging
from nornir_shared.misc import SetupLogging
import shutil
import cProfile
import pickle
import pstats
import nornir_pools
 
class PickleHelper(object):
         
    @property
    def TestCachePath(self):
        '''Contains cached files from previous test runs, such as database query results.
           Entries in this cache should have a low probablility of changing and breaking tests'''
        if 'TESTOUTPUTPATH' in os.environ:
            TestOutputDir = os.environ["TESTOUTPUTPATH"]
            return os.path.join(TestOutputDir, "Cache", self.classname)
        else:
            self.fail("TESTOUTPUTPATH environment variable should specify test output directory")

        return None
    
    
    def SaveVariable(self, var, path):
        fullpath = os.path.join(self.TestCachePath, path)

        if not os.path.exists(os.path.dirname(fullpath)):
            os.makedirs(os.path.dirname(fullpath))

        with open(fullpath, 'wb') as filehandle:
            print("Saving: " + fullpath)
            pickle.dump(var, filehandle, protocol=pickle.HIGHEST_PROTOCOL)
            

    def ReadOrCreateVariable(self, varname, createfunc=None, **kwargs):
        '''Reads variable from disk, call createfunc if it does not exist'''

        var = None
        if hasattr(self, varname):
            var = getattr(self, varname)

        if var is None:
            path = os.path.join(self.TestCachePath, varname + ".pickle")
            if os.path.exists(path):
                with open(path, 'rb') as filehandle:
                    try:
                        var = pickle.load(filehandle)
                    except:
                        var = None
                        print("Unable to load graph from pickle file: " + path)

            if var is None and not createfunc is None:
                var = createfunc(**kwargs)
                self.SaveVariable(var, path)

        return var


class TestBase(unittest.TestCase):

    @property
    def classname(self):
        return str(self.__class__.__name__)

    @property
    def TestInputPath(self):
        if 'TESTINPUTPATH' in os.environ:
            TestInputDir = os.environ["TESTINPUTPATH"]
            self.assertTrue(os.path.exists(TestInputDir), "Test input directory specified by TESTINPUTPATH environment variable does not exist")
            return TestInputDir
        else:
            self.fail("TESTINPUTPATH environment variable should specfify input data directory")

        return None

    @property
    def TestOutputPath(self):
        if 'TESTOUTPUTPATH' in os.environ:
            TestOutputDir = os.environ["TESTOUTPUTPATH"]
            return os.path.join(TestOutputDir, self.classname)
        else:
            self.fail("TESTOUTPUTPATH environment variable should specify input data directory")

        return None
    
    @property
    def TestSetupCachePath(self):
        '''The directory where we can cache the setup phase of the tests.  Delete to obtain a clean run of all tests'''
        if 'TESTOUTPUTPATH' in os.environ:
            TestOutputDir = os.environ["TESTOUTPUTPATH"]
            return os.path.join(TestOutputDir, 'Cache')
        else:
            self.fail("TESTOUTPUTPATH environment variable should specify input data directory")

        return None
    
    @property
    def TestOutputURL(self):
        if 'TESTOUTPUTURL' in os.environ:
            return os.environ["TESTOUTPUTURL"]
        else:
            return "http://localhost/"

    @property
    def TestLogPath(self):
        if 'TESTOUTPUTPATH' in os.environ:
            TestOutputDir = os.environ["TESTOUTPUTPATH"]
            return os.path.join(TestOutputDir, "Logs", self.classname)
        else:
            self.fail("TESTOUTPUTPATH environment variable should specfify input data directory")

        return None

    @property
    def TestProfilerOutputPath(self):
        return os.path.join(self.TestOutputPath, self.classname + '.profile')

    def setUp(self):
        self.VolumeDir = self.TestOutputPath

        # Remove output of earlier tests

        try:
            if os.path.exists(self.VolumeDir):
                shutil.rmtree(self.VolumeDir)

            os.makedirs(self.VolumeDir)
        except:
            pass

        self.profiler = None

        if 'PROFILE' in os.environ:
            self.profiler = cProfile.Profile()
            self.profiler.enable()
          
        SetupLogging(Level=logging.INFO)
        self.Logger = logging.getLogger(self.classname)
        

    def tearDown(self):
        nornir_pools.ClosePools()
        if not self.profiler is None:
            self.profiler.dump_stats(self.TestProfilerOutputPath)
                         
        unittest.TestCase.tearDown(self)
        
    def SaveTestSetupToCache(self, subpath=None):
        '''Copy the results from the output directory to the test cache directory'''
        TestCachePath = self.TestSetupCachePath
        if subpath is not None:
            TestCachePath = os.path.join(self.TestSetupCachePath, subpath)
            
        if os.path.exists(TestCachePath):
            shutil.rmtree(TestCachePath)
            
        shutil.copytree(self.TestOutputPath, TestCachePath)
        print("Saved test setup to cache %s" % TestCachePath)
        
    def TryLoadTestSetupFromCache(self, subpath=None):
        '''Copy the results from the setup cache to the output directory.
        :return: True if cache existed and copied
        ''' 
        if not isinstance(subpath, str):
            subpath = str(subpath)
            
        TestCachePath = self.TestSetupCachePath
        if subpath is not None:
            TestCachePath = os.path.join(self.TestSetupCachePath, subpath)
            
        if os.path.exists(TestCachePath):
            print("Found test setup in cache %s" % TestCachePath)
            if os.path.exists(self.TestOutputPath):
                shutil.rmtree(self.TestOutputPath)
            shutil.copytree(TestCachePath, self.TestOutputPath)
            return True
        
        return False 


class ImageTestBase(TestBase):

    def GetImagePath(self, ImageFilename):
        return os.path.join(self.ImportedDataPath, ImageFilename)

    def setUp(self):
        self.ImportedDataPath = os.path.join(self.TestInputPath, "Images")

        super(ImageTestBase, self).setUp()


class MosaicTestBase(TestBase):

    @property
    def TestName(self):
        raise NotImplementedError("Test should override TestName property")
    
    @property
    def TestOutputPath(self):
        return os.path.join(super(MosaicTestBase,self).TestOutputPath, self.id())

    def GetMosaicFiles(self):
        return glob.glob(os.path.join(self.ImportedDataPath, self.TestName, "*.mosaic"))

    def GetTileFullPath(self, downsamplePath=None):
        if downsamplePath is None:
            downsamplePath = "001"

        return os.path.join(self.ImportedDataPath, self.TestName, "Leveled", "TilePyramid", downsamplePath)

    def setUp(self):
        self.ImportedDataPath = os.path.join(self.TestInputPath, "Transforms", "Mosaics")
        
        if not os.path.exists(self.TestOutputPath):
            os.makedirs(self.TestOutputPath, exist_ok=True)

        super(MosaicTestBase, self).setUp()


if __name__ == "__main__":
    # import syssys.argv = ['', 'Test.testName']
    unittest.main()