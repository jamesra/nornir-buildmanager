import unittest
import nornir_buildmanager
import nornir_buildmanager.operations.migration


class MigrationTests(unittest.TestCase):

    test_xmls = (r"""<Volume>""",
                 r"""<Volume/>""",
                 r"""<Volume></Volume>""",
                 r"""  <Volume></Volume>""",
                 r"""  <Volume></Volume>  """,
                 r"""<Volume Name="RC1">""",
                 r"""<Volume Name="RC1"/>""",
                 r"""<Volume Name="RC1"></Volume>""",
                 r"""<Volume  Name="RC1"></Volume>""",
                 r"""<Volume Name="RC1"  ></Volume>""",
                 r"""<Volume Name="RC1"> </Volume>""",
                 r"""<Volume  Name="RC1"> </Volume>""",
                 r"""<Volume Name="RC1"  > </Volume>""",
                 r"""<Volume Name="RC1" CreationDate="2019-02-14 18:45:35"><Block></Block></Volume>""",
                 r"""<Volume Name="RC1" CreationDate="2019-02-14 18:45:35"><Block CreationDate="2019-02-14 18:45:35"></Block></Volume>""",
                 r"""<Volume Name="RC1" Path="path"></Volume>""",
                 r"""<Volume Name="RC1" Path= "path"></Volume>""",
                 r"""<Volume Name="RC1" Path ="path"></Volume>""",
                 r"""<Volume Name="RC1" Path ="path" ></Volume>""",
                 r"""<Volume Name="RC1" Path="path"></Volume>""",
                 r"""<Volume  Name="RC1" Path="path"></Volume>""",
                 r"""<Volume Name="RC1" Path="path"  ></Volume>""",
                 r"""<Volume Name="RC1" Path="path"><Block></Block></Volume>""",
                 r"""<Volume  Name="RC1" Path="path"> <Block></Block> </Volume>""",
                 r"""<Volume Name="RC1" Path="path"  > <Block></Block></Volume>""",
                 r"""<Volume Name="RC1" Path="path"><Block Name="TEM"></Block></Volume>""",
                 r"""<Volume  Name="RC1" Path="path"> <Block Name="TEM" ></Block> </Volume>""",
                 r"""<Volume Name="RC1" Path="path"  > <Block  Name= "TEM"></Block></Volume>""",
                 )

    def test_migration_re(self):

        self.run_xml_list(self.test_xmls)
        XMLHeader = r"""<?xml version="1.0" encoding="utf-8"?>"""

        #Prepend the standard XML file header and run tests again
        header_test_xmls = [XMLHeader + xml for xml in self.test_xmls]
        self.run_xml_list(header_test_xmls)

    def run_xml_list(self, input):
        re = nornir_buildmanager.operations.migration._XMLHeadTagParser

        for xml in input:
            print("Testing: " + xml)
            match = re.match(xml)
            self.assertIsNotNone(match, "Failed to match: " + xml)
            for group_name, group_index in re.groupindex.items():
                if group_name is not None:
                    print(f'\tFound group: {group_name} -> {match[group_name]}')

            self.assertTrue(match.group('Tag') == "Volume", "Failed to match: " + xml)


if __name__ == '__main__':
    unittest.main()
