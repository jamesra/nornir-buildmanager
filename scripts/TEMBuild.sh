#!/usr/bin/env bash
# TEMBuild.sh
#
# Builds the mosaics of every section in a volume: runs TEMBuildLegacy.sh (sections with Raw8, contrast-stretched
# tiles) and then TEMBuildCrushed.sh (sections with Quantized, crushed codes). Each chain skips the sections it
# does not apply to, so a volume may mix both kinds. Both end with a Leveled filter that TEMAlign reads.
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
# Arguments after the volume folder are passed to both scripts as nornir-build global options. -start-at and
# -stop-after apply to each chain separately, so use a stage name both chains have (Prune, Mosaic, Assemble,
# MosaicReport, CreateVikingXML) or run TEMBuildLegacy.sh / TEMBuildCrushed.sh directly.
#
# Environment: as TEMBuildLegacy.sh and TEMBuildCrushed.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_nornir_tem_common.sh
source "$SCRIPT_DIR/_nornir_tem_common.sh"

VOLUME_ARG=""
if [[ $# -gt 0 && "$1" != -* ]]; then
  VOLUME_ARG="$1"
  shift
fi
VOLUME_DIR="$(resolve_volume_dir "$VOLUME_ARG")"

bash "$(tem_script "$SCRIPT_DIR" TEMBuildLegacy)" "$VOLUME_DIR" "$@"
exec bash "$(tem_script "$SCRIPT_DIR" TEMBuildCrushed)" "$VOLUME_DIR" "$@"
