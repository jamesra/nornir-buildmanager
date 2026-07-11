"""Tests for early dashboard run metadata published from build.py."""

import unittest
from unittest import mock

import nornir_buildmanager.build as build


class TestEarlyRunMeta(unittest.TestCase):
    def test_publish_early_run_meta_from_args_uses_pipeline_name(self) -> None:
        args = mock.Mock(PipelineName="Assemble", command="Assemble", volumepath="/storage4/RPC3")
        with mock.patch("nornir_buildmanager.build.prettyoutput.publish_early_run_meta") as publish:
            build._publish_early_run_meta_from_args(args)
        publish.assert_called_once_with(pipeline="Assemble", volumepath="/storage4/RPC3")

    def test_publish_early_run_meta_from_args_uses_command_for_utilities(self) -> None:
        args = mock.Mock(spec=["command", "volumepath"])
        args.command = "RecoverLinks"
        args.volumepath = "/storage4/RPC3"
        with mock.patch("nornir_buildmanager.build.prettyoutput.publish_early_run_meta") as publish:
            build._publish_early_run_meta_from_args(args)
        publish.assert_called_once_with(pipeline="RecoverLinks", volumepath="/storage4/RPC3")

    def test_publish_early_run_meta_from_args_skips_when_incomplete(self) -> None:
        args = mock.Mock(spec=["command"])
        args.command = "help"
        with mock.patch("nornir_buildmanager.build.prettyoutput.publish_early_run_meta") as publish:
            build._publish_early_run_meta_from_args(args)
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
