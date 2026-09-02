"""Regressions for #199: StomPreviewOutputInterceptor error handling."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from nornir_buildmanager.operations.block import StomPreviewOutputInterceptor
from nornir_shared import prettyoutput


class TestStomPreviewInterceptor(unittest.TestCase):
    def test_malformed_line_is_logged_not_swallowed_silently(self) -> None:
        """#199: AttributeError on a bad line must surface via prettyoutput.Log."""
        interceptor = StomPreviewOutputInterceptor(proc=None)
        interceptor.Output = [object()]  # no .lower()

        with mock.patch.object(prettyoutput, 'Log') as log:
            interceptor.Parse(None)

        messages = ' '.join(str(call.args[0]) for call in log.call_args_list if call.args)
        self.assertIn('malformed', messages.lower())

    def test_temp_rename_gives_up_after_max_attempts(self) -> None:
        """#199: colliding temp names must not spin forever."""
        with tempfile.TemporaryDirectory() as temp_dir:
            one = os.path.join(temp_dir, 'out0.tif')
            two = os.path.join(temp_dir, 'out1.tif')
            for path in (one, two):
                with open(path, 'wb') as handle:
                    handle.write(b'x')

            interceptor = StomPreviewOutputInterceptor(proc=None)
            interceptor.Output = [
                'loading section_a.png',
                'saving ' + one,
                'loading section_b.png',
                'saving ' + two,
            ]

            with mock.patch.object(os.path, 'exists', return_value=True), \
                    mock.patch.object(prettyoutput, 'Log'):
                with self.assertRaises(RuntimeError) as ctx:
                    interceptor.Parse(None)

            self.assertIn('unique temp names', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
