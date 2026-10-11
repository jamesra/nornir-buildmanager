"""Badly named PMG and section-image files fail with an error that names the file.

The importers once carried a TODO to wrap ``ParseFilename`` in a try/except. It already prints the
mapping usage and raises naming the file, so these tests pin that contract. The PMG ``None`` branch
also had a literal ``%s`` in its message.
"""

import unittest
from unittest import mock

from nornir_buildmanager.importers import filenameparser, pmg, sectionimage


def _volume() -> mock.MagicMock:
    """Return a volume stand-in whose UpdateOrAddChild yields the [added, node] pair."""
    volume = mock.MagicMock()
    volume.UpdateOrAddChild.return_value = [True, mock.MagicMock()]
    return volume


class TestBadFilenameErrors(unittest.TestCase):
    """Error messages for files the filename parser rejects."""

    def test_pmg_filename_with_too_few_parts_names_the_file(self) -> None:
        """A PMG path without enough underscore fields raises naming the file."""
        with mock.patch("builtins.print"), self.assertRaises(Exception) as ctx:
            pmg.PMGImport.ToMosaic(_volume(), "C:/data/bad.pmg", 2.18)
        self.assertIn("Insufficient arguments in filename bad", str(ctx.exception))

    def test_section_image_filename_with_too_many_parts_names_the_file(self) -> None:
        """A section image path with extra underscores raises naming the file."""
        with mock.patch("builtins.print"), self.assertRaises(Exception) as ctx:
            sectionimage.SectionImage.ToMosaic(_volume(), "C:/data/1_2_3_4_5_6.png", 2.18)
        self.assertIn("Too many underscores in filename 1_2_3_4_5_6", str(ctx.exception))

    def test_section_image_non_numeric_section_names_the_parameter(self) -> None:
        """A non-numeric Section field raises naming the parameter."""
        with mock.patch("builtins.print"), self.assertRaises(Exception) as ctx:
            filenameparser.ParseFilename("C:/data/abc_chan.png", sectionimage.imageNameMappings)
        self.assertIn('Cannot parse parameter "Section"', str(ctx.exception))

    def test_pmg_unparseable_section_message_has_path_and_no_format_token(self) -> None:
        """The None branch interpolates the path instead of leaving a literal %s."""
        path = "C:/data/odd.pmg"
        with mock.patch.object(pmg, "ParseFilename", return_value=None), self.assertRaises(Exception) as ctx:
            pmg.PMGImport.ToMosaic(_volume(), path, 2.18)
        message = str(ctx.exception)
        self.assertNotIn("%s", message)
        self.assertEqual(f"Could not parse section from PMG filename: {path}", message)


if __name__ == "__main__":
    unittest.main()
