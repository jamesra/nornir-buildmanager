import contextlib
import io
import unittest
from xml.etree import ElementTree

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

    def test_EnumArgumentParsesMemberNames(self):
        """An enum-typed Pipelines.xml argument accepts member names (AlignSections -Method, 2026-11-21)."""
        from nornir_imageregistration.settings import SliceToSliceMethod
        node = ElementTree.fromstring(
            '<Arguments><Argument flag="-Method" dest="Method" required="False" default="LogPolar" '
            'type="nornir_imageregistration.settings.SliceToSliceMethod"/></Arguments>')
        parser = argparsexml.CreateOrExtendParserForArguments(node.findall('Argument'))
        self.assertIs(parser.parse_args([]).Method, SliceToSliceMethod.LogPolar)
        for text in ('BruteForce', 'bruteforce', 'SliceToSliceMethod.BruteForce'):
            self.assertIs(parser.parse_args(['-Method', text]).Method, SliceToSliceMethod.BruteForce)
        self.assertIn('{BruteForce,LogPolar}', parser.format_help())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(['-Method', 'Nope'])
