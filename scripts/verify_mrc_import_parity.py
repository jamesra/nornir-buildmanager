#!/usr/bin/env python3
"""Report whether the MRC header/flip fixes can change an existing import.

Read-only. Point it at one or more ``.mrc`` files (or a directory tree) and it
answers three questions per file, without importing anything:

1. **Signed-short parse** - does any extended-header short have the high bit
   set? Stage position, tilt angle, magnification and intensity are signed per
   https://bio3d.colorado.edu/imod/doc/mrc_format.txt ("The short integers are
   signed, except for piece coordinates"). If every raw value is < 32768 the
   signed and unsigned parses are bit-identical and the fix cannot move a tile.
2. **Stage-origin straddle** - a wrap shared by every tile is absorbed by
   ``Mosaic.TranslateToZeroOrigin``, so it is reported but not counted as a
   change. Only a section with mixed-sign stage coordinates moves its tiles
   relative to each other, and that is what gates a reimport.
3. **FlipList** - is there a ``FlipList.txt`` naming this section? Unlisted
   sections keep the historical baseline flip; listed sections now invert it,
   which is the one change that alters a section that imports correctly today.

Exit code is 0 when no file can change, 2 when at least one can, so it can gate
a reimport.

Usage:
  python verify_mrc_import_parity.py <mrc-file-or-dir> [...]
  python verify_mrc_import_parity.py D:\\data\\RC1 --json report.json
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from pathlib import Path

import nornir_buildmanager.importers
from nornir_buildmanager.importers import shared
from nornir_buildmanager.importers.mrc import (
    DEFAULT_FLIP_UD,
    MRCFile,
    MRCTileHeaderFlags,
    flip_ud_for_section,
)

SIGN_BIT = 0x8000

# Extended-header layout, in the order MRCTileHeader.Load consumes it.
# (flag, name, struct code, signed-per-spec)
FIELD_LAYOUT: list[tuple[int, str, str, bool]] = [
    (MRCTileHeaderFlags.TiltAngle, 'tilt_angle', 'H', True),
    (MRCTileHeaderFlags.PieceCoord, 'piece_coords', 'HHH', False),
    (MRCTileHeaderFlags.StageCoord, 'stage_coords', 'HH', True),
    (MRCTileHeaderFlags.Magnification, 'magnification', 'H', True),
    (MRCTileHeaderFlags.Intensity, 'intensity', 'H', True),
    (MRCTileHeaderFlags.Exposure, 'exposure', 'f', False),
]


def _raw_signed_fields(mrc: MRCFile, mrc_path: Path) -> dict[str, list[int]]:
    """Collect raw unsigned short values for each signed-per-spec field."""
    endian = '>' if mrc.IsBigEndian else '<'
    collected: dict[str, list[int]] = {}

    if mrc.tile_header_flags is None or mrc.tile_header_size is None or mrc.num_tiles is None:
        raise ValueError('MRC file has no extended tile headers')

    flags = int(mrc.tile_header_flags)
    header_size = int(mrc.tile_header_size)

    # MRCFile.mrc is an open handle; read through our own so we never disturb
    # its file position.
    with open(mrc_path, 'rb') as handle:
        for i_tile in range(int(mrc.num_tiles)):
            handle.seek(MRCFile.HeaderLength + (i_tile * header_size))
            record = handle.read(header_size)
            offset = 0
            for flag, name, code, signed in FIELD_LAYOUT:
                if not (flags & flag):
                    continue
                width = struct.calcsize(endian + code)
                if signed:
                    values = struct.unpack(endian + code, record[offset:offset + width])
                    collected.setdefault(name, []).extend(int(v) for v in values)
                offset += width
    return collected


def _flip_list_for(mrc_path: Path) -> list[int] | None:
    """Load FlipList.txt from the MRC file's directory, if present."""
    try:
        return nornir_buildmanager.importers.GetFlipList(str(mrc_path.parent))
    except Exception:
        return None


