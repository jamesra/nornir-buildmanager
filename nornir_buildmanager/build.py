"""

*Note*: Certain arguments support regular expressions.  See the python :py:mod:`re` module for instructions on how to construct appropriate regular expressions.

.. argparse::
   :module: nornir_buildmanager.build
   :func: BuildParserRoot
   :prog: nornir_build volumepath

"""

import argparse
import logging
import os
import sys

import matplotlib

import nornir_buildmanager.volumemanager.volumemanager
import nornir_buildmanager.pipelinemanager as pipelinemanager
import nornir_imageregistration

# Nornir build must use a backend that does not allocate windows in the GUI should be used.
# Otherwise bugs will appear in multi-threaded environments
if 'DEBUG' not in os.environ:
    try:
        matplotlib.use('QtAgg')
    except ImportError:
        matplotlib.use('Agg')  # Fallback to non-interactive backend

import matplotlib.pyplot as plt

plt.ioff()

import nornir_buildmanager
from nornir_buildmanager import *
from nornir_shared.misc import SetupLogging, lowpriority
from nornir_shared.tasktimer import TaskTimer
import pkgutil

import nornir_shared.prettyoutput as prettyoutput

CommandParserDict = {}


def _AddParserRootArguments(parser: argparse.ArgumentParser):
    """Add global flags shared by all build subcommands."""
    parser.add_argument('-debug',
                        action='store_true',
                        required=False,
                        default=False,
                        help='If true any exceptions raised by pipelines are not handled.',
                        dest='debug')

    parser.add_argument('-lowpriority', '-lp',
                        action='store_true',
                        required=False,
                        default=False,
                        help='Run the build with lower priority.  The machine may be more responsive at the expense of much slower builds. 3x-5x slower in tests.',
                        dest='lowpriority')

    parser.add_argument('-verbose',
                        action='store_true',
                        required=False,
                        default=False,
                        help='Provide additional output',
                        dest='verbose')

    parser.add_argument('-computational_library',
                        choices=['cupy', 'numpy', 'detect'],
                        required=False,
                        default='detect',
                        help='If not specified, cupy will be used if a nVidia GPU is present.  Otherwise, force cupy (GPU) or numpy (CPU) use.',
                        dest='computational_library')


#     parser.add_argument('-recover',
#                         action='store_true',
#                         required=False,
#                         default=False,
#                         help='Used to recover missing meta-data.  This searches child directories for VolumeData.xml files and re-links them to the parent element in volume path.  This command does not recurse and does not need to be run on the top-level volume directory.',
#                         dest='verbose')

def _AddRecoverNotesParser(root_parser: argparse.ArgumentParser, subparsers):
    recover_parser = subparsers.add_parser('RecoverNotes',
                                           help='Used to recover or update notes files in a folder.  This searches a path for *.txt files and creates/updates a notes element with the information in the file.', )
    recover_parser.set_defaults(func=call_recover_import_meta_data, parser=root_parser)

    recover_parser.add_argument('volumepath',
                                action='store',
                                type=str,
                                help='The path to the volume')

    recover_parser.add_argument('-save',
                                action='store_true',
                                required=False,
                                default=False,
                                help='Set this flag to save the VolumeData.xml files with the located linked elements included.',
                                dest='save_restoration')


def _AddRecoverParser(root_parser: argparse.ArgumentParser, subparsers):
    recover_parser = subparsers.add_parser('RecoverLinks',
                                           help='Used to recover missing meta-data.  This searches child directories for VolumeData.xml files and re-links them to the parent element in volume path.  This command does not recurse and does not need to be run on the top-level volume directory.', )
    recover_parser.set_defaults(func=call_recover_links, parser=root_parser)

    recover_parser.add_argument('volumepath',
                                action='store',
                                type=str,
                                help='The path to the volume')

    recover_parser.add_argument('-recurse',
                                action='store_true',
                                required=False,
                                default=False,
                                help='Set this flag to include sub-directories',
                                dest='recurse')

    recover_parser.add_argument('-save',
                                action='store_true',
                                required=False,
                                default=False,
                                help='Set this flag to save the VolumeData.xml files with the located linked elements included.',
                                dest='save_restoration')


