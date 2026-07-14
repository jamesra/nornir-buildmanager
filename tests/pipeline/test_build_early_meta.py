"""Tests for early dashboard run metadata published from build.py."""

import unittest
from unittest import mock

import nornir_buildmanager.build as build


class TestEarlyRunMeta(unittest.TestCase):
    def test_publish_early_run_meta_from_args_uses_pipeline_name(self) -> None:
        args = mock.Mock(PipelineName="Assemble", command="Assemble", volumepath="/storage4/RPC3")
        with mock.patch("nornir_buildmanager.build.prettyoutput.publish_early_run_meta") as publish:
            with mock.patch.dict("os.environ", {"NORNIR_COMPUTATIONAL_LIBRARY": "cupy"}, clear=False):
                build._publish_early_run_meta_from_args(args)
        publish.assert_called_once_with(
            pipeline="Assemble", volumepath="/storage4/RPC3", compute="cupy")

    def test_publish_early_run_meta_from_args_uses_command_for_utilities(self) -> None:
        args = mock.Mock(spec=["command", "volumepath"])
        args.command = "RecoverLinks"
        args.volumepath = "/storage4/RPC3"
        with mock.patch("nornir_buildmanager.build.prettyoutput.publish_early_run_meta") as publish:
            with mock.patch.dict("os.environ", {}, clear=False):
                # Ensure compute comes from environ.get (may be None)
                build._publish_early_run_meta_from_args(args)
        self.assertEqual(publish.call_count, 1)
        kwargs = publish.call_args.kwargs
        self.assertEqual(kwargs["pipeline"], "RecoverLinks")
        self.assertEqual(kwargs["volumepath"], "/storage4/RPC3")
        self.assertIn("compute", kwargs)

    def test_publish_early_run_meta_from_args_skips_when_incomplete(self) -> None:
        args = mock.Mock(spec=["command"])
        args.command = "help"
        with mock.patch("nornir_buildmanager.build.prettyoutput.publish_early_run_meta") as publish:
            build._publish_early_run_meta_from_args(args)
        publish.assert_not_called()

    def test_publish_run_completion_completed(self) -> None:
        with mock.patch("nornir_buildmanager.build.prettyoutput.publish_run_meta") as publish:
            build._publish_run_completion(True)
        publish.assert_called_once()
        self.assertEqual(publish.call_args.kwargs["status"], "completed")
        self.assertIn("end_ts", publish.call_args.kwargs)

    def test_publish_run_completion_failed(self) -> None:
        with mock.patch("nornir_buildmanager.build.prettyoutput.publish_run_meta") as publish:
            build._publish_run_completion(False)
        self.assertEqual(publish.call_args.kwargs["status"], "failed")


if __name__ == "__main__":
    unittest.main()