def inspect(mrc_path: Path) -> dict:
    """Return a per-file report of whether the fixes can change the output."""
    report: dict = {'path': str(mrc_path)}
    mrc = MRCFile.Load(str(mrc_path))
    try:
        report['num_tiles'] = int(mrc.num_tiles) if mrc.num_tiles is not None else None
        raw = _raw_signed_fields(mrc, mrc_path)
    finally:
        try:
            mrc.mrc.close()
        except Exception:
            pass

    wrapped: dict[str, int] = {}
    for name, values in raw.items():
        count = sum(1 for v in values if v >= SIGN_BIT)
        if count:
            wrapped[name] = count
    report['fields_present'] = sorted(raw.keys())
    report['wrapped_counts'] = wrapped
    report['signed_parse_changes_output'] = bool(wrapped)

    stage = raw.get('stage_coords', [])
    # stage_coords arrive interleaved as x, y per tile.
    signed_stage = [v - 0x10000 if v >= SIGN_BIT else v for v in stage]
    xs = signed_stage[0::2]
    ys = signed_stage[1::2]
    straddles = bool(xs and min(xs) < 0 <= max(xs)) or bool(ys and min(ys) < 0 <= max(ys))
    report['stage_straddles_origin'] = straddles
    report['stage_x_range'] = [min(xs), max(xs)] if xs else None
    report['stage_y_range'] = [min(ys), max(ys)] if ys else None

    section = shared.GetSectionInfo(str(mrc_path))
    section_number = getattr(section, 'number', None)
    flip_list = _flip_list_for(mrc_path)
    report['section_number'] = section_number
    report['flip_list'] = flip_list
    if section_number is None:
        report['flip_changes_output'] = None
    else:
        report['flip_ud'] = flip_ud_for_section(int(section_number), flip_list)
        report['flip_changes_output'] = report['flip_ud'] != DEFAULT_FLIP_UD

    # A stage wrap that every tile shares is removed by TranslateToZeroOrigin,
    # so it cannot move a tile relative to its neighbours. Only a straddling
    # section changes its layout. Verified against SmallMouse/0001 and 0002,
    # where signed and unsigned agree to 0.000 px, and RABBIT_SALVAGE/0226,
    # where they differ by 1.16e6 px.
    report['layout_changes'] = straddles
    metadata_fields = sorted(name for name in wrapped if name != 'stage_coords')
    report['metadata_changes'] = metadata_fields
    report['any_change'] = bool(straddles or metadata_fields
                                or report.get('flip_changes_output'))
    return report


def iter_mrc_paths(targets: list[str]) -> list[Path]:
    """Expand files and directories into a sorted list of .mrc paths."""
    found: list[Path] = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            found.extend(sorted(path.rglob('*.mrc')))
        elif path.is_file():
            found.append(path)
        else:
            print(f"skipping missing path: {target}", file=sys.stderr)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('targets', nargs='+', help='.mrc files or directories to scan')
    parser.add_argument('--json', help='write the full report to this path')
    args = parser.parse_args()

    paths = iter_mrc_paths(args.targets)
    if not paths:
        print('no .mrc files found', file=sys.stderr)
        return 1

    reports = []
    changed = []
    for path in paths:
        try:
            report = inspect(path)
        except Exception as exc:  # a malformed file should not abort the scan
            report = {'path': str(path), 'error': f'{type(exc).__name__}: {exc}'}
            print(f"  ERROR {path}: {exc}", file=sys.stderr)
        reports.append(report)
        if report.get('any_change'):
            changed.append(report)

        flag = 'CHANGES ' if report.get('any_change') else 'identical'
        print(f"[{flag}] {path.name}  tiles={report.get('num_tiles')}  "
              f"wrapped={report.get('wrapped_counts') or '{}'}  "
              f"straddle={report.get('stage_straddles_origin')}  "
              f"flip_changes={report.get('flip_changes_output')}")

    print(f"\n{len(paths)} file(s) scanned; {len(changed)} can change on reimport")
    if changed:
        print("Reimport and diff these before trusting the new output:")
        for report in changed:
            reasons = []
            if report.get('layout_changes'):
                reasons.append(f"stage straddles origin, relative layout moves "
                               f"(wrapped {report['wrapped_counts']})")
            if report.get('metadata_changes'):
                reasons.append(f"signed metadata fields {report['metadata_changes']}")
            if report.get('flip_changes_output'):
                reasons.append('FlipList inverts the baseline flip')
            print(f"  {report['path']}: {'; '.join(reasons)}")

    if args.json:
        Path(args.json).write_text(json.dumps(reports, indent=2), encoding='utf-8')
        print(f"wrote {args.json}")

    return 2 if changed else 0


if __name__ == '__main__':
    raise SystemExit(main())