def _AddXMLRepairParser(root_parser: argparse.ArgumentParser, subparsers):
    recover_parser = subparsers.add_parser('RepairXML',
                                           help='Fixes an issue where XML files have extra characters after the closing tag.  Should only apply to data before Dec 2023', )
    recover_parser.set_defaults(func=call_repair_xml, parser=root_parser)

    recover_parser.add_argument('volumepath',
                                action='store',
                                type=str,
                                help='The path to the volume')


def _GetPipelineXMLPath() -> str:
    if __spec__ is None:
        return os.path.join(os.path.dirname(__file__), 'config', 'Pipelines.xml')
    else:
        return pkgutil.get_data(__name__, os.path.join('config', 'Pipelines.xml'))  # type: ignore[return-value]


def BuildParserRoot() -> argparse.ArgumentParser:
    # conflict_handler = 'resolve' replaces old arguments with new if both use the same option flag
    parser = argparse.ArgumentParser('Buildscript', conflict_handler='resolve',
                                     description='Options available to all build commands. Specific pipelines extend this argument list.',
                                     epilog='Examples:\n'
                                            '  nornir-build ImportIDoc /data/volume ImportDir=/data/idoc\n'
                                            '  nornir-build -debug -computational_library cupy Mosaic /data/volume -Sections 1-10\n'
                                            '  nornir-build help Mosaic')
    _AddParserRootArguments(parser)

    # Create subparsers for commands
    pipeline_subparsers = parser.add_subparsers(title='Commands', dest='command')

    # Add a special help command that doesn't require volumepath
    help_parser = pipeline_subparsers.add_parser('help', help='Show help for a specific command')
    help_parser.add_argument('command_name', nargs='?', help='Name of the command to show help for')
    help_parser.set_defaults(func=print_help, parser=parser)

    _AddRecoverParser(parser, pipeline_subparsers)
    _AddRecoverNotesParser(parser, pipeline_subparsers)
    _AddXMLRepairParser(parser, pipeline_subparsers)
    _AddPipelineParsers(pipeline_subparsers)

    return parser


def _AddPipelineParsers(subparsers: argparse._SubParsersAction):
    PipelineXML = _GetPipelineXMLPath()
    # Load the element tree once and pass it to the later functions so we aren't parsing the XML text in the loop
    PipelineTree = pipelinemanager.PipelineManager.LoadPipelineXML(PipelineXML)

    for pipeline_name in pipelinemanager.PipelineManager.ListPipelines(PipelineTree):
        pipeline = pipelinemanager.PipelineManager.Load(PipelineTree, pipeline_name)

        pipeline_parser = subparsers.add_parser(pipeline_name, help=pipeline.Help, epilog=pipeline.Epilog)  # type: ignore[union-attr]

        # Add volumepath as first positional argument
        pipeline_parser.add_argument('volumepath',
                                     action='store',
                                     type=str,
                                     help='The path to the volume')

        pipeline.GetArgParser(pipeline_parser, IncludeGlobals=True)  # type: ignore[union-attr]

        pipeline_parser.set_defaults(func=call_pipeline, PipelineXmlFile=_GetPipelineXMLPath(),
                                     PipelineName=pipeline_name)

        CommandParserDict[pipeline_name] = pipeline_parser


def print_help(args):
    if not hasattr(args, 'parser'):
        print("Error: Parser reference not found")
        return

    if not hasattr(args, 'command_name') or args.command_name is None:
        args.parser.print_help()
    elif args.command_name in CommandParserDict:
        parser = CommandParserDict[args.command_name]
        parser.print_help()
    else:
        print(f"Unknown command: {args.command_name}")
        args.parser.print_help()


def call_recover_links(args):
    """This function checks for missing link elements in a volume and adds them back to the volume"""
    volumeObj = nornir_buildmanager.volumemanager.volumemanager.VolumeManager.Load(args.volumepath)
    volumeObj.RepairMissingLinkElements(recurse=args.recurse)  # type: ignore[union-attr]

    if args.save_restoration:
        volumeObj.Save()  # type: ignore[union-attr]
        prettyoutput.Log("Recovered links saved (if found).")
    else:
        prettyoutput.Log("Save flag not set, recovered links not saved.")


def call_repair_xml(args):
    """Repair malformed VolumeData XML files with trailing content.

    This migration-style repair targets older metadata where characters were written
    after the closing XML tag (primarily pre-Dec 2023 data sets).
    """
    volumeObj = nornir_buildmanager.volumemanager.volumemanager.VolumeManager.Load(args.volumepath)
    nornir_buildmanager.operations.migration.RepairCroppedXMLFilesInElement(volumeObj)  # type: ignore[arg-type]


