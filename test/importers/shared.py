'''
Created on Aug 7, 2020

@author: u0490822
'''
import unittest
import collections

import nornir_buildmanager 


class TestShared(unittest.TestCase):
    
    def testInvalidNames(self):
        test_cases = ["", "B", "Jones_33"]
        
        for test_case in test_cases:
            try:
                d = nornir_buildmanager.importers.shared.ParseMetadataFromFilename(test_case)
                self.fail(f'Should not be able to parse invalid section name {test_case}.\nGot: {d}')
            except nornir_buildmanager.NornirUserException:
                pass
            except:
                self.fail(f"Failing to parse an invalid section name {test_case} should raise NornirUserException")
 
    def testName(self):
        
        FilenameParserTestCase = collections.namedtuple('FilenameParserTestCase', 'string Number Version Name Downsample Extension')
 
        test_cases = [
            FilenameParserTestCase("0.idoc", 0, None, None, None, '.idoc'),
            FilenameParserTestCase("1B.idoc", 1, 'B', None, None, '.idoc'),
            FilenameParserTestCase("2_NameHere.idoc", 2, None, 'NameHere', None, '.idoc'),
            FilenameParserTestCase("3B_NameHere.idoc", 3, 'B', 'NameHere', None, '.idoc'),
            FilenameParserTestCase("4_NameHere_32.idoc", 4, None, 'NameHere', 32, '.idoc'),
            FilenameParserTestCase("5B_NameHere_32.idoc", 5, 'B', 'NameHere', 32, '.idoc'),
            FilenameParserTestCase("6_2343.idoc", 6, None, '2343', None, '.idoc'),
            FilenameParserTestCase("7 NameHere.idoc", 7, None, 'NameHere', None, '.idoc'),
            FilenameParserTestCase("8B NameHere.idoc", 8, 'B', 'NameHere', None, '.idoc'),
            FilenameParserTestCase("9 NameHere 32.idoc", 9, None, 'NameHere', 32, '.idoc'),
            FilenameParserTestCase("10B NameHere 32.idoc", 10, 'B', 'NameHere', 32, '.idoc'),
            FilenameParserTestCase("11 2343.idoc", 11, None, '2343', None, '.idoc'),
            FilenameParserTestCase("14 B NameHere.idoc", 14, 'B', 'NameHere', None, '.idoc'),
            FilenameParserTestCase("16 B.idoc", 16, 'B', None, None, '.idoc'),
            FilenameParserTestCase("18 B_NameHere.idoc", 18, 'B', 'NameHere', None, '.idoc'),
            FilenameParserTestCase("19 B_NameHere 32.idoc", 19, 'B', 'NameHere', 32, '.idoc'),
            FilenameParserTestCase("20 B This is a long description 32.idoc", 20, 'B', "This is a long description", 32, '.idoc'),
            FilenameParserTestCase("21 Long Desc 4 reasons", 21, None, "Long Desc 4 reasons", None, None),
            FilenameParserTestCase("22 Long Desc 4 reasons 32.png", 22, None, "Long Desc 4 reasons", 32, '.png'),
            FilenameParserTestCase("23 2343", 23, None, '2343', None, None),
            FilenameParserTestCase("24 B", 24, 'B', None, None, None), #Two digit section number with a letter
            FilenameParserTestCase("25B", 25, 'B', None, None, None), #Two digit section number with a letter
            ]
            
        for test_case in test_cases:
            d = nornir_buildmanager.importers.shared.ParseMetadataFromFilename(test_case.string)
            
            if d is not None:
                output = "{:<22}".format(test_case.string)
            
                for val in d.items():
                    output += "{:<18}".format("{0}: {1}".format(val[0], val[1]))
                
                print(output)
                self._CheckOutputDict(d, test_case)
            else:
                print("{:<24}: Parser Error".format(test_case.string))
                
    def _CheckOutputDict(self, d, test_case):
        '''This is a bit crude because my test case doesn't set expected values...'''
        if 'Number' in d:
            n = d['Number'] 
            self.assertEqual(n, test_case.Number, f'Unexpected section number {n} from "{test_case.string}" got {d}')
        else:
            self.assertIsNone(test_case.Number, 'Missing section number')
        
        if 'Version' in d:
            v = d['Version']
            expected = test_case.Version
            if expected is None:
                expected = '\0'  # The default value used for sorting purposes
            self.assertEqual(v, expected, f'Unexpected version {v} from "{test_case.string}" got {d}')
        else:
            self.assertIsNone(test_case.Version, 'Missing Version')
        
        if 'Name' in d:
            n = d['Name']
            self.assertEqual(n, test_case.Name, f'Unexpected name {n} from "{test_case.string}" got {d}')
        else:
            self.assertIsNone(test_case.Name, 'Missing Name')
            
        if 'Downsample' in d:
            ds = d['Downsample']
            self.assertEqual(ds, test_case.Downsample, f'Unexpected downsample {ds} from "{test_case.string}" got {d}')
        else:
            self.assertIsNone(test_case.Downsample, 'Missing Downsample')
            
        if 'Extension' in d:
            ext = d['Extension']
            self.assertEqual(ext, test_case.Extension, f'Unexpected extension {ext} from "{test_case.string}" got {d}')
        else:
            self.assertIsNone(test_case.Extension, 'Missing Extension')


if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
