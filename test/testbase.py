'''
Created on May 22, 2013

@author: u0490822
'''

import logging
import os
import shutil
import unittest
import cProfile

from nornir_shared.misc import SetupLogging


class TestBase(unittest.TestCase):
    '''
    classdocs
    '''

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
            self.fail("TESTOUTPUTPATH environment variable should specfify input data directory")

        return None

    @property
    def TestLogPath(self):
        if 'TESTOUTPUTPATH' in os.environ:
            TestOutputDir = os.environ["TESTOUTPUTPATH"]
            return os.path.join(TestOutputDir, "Logs", self.classname)
        else:
            self.fail("TESTOUTPUTPATH environment variable should specfify input data directory")

        return None

    @property
    def TestHost(self):
        if 'TESTHOST' in os.environ:
            return os.environ["TESTHOST"]

        return None

    @property
    def TestOutputURL(self):
        if self.TestHost is None:
            return None

        return self.TestHost + "/" + self.classname

    @property
    def TestProfilerOutputPath(self):
        return os.path.join(self.TestOutputPath, self.classname + '.profile')


    def setUp(self):

        super(TestBase, self).setUp()

        self.profiler = None

        if 'PROFILE' in os.environ:
            self.profiler = cProfile.Profile()
            self.profiler.enable()

        self.TestDataPath = self.TestInputPath

        # Remove output of earlier tests
        if os.path.exists(self.TestOutputPath):
            shutil.rmtree(self.TestOutputPath)

        os.makedirs(self.TestOutputPath)

        SetupLogging()
        self.Logger = logging.getLogger(self.classname)

    def tearDown(self):

        if not self.profiler is None:
            self.profiler.dump_stats(self.TestProfilerOutputPath)

        unittest.TestCase.tearDown(self)