def call_recover_import_meta_data(args):
    """Recover notes metadata from text files under the target volume path."""
    volumeObj = nornir_buildmanager.volumemanager.volumemanager.VolumeManager.Load(args.volumepath)
    notesAdded = nornir_buildmanager.importers.shared.TryAddNotes(volumeObj, volumeObj.FullPath, None)  # type: ignore[union-attr]

    if not notesAdded:
        prettyoutput.Log(f"No notes recovered from {volumeObj.FullPath}.")  # type: ignore[union-attr]
        return

    if notesAdded and args.save_restoration:
        volumeObj.Save(recurse=False)  # type: ignore[union-attr]
        prettyoutput.Log("Recovered notes file saved.")
    else:
        prettyoutput.Log("Save flag not set, recovered notes, but not saved.")


def call_pipeline(args):
    pipelinemanager.PipelineManager.RunPipeline(PipelineXmlFile=args.PipelineXmlFile, PipelineName=args.PipelineName,
                                                args=args)


def _run_pipeline_segment(args: argparse.Namespace, volume_tree=None, flush_at_boundary: bool = False):
    """Run one pipeline segment, optionally reusing an in-memory volume tree."""
    tree = pipelinemanager.PipelineManager.RunPipeline(
        PipelineXmlFile=args.PipelineXmlFile,
        PipelineName=args.PipelineName,
        args=args,
        volume_tree=volume_tree,
    )
    if flush_at_boundary and tree is not None:
        nornir_buildmanager.volumemanager.volumemanager.VolumeManager.Save(tree)
    return tree


def _GetFromNamespace(ns, attribname, default=None):
    if attribname in ns:
        return getattr(ns, attribname)
    else:
        return default


def _publish_early_run_meta_from_args(args: argparse.Namespace) -> None:
    """Publish retained dashboard meta as soon as CLI args are known.

    Uses ``PipelineName`` when present (pipeline commands); otherwise ``command``
    (utilities such as RecoverLinks). Skips when volumepath is missing.
    """
    volumepath = getattr(args, 'volumepath', None)
    if not volumepath:
        return

    pipeline = getattr(args, 'PipelineName', None)
    if not pipeline:
        pipeline = getattr(args, 'command', None)
    if not pipeline:
        return

    prettyoutput.publish_early_run_meta(
        pipeline=pipeline,
        volumepath=volumepath,
        compute=os.environ.get('NORNIR_COMPUTATIONAL_LIBRARY'),
    )


def _publish_run_completion(succeeded: bool) -> None:
    """Publish retained final run status for the dashboard."""
    import time
    prettyoutput.publish_run_meta(
        status="completed" if succeeded else "failed",
        end_ts=time.time(),
    )


def InitLogging(buildArgs):
    """Initialize persistent logging for the current command invocation.

    Logging writes through ``nornir_shared.misc.SetupLogging``. When a
    ``volumepath`` is present and ``-debug`` is enabled, the log level is DEBUG;
    otherwise WARN is used.
    """
    #    nornir_shared.Misc.RunWithProfiler('Execute()', "C:/Temp/profile.pr")

    buildArgs = _ReorderArgs(list(buildArgs))

    parser = BuildParserRoot()

    (args, extraargs) = parser.parse_known_args(buildArgs)

    if 'volumepath' in args:
        if _GetFromNamespace(args, 'debug', False):
            SetupLogging(OutputPath=args.volumepath, Level=logging.DEBUG)
        else:
            SetupLogging(Level=logging.WARN)
    else:
        SetupLogging(Level=logging.WARN)


def init_computational_library(args: argparse.Namespace):
    """Select CPU/GPU computation backend and export process environment.

    Sets ``NORNIR_COMPUTATIONAL_LIBRARY`` and updates
    ``nornir_imageregistration``'s active backend.
    """
    if args.computational_library == 'detect':
        if nornir_imageregistration.HasCupy():
            args.computational_library = 'cupy'
        else:
            args.computational_library = 'numpy'
    else:
        args.computational_library = args.computational_library.lower()

    os.environ['NORNIR_COMPUTATIONAL_LIBRARY'] = args.computational_library
    nornir_imageregistration.SetActiveComputationLib(
        nornir_imageregistration.ComputationLib.cupy if args.computational_library == 'cupy' else nornir_imageregistration.ComputationLib.numpy)


