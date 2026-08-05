"""Tests for TEMBuild iterate_progress helpers and ImportIDoc tracks."""

from __future__ import annotations

import unittest
from unittest import mock

from nornir_buildmanager.progress import report_iterate


class TestReportIterate(unittest.TestCase):
    def test_skips_zero_total(self) -> None:
        with mock.patch("nornir_buildmanager.progress.publish_run_event") as publish:
            report_iterate("t", 0, 0, "label")
            publish.assert_not_called()

    def test_publishes_iterate_progress_fields(self) -> None:
        with mock.patch("nornir_buildmanager.progress.publish_run_event") as publish:
            report_iterate("import_idoc:sections", 2, 5, "ImportIDoc", depth=0, section=3)
            publish.assert_called_once_with(
                "iterate_progress",
                current=2,
                total=5,
                depth=0,
                track_id="import_idoc:sections",
                label="ImportIDoc",
                section=3,
            )


class TestImportIDocProgress(unittest.TestCase):
    def test_import_reports_section_progress(self) -> None:
        from nornir_buildmanager.importers import idoc as idoc_mod

        section_a = mock.Mock()
        section_a.number = 1
        section_b = mock.Mock()
        section_b.number = 2
        found = [
            (section_a, ["a1.idoc", "a2.idoc"]),
            (section_b, ["b1.idoc"]),
        ]
        volume = mock.Mock()

        with mock.patch.object(idoc_mod, "find_sections", return_value=iter(found)):
            with mock.patch.object(idoc_mod.find, "find_section_candidates", return_value={}):
                with mock.patch.object(idoc_mod.nornir_buildmanager.importers, "GetFlipList", return_value=[]):
                    with mock.patch.object(
                            idoc_mod.nornir_buildmanager.importers,
                            "LoadHistogramCutoffs",
                            return_value={}):
                        with mock.patch.object(
                                idoc_mod.serialem_utils,
                                "get_import_cache_path",
                                return_value="/tmp"):
                            with mock.patch.object(idoc_mod.os.path, "exists", return_value=True):
                                with mock.patch.object(
                                        idoc_mod.SerialEMIDocImport,
                                        "ToMosaic",
                                        side_effect=lambda *a, **k: (x for x in (volume,))) as to_mosaic:
                                    with mock.patch.object(idoc_mod, "report_iterate") as report:
                                        with mock.patch.object(idoc_mod.nornir_pools, "ReleaseStagePools"):
                                            results = list(idoc_mod.Import(
                                                volume,
                                                ImportPath="/data",
                                                Min=0.1,
                                                Max=0.9,
                                            ))

        self.assertEqual(len(results), 3)
        self.assertEqual(to_mosaic.call_count, 3)
        currents = [c.args[1] for c in report.call_args_list]
        # Initial 0/N, then start+complete for each of 3 idocs → [0, 0,1, 1,2, 2,3]
        self.assertEqual(currents, [0, 0, 1, 1, 2, 2, 3])
        self.assertTrue(all(c.args[0] == "import_idoc:sections" for c in report.call_args_list))
        # Start-of-idoc events include path/element for the dashboard card.
        start_kwargs = report.call_args_list[1].kwargs
        self.assertEqual(start_kwargs.get("element"), "a1.idoc")
        self.assertEqual(start_kwargs.get("path"), "a1.idoc")


class TestPublishTaskProgress(unittest.TestCase):
    def test_prettyoutput_task_progress_emits_iterate(self) -> None:
        from nornir_shared import prettyoutput

        with mock.patch("nornir_shared.mqtt_telemetry.publish_run_event") as publish:
            prettyoutput.publish_task_progress("import_idoc:tiles", 4, 10, name="tiles")
            publish.assert_called_once()
            self.assertEqual(publish.call_args.args[0], "iterate_progress")
            self.assertEqual(publish.call_args.kwargs["track_id"], "import_idoc:tiles")
            self.assertEqual(publish.call_args.kwargs["current"], 4)
            self.assertEqual(publish.call_args.kwargs["total"], 10)
            self.assertEqual(publish.call_args.kwargs["depth"], 1)

    def test_prettyoutput_task_complete_emits_complete(self) -> None:
        from nornir_shared import prettyoutput

        with mock.patch("nornir_shared.mqtt_telemetry.publish_run_event") as publish:
            prettyoutput.publish_task_complete("import_idoc:tiles", 10)
            publish.assert_called_once_with(
                "iterate_progress_complete",
                track_id="import_idoc:tiles",
                total=10,
            )

    def test_task_progress_reporter_complete_publishes_progress_then_complete(self) -> None:
        from nornir_shared import prettyoutput

        with mock.patch.object(prettyoutput, "publish_task_progress") as progress:
            with mock.patch.object(prettyoutput, "publish_task_complete") as complete:
                reporter = prettyoutput.TaskProgressReporter(
                    "import_idoc:histogram", 3, name="hist", min_interval_s=0.0)
                reporter.start()
                reporter.update(1)
                reporter.complete()

        self.assertGreaterEqual(progress.call_count, 2)
        progress.assert_any_call(
            "import_idoc:histogram", 3, 3, name="hist",
            element=None, path=None, section=None)
        complete.assert_called_once_with("import_idoc:histogram", 3)


if __name__ == "__main__":
    unittest.main()
