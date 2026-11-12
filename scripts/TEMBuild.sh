#!/usr/bin/env bash
# TEMBuild.sh
#
# Builds the mosaics of a volume's crushed sections (Quantized): runs TEMBuildQuantized.sh. Sections imported with
# -NoCrush (Raw8) are skipped; build those with TEMBuildLegacy.sh. Do not run both on a section that holds both
# filters (imported twice): each writes Leveled.
# Assumes the volume is already imported (VolumeData.xml present).
#
# Prompts for a volume folder (or accept $1 / VOLUME_DIR).
#
# Usage:
#   ./TEMBuild.sh
#   ./TEMBuild.sh /storage4/MyVolume
#   VOLUME_DIR=/storage4/MyVolume ./TEMBuild.sh
#   ./TEMBuild.sh /storage4/MyVolume -start-at Mosaic
#
# Arguments after the volume folder are passed to TEMBuildQuantized.sh as nornir-build global options.
#
# Environment: as TEMBuildQuantized.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_nornir_tem_common.sh
source "$SCRIPT_DIR/_nornir_tem_common.sh"

exec bash "$(tem_script "$SCRIPT_DIR" TEMBuildQuantized)" "$@"