def _GetValidCommands() -> list[str]:
    """Get list of all valid commands/pipelines."""
    commands = ['help', 'RecoverLinks', 'RecoverNotes', 'RepairXML']
    # Add pipeline commands from XML
    PipelineXML = _GetPipelineXMLPath()
    PipelineTree = pipelinemanager.PipelineManager.LoadPipelineXML(PipelineXML)
    commands.extend(pipelinemanager.PipelineManager.ListPipelines(PipelineTree))
    return commands


# Flags defined on the root parser only (see _AddParserRootArguments). Used to recognize
# [volumepath, <root flags...>, <command>, ...] test/harness argv and normalize to
# [<root flags...>, <command>, volumepath, ...] before subparser dispatch.
_ROOT_FLAGS_NO_VALUE = frozenset({'-debug', '-verbose', '-lowpriority', '-lp'})
_ROOT_FLAGS_WITH_VALUE = frozenset({'-computational_library'})


def _segment_is_root_only_flags(segment: list[str]) -> bool:
    """True if *segment* is a sequence of root-parser flags (and values for value-taking flags)."""
    j = 0
    while j < len(segment):
        t = segment[j]
        if t in _ROOT_FLAGS_NO_VALUE:
            j += 1
            continue
        if t in _ROOT_FLAGS_WITH_VALUE:
            if j + 1 >= len(segment):
                return False
            j += 2
            continue
        return False
    return True


def _leading_root_flag_segment_length(args: list[str]) -> int:
    """Return the length of a leading argv prefix consumed by root-parser flags."""
    j = 0
    while j < len(args):
        token = args[j]
        if token in _ROOT_FLAGS_NO_VALUE:
            j += 1
            continue
        if token in _ROOT_FLAGS_WITH_VALUE:
            if j + 1 >= len(args):
                break
            j += 2
            continue
        break
    return j


def _ReorderArgs(args: list[str]) -> list[str]:
    """Reorder argv so root flags and subcommand precede volumepath (argparse subparser layout).

    Accepts legacy test orderings:
    - ``[volumepath, command, ...]`` (swap first two when command is second)
    - ``[volumepath, -debug, ..., command, ...]`` (move root flags + command before volumepath)
    - ``[-debug, ..., volumepath, command, ...]`` (launch.json / flags-before-path order)

    Leaves canonical ``[flags..., command, volumepath, ...]`` unchanged.
    """
    if not args:
        return args

    valid_commands = frozenset(_GetValidCommands())

    # Command-first: nothing to do
    if args[0] in valid_commands:
        return args

    # [volumepath, command, ...] — do not swap when args[0] is a flag (e.g. -debug ImportPMG vol)
    if len(args) > 1 and not args[0].startswith('-') and args[1] in valid_commands:
        return [args[1], args[0]] + args[2:]

    # [volumepath, <root flags...>, command, <tail>]
    if not args[0].startswith('-'):
        volumepath = args[0]
        for i in range(2, len(args)):
            if args[i] not in valid_commands:
                continue
            middle = args[1:i]
            if _segment_is_root_only_flags(middle):
                return middle + [args[i], volumepath] + args[i + 1 :]

    # [<root flags...>, volumepath, command, <tail>] — e.g. launch.json TEM configs
    prefix_len = _leading_root_flag_segment_length(args)
    if prefix_len < len(args):
        rest = args[prefix_len:]
        if len(rest) > 1 and not rest[0].startswith('-') and rest[1] in valid_commands:
            return args[:prefix_len] + [rest[1], rest[0]] + rest[2:]

    return args


def _SplitChainSegments(buildArgs: list[str]) -> list[list[str]]:
    """Split argv on ``--then`` into per-pipeline segments."""
    segments: list[list[str]] = []
    current: list[str] = []
    for token in buildArgs:
        if token == '--then':
            if not current:
                raise ValueError('--then cannot precede the first pipeline segment')
            segments.append(current)
            current = []
        else:
            current.append(token)
    if not current:
        raise ValueError('no pipeline segment after final --then')
    segments.append(current)
    return segments


