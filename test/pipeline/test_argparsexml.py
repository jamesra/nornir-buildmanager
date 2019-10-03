
import unittest

import nornir_buildmanager.argparsexml as argparsexml


class ArgParseXMLTests(unittest.TestCase):

	def test_NumberList(self):

		output = argparsexml.IntegerList('1')
		self.assertEqual(output, [1])
		
		output = argparsexml.IntegerList(' 1')
		self.assertEqual(output, [1])
		
		output = argparsexml.IntegerList('1 ')
		self.assertEqual(output, [1])
		
		output = argparsexml.IntegerList('5-7')
		self.assertEqual(output, [5, 6, 7])
		
		output = argparsexml.IntegerList('5 -7')
		self.assertEqual(output, [5, 6, 7])
		
		output = argparsexml.IntegerList('5- 7')
		self.assertEqual(output, [5, 6, 7])
		
		output = argparsexml.IntegerList(' 5-7 ')
		self.assertEqual(output, [5, 6, 7])
		
		output = argparsexml.IntegerList('1, 3,5-7,9')
		self.assertEqual(output, [1, 3, 5, 6, 7, 9])
		
		output = argparsexml.IntegerPair('256')
		self.assertEqual(output, (256, 256))
		
		output = argparsexml.IntegerPair('512,256')
		self.assertEqual(output, (512, 256))
