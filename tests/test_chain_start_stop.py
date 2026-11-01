"""Tests for ``-start-at`` / ``-stop-after`` on ``--then`` pipeline chains in ``build.py``."""
from __future__ import annotations

import argparse
import tempfile
import unittest
from typing import Any
from unittest import mock

from nornir_buildmanager import build

#: The TEMAlign.sh chain, by pipeline name (16 steps, three names repeated).
TEMALIGN_NAMES: list[str] = [
    'CreateBlobFilter', 'AlignSections', 'AssembleStosOverlays', 'SelectBestRegistrationChain',
    'RefineSectionAlignment', 'AssembleStosOverlays', 'CreateVikingXML', 'RefineSectionAlignment',
    'SliceToVolume', 'ScaleVolumeTransforms', 'LinearizeVolume', 'CreateVikingXML', 'MosaicToVolume',
    'Assemble', 'MosaicReport', 'ExportImages',
]


class TestResolveChainStep(unittest.TestCase):
    """Selector parsing: step number, Name#k, bare Name, and the error cases."""

    def test_step_number(self) -> None:
        self.assertEqual(build._resolve_chain_step('1', TEMALIGN_NAMES, '-start-at'), 0)
        self.assertEqual(build._resolve_chain_step('16', TEMALIGN_NAMES, '-start-at'), 15)
        self.assertEqual(build._resolve_chain_step(' 9 ', TEMALIGN_NAMES, '-start-at'), 8)

    def test_name_with_occurrence(self) -> None:
        self.assertEqual(build._resolve_chain_step('AssembleStosOverlays#1', TEMALIGN_NAMES, '-start-at'), 2)
        self.assertEqual(build._resolve_chain_step('AssembleStosOverlays#2', TEMALIGN_NAMES, '-start-at'), 5)
        self.assertEqual(build._resolve_chain_step('CreateVikingXML#2', TEMALIGN_NAMES, '-start-at'), 11)
        # Name#1 is valid for a name that occurs once.
        self.assertEqual(build._resolve_chain_step('SliceToVolume#1', TEMALIGN_NAMES, '-start-at'), 8)

    def test_names_ignore_case(self) -> None:
        self.assertEqual(build._resolve_chain_step('createvikingxml#2', TEMALIGN_NAMES, '-start-at'), 11)
        self.assertEqual(build._resolve_chain_step('SELECTBESTREGISTRATIONCHAIN', TEMALIGN_NAMES, '-stop-after'), 3)
        with self.assertRaises(ValueError) as ctx:
            build._resolve_chain_step('refinesectionalignment', TEMALIGN_NAMES, '-start-at')
        self.assertIn('RefineSectionAlignment#1', str(ctx.exception), 'choices use the chain spelling')

    def test_unique_bare_name(self) -> None:
        self.assertEqual(build._resolve_chain_step('SelectBestRegistrationChain', TEMALIGN_NAMES, '-stop-after'), 3)
        self.assertEqual(build._resolve_chain_step('CreateBlobFilter', TEMALIGN_NAMES, '-start-at'), 0)

    def test_ambiguous_bare_name_lists_choices(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build._resolve_chain_step('RefineSectionAlignment', TEMALIGN_NAMES, '-start-at')
        message = str(ctx.exception)
        self.assertIn('RefineSectionAlignment#1 (step 5)', message)
        self.assertIn('RefineSectionAlignment#2 (step 8)', message)
        self.assertIn('-start-at', message)

    def test_out_of_range_number(self) -> None:
        for selector in ('0', '17', '100'):
            with self.subTest(selector=selector), self.assertRaises(ValueError) as ctx:
                build._resolve_chain_step(selector, TEMALIGN_NAMES, '-stop-after')
            self.assertIn('out of range', str(ctx.exception))

    def test_occurrence_out_of_range(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build._resolve_chain_step('AssembleStosOverlays#3', TEMALIGN_NAMES, '-start-at')
        self.assertIn('AssembleStosOverlays#2 (step 6)', str(ctx.exception))
        for selector in ('AssembleStosOverlays#0', 'AssembleStosOverlays#x', 'AssembleStosOverlays#'):
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                build._resolve_chain_step(selector, TEMALIGN_NAMES, '-start-at')

    def test_unknown_name(self) -> None:
        for selector in ('Mosaic', 'Mosaic#1', 'createblob'):  # names ignore case but must be whole
            with self.subTest(selector=selector), self.assertRaises(ValueError) as ctx:
                build._resolve_chain_step(selector, TEMALIGN_NAMES, '-start-at')
            self.assertIn('no pipeline named', str(ctx.exception))


class TestResolveChainRange(unittest.TestCase):
    """Combining ``-start-at`` and ``-stop-after``."""

    def test_defaults_cover_whole_chain(self) -> None:
        self.assertEqual(build._resolve_chain_range(TEMALIGN_NAMES, None, None), (0, 15))

    def test_start_and_stop(self) -> None:
        self.assertEqual(build._resolve_chain_range(TEMALIGN_NAMES, '2', '9'), (1, 8))
        self.assertEqual(build._resolve_chain_range(TEMALIGN_NAMES, 'AssembleStosOverlays#2', None), (5, 15))
        self.assertEqual(build._resolve_chain_range(TEMALIGN_NAMES, None, 'SelectBestRegistrationChain'), (0, 3))
        self.assertEqual(build._resolve_chain_range(TEMALIGN_NAMES, '4', '4'), (3, 3))

    def test_stop_before_start_is_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build._resolve_chain_range(TEMALIGN_NAMES, '9', 'AlignSections')
        self.assertIn('comes before', str(ctx.exception))

    def test_single_pipeline(self) -> None:
        self.assertEqual(build._resolve_chain_range(['Mosaic'], '1', '1'), (0, 0))
        self.assertEqual(build._resolve_chain_range(['Mosaic'], 'Mosaic', None), (0, 0))
        with self.assertRaises(ValueError):
            build._resolve_chain_range(['Mosaic'], '2', None)


class TestRootFlagReorder(unittest.TestCase):
    """The TEM scripts put the new flags before the volume path; reorder must keep them."""

    def test_flags_before_volumepath(self) -> None:
        argv = ['-debug', '-start-at', '2', '-stop-after', 'Mosaic', '/vol', 'Prune', '-Channels', 'TEM']
        self.assertEqual(build._ReorderArgs(argv),
                         ['-debug', '-start-at', '2', '-stop-after', 'Mosaic', 'Prune', '/vol', '-Channels', 'TEM'])
        self.assertEqual(build._leading_root_flag_segment_length(argv), 5)


class _ChainHarness(unittest.TestCase):
    """Run ``build.Execute`` with pipeline execution and MQTT publishing mocked out."""

    volumepath: str
    ran: list[str]
    events: list[dict[str, Any]]
    logs: list[str]

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.volumepath = self._tmp.name
        self.ran = []
        self.events = []
        self.logs = []

        def fake_run(args: argparse.Namespace, volume_tree: Any = None, flush_at_boundary: bool = False) -> Any:
            self.ran.append(args.PipelineName)
            return volume_tree

        def fake_event(event: str, **fields: Any) -> None:
            self.events.append({'event': event, **fields})

        patches = [
            mock.patch.object(build, '_run_pipeline_segment', side_effect=fake_run),
            mock.patch.object(build, 'InitLogging'),
            mock.patch.object(build, 'init_computational_library'),
            mock.patch.object(build, '_AppendTimingOutput'),
            mock.patch.object(build.prettyoutput, 'publish_run_event', side_effect=fake_event),
            mock.patch.object(build.prettyoutput, 'publish_run_meta'),
            mock.patch.object(build.prettyoutput, 'publish_early_run_meta'),
            mock.patch.object(build.prettyoutput, 'Log', side_effect=lambda msg, *a, **k: self.logs.append(str(msg))),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.early_meta = build.prettyoutput.publish_early_run_meta
        self.addCleanup(self._tmp.cleanup)

    def chain_argv(self, *root_flags: str) -> list[str]:
        """A four-step chain with AssembleStosOverlays twice, laid out like TEMAlign.sh."""
        return [*root_flags, self.volumepath,
                'AlignSections', '-NumAdjacentSections', '1', '-Downsample', '64',
                '--then', 'AssembleStosOverlays', '-StosGroup', 'StosBrute', '-Downsample', '64', '-StosMap', 'P',
                '--then', 'SelectBestRegistrationChain', '-StosGroup', 'StosBrute', '-Downsample', '64',
                '-InputStosMap', 'P', '-OutputStosMap', 'F',
                '--then', 'AssembleStosOverlays', '-StosGroup', 'Grid', '-Downsample', '32', '-StosMap', 'F']

    def skipped_tracks(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get('state') == 'skipped']

    def chain_progress(self) -> list[int]:
        return [e['current'] for e in self.events if e.get('track_id') == 'chain']


class TestChainRunnerSkips(_ChainHarness):
    """The chain runner runs only the selected steps and reports the rest as skipped."""

    def test_full_chain_unchanged(self) -> None:
        build.Execute(self.chain_argv('-debug'))
        self.assertEqual(self.ran, ['AlignSections', 'AssembleStosOverlays', 'SelectBestRegistrationChain',
                                    'AssembleStosOverlays'])
        self.assertEqual(self.skipped_tracks(), [])
        self.assertEqual(self.chain_progress(), [0, 1, 1, 2, 2, 3, 3, 4])

    def test_start_and_stop(self) -> None:
        build.Execute(self.chain_argv('-debug', '-start-at', '2', '-stop-after', 'SelectBestRegistrationChain'))
        self.assertEqual(self.ran, ['AssembleStosOverlays', 'SelectBestRegistrationChain'])

        skipped = self.skipped_tracks()
        self.assertEqual([e['label'] for e in skipped],
                         ['AlignSections (1/4) skipped', 'AssembleStosOverlays (4/4) skipped'])
        for event in skipped:
            self.assertEqual(event['event'], 'iterate_progress')
            self.assertEqual(event['depth'], 0)
            self.assertTrue(event['track_id'].startswith('pipeline:'))

        self.assertIn('Skipping 1/4 AlignSections (start-at 2)', self.logs)
        self.assertIn('Skipping 4/4 AssembleStosOverlays (stop-after 3)', self.logs)
        # Chain bar starts at the first run step and ends full once the tail is skipped.
        self.assertEqual(self.chain_progress(), [1, 2, 2, 3, 4])
        # The first step that runs owns the early run meta (start_ts), shown as "(2/4)".
        self.early_meta.assert_called_once()
        self.assertEqual(self.early_meta.call_args.kwargs['pipeline'], 'AssembleStosOverlays (2/4)')

    def test_start_at_name_occurrence(self) -> None:
        build.Execute(self.chain_argv('-start-at', 'AssembleStosOverlays#2'))
        self.assertEqual(self.ran, ['AssembleStosOverlays'])
        self.assertEqual(len(self.skipped_tracks()), 3)

    def test_ambiguous_name_fails_before_running(self) -> None:
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
            build.Execute(self.chain_argv('-start-at', 'AssembleStosOverlays'))
        self.assertEqual(self.ran, [])
        self.assertEqual(self.events, [])

    def test_stop_before_start_fails_before_running(self) -> None:
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
            build.Execute(self.chain_argv('-start-at', '3', '-stop-after', '2'))
        self.assertEqual(self.ran, [])

    def test_typo_in_skipped_segment_fails_before_running(self) -> None:
        argv = self.chain_argv('-start-at', '3')
        # Step 1 (skipped) gets an unknown flag.
        argv[argv.index('AlignSections') + 1] = '-NotAnArgument'
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
            build.Execute(argv)
        self.assertEqual(self.ran, [])


class TestSinglePipeline(_ChainHarness):
    """Without ``--then`` the run is step 1 of 1."""

    def single_argv(self, *root_flags: str) -> list[str]:
        return [*root_flags, self.volumepath, 'AlignSections', '-NumAdjacentSections', '1', '-Downsample', '64']

    def test_step_one_is_no_op(self) -> None:
        with mock.patch.object(build, 'call_pipeline') as call_pipeline:
            build.Execute(self.single_argv('-start-at', '1', '-stop-after', 'AlignSections'))
        call_pipeline.assert_called_once()

    def test_other_steps_are_errors(self) -> None:
        for flags in (('-start-at', '2'), ('-stop-after', 'Mosaic')):
            with self.subTest(flags=flags), mock.patch.object(build, 'call_pipeline') as call_pipeline, \
                    self.assertRaises(SystemExit), mock.patch('sys.stderr'):
                build.Execute(self.single_argv(*flags))
            call_pipeline.assert_not_called()


if __name__ == '__main__':
    unittest.main()