def _AppendTimingOutput(volumepath: str, timer: TaskTimer) -> None:
    """Append a timing record for one pipeline invocation to Timing.txt."""
    out_str = str(timer)
    prettyoutput.Log(out_str)
    time_text_full_path = os.path.join(volumepath, 'Timing.txt')
    try:
        with open(time_text_full_path, 'a') as output_file:
            output_file.writelines(out_str)
    except OSError:
        prettyoutput.Log('Could not write %s' % time_text_full_path)


def ExecuteChain(buildArgs: list[str]) -> None:
    """Run multiple pipelines sequentially in one process, separated by ``--then``."""
    segments = _SplitChainSegments(buildArgs)
    first_segment = _ReorderArgs(segments[0])

    InitLogging(first_segment)

    parser = BuildParserRoot()
    first_args = parser.parse_args(first_segment)

    if getattr(first_args, 'command', None) == 'help':
        first_args.func(first_args)
        return

    if not hasattr(first_args, 'volumepath') or not first_args.volumepath:
        parser.error("the following arguments are required: volumepath")

    if first_args.lowpriority:
        lowpriority()
        print("Warning, using low priority flag.  This can make builds much slower")

    if hasattr(first_args, 'computational_library'):
        init_computational_library(first_args)

    root_flags = first_segment[:_leading_root_flag_segment_length(first_segment)]
    volumepath = first_args.volumepath
    valid_commands = frozenset(_GetValidCommands())

    volume_tree = None
    succeeded = False
    try:
        for index, segment in enumerate(segments):
            if index > 0 and segment[0] not in valid_commands:
                parser.error(f"unknown pipeline in chain segment: {segment[0]}")

            if index == 0:
                segment_argv = first_segment
            else:
                segment_argv = _ReorderArgs(root_flags + [segment[0], volumepath] + segment[1:])

            args = parser.parse_args(segment_argv)
            _publish_early_run_meta_from_args(args)

            cmd_name = args.PipelineName
            timer = TaskTimer()
            try:
                timer.Start(cmd_name)
                volume_tree = _run_pipeline_segment(args, volume_tree=volume_tree, flush_at_boundary=True)
            finally:
                timer.End(cmd_name)
                _AppendTimingOutput(volumepath, timer)
        succeeded = True
    finally:
        _publish_run_completion(succeeded)


def Execute(buildArgs=None):
    """Run the nornir-build command line entrypoint.

    The argv parser accepts canonical subcommand ordering and legacy test
    ordering that starts with ``volumepath``.
    """
    # Spend more time on each thread before switching
    # sys.setswitchinterval(500)

    if buildArgs is None:
        buildArgs = sys.argv[1:]

    if '--then' in buildArgs:
        ExecuteChain(buildArgs)
        return

    # Reorder arguments to support both command-first and volumepath-first patterns
    buildArgs = _ReorderArgs(buildArgs)

    # Change the temp directory if nornir specifies an alternate in the environment variables
    if 'NORNIR_TEMP_DIR' in os.environ:
        temp_dir = os.environ['NORNIR_TEMP_DIR']
        os.makedirs(temp_dir, exist_ok=True)
        os.environ['TEMP'] = temp_dir
        os.environ['TMP'] = temp_dir
        os.environ['TMPDIR'] = temp_dir

    InitLogging(buildArgs)

    Timer = TaskTimer()

    parser = BuildParserRoot()

    args = parser.parse_args(buildArgs)

    # Help command does not require volumepath or timing output.
    if getattr(args, 'command', None) == 'help':
        args.func(args)
        return

    # For all other commands, require volumepath
    if not hasattr(args, 'volumepath') or not args.volumepath:
        parser.error("the following arguments are required: volumepath")

    if args.lowpriority:
        lowpriority()
        print("Warning, using low priority flag.  This can make builds much slower")

    if hasattr(args, 'computational_library'):
        init_computational_library(args)

    _publish_early_run_meta_from_args(args)

    cmd_name = None
    if hasattr(args, 'PipelineName'):
        cmd_name = args.PipelineName
    elif len(buildArgs) >= 2:
        cmd_name = buildArgs[1]

    succeeded = False
    try:
        if cmd_name is not None:
            Timer.Start(cmd_name)

        args.func(args)
        succeeded = True

    finally:
        if cmd_name is not None:
            Timer.End(cmd_name)

        _AppendTimingOutput(args.volumepath, Timer)
        _publish_run_completion(succeeded)


if __name__ == '__main__':
    Execute()
