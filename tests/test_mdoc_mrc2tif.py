"""Regression for #204: mrc2tif without shell=True; case-insensitive TIFF listing."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from nornir_buildmanager.importers import mdoc


class TestMrc2TifInvocation(unittest.TestCase):
    def test_run_mrc2tif_uses_argv_list_without_shell(self) -> None:
        """#204: paths with spaces must be separate argv elements, not shell=True."""
        st_path = r'D:\data set\stack.st'
        out_dir = r'D:\data set\Unpack'
        with mock.patch.object(mdoc.subprocess, 'run') as run:
            mdoc._run_mrc2tif(st_path, out_dir)
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['mrc2tif', st_path, out_dir])
        self.assertFalse(kwargs.get('shell', False))


class TestListMrc2TifOutputs(unittest.TestCase):
    def test_prefers_dot_prefixed_and_matches_uppercase_ext(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dotted = os.path.join(temp_dir, '.001.TIF')
            plain = os.path.join(temp_dir, '2.tif')
            for path in (dotted, plain):
                with open(path, 'wb') as handle:
                    handle.write(b'x')
            found = mdoc._list_mrc2tif_outputs(temp_dir)
            self.assertEqual(found, [dotted])

    def test_falls_back_to_plain_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plain = os.path.join(temp_dir, '3.TIF')
            with open(plain, 'wb') as handle:
                handle.write(b'x')
            self.assertEqual(mdoc._list_mrc2tif_outputs(temp_dir), [plain])


if __name__ == '__main__':
    unittest.main()
