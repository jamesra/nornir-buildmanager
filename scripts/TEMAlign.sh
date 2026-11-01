#!/usr/bin/env bash
# TEMAlign.sh
#
# Linux equivalent of the VS Code launch config "TEM Full Alignment".
# Assumes the volume already has a mosaic build (Leveled / Grid).
#
# Prompts for a volume folder (or accept $1 / VOLUME_DIR).
#
# Usage:
#   ./TEMAlign.sh
#   ./TEMAlign.sh /storage4/MyVolume
#   VOLUME_DIR=/storage4/MyVolume ./TEMAlign.sh
#   ./TEMAlign.sh /storage4/MyVolume -start-at 2 -stop-after 9
#
# Arguments after the volume folder are passed to nornir-build as global options,
# e.g. -start-at / -stop-after to run part of the chain (step number, Name#k, or a
# Name that occurs once).
#
# Environment (optional overrides; defaults match the launch config):
#   NORNIR_HEADLESS, PYTHONUNBUFFERED, NORNIR_MQTT_* ,
#   NORNIR_COMPUTATIONAL_LIBRARY (default: cupy when a GPU is usable, else numpy),
#   NORNIR_BUILD_PYTHON (python executable; default: python3 then python)

set -euo pipefail

# shellcheck source=_nornir_tem_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_nornir_tem_common.sh"

# First argument is the volume folder unless it is an option; the rest are global
# nornir-build options (e.g. -start-at 2 -stop-after 9).
VOLUME_ARG=""
if [[ $# -gt 0 && "$1" != -* ]]; then
  VOLUME_ARG="$1"
  shift
fi
VOLUME_DIR="$(resolve_volume_dir "$VOLUME_ARG")"
PYTHON="$(resolve_python)"
COMP_LIB="${NORNIR_COMPUTATIONAL_LIBRARY:-$(default_computational_library "$PYTHON")}"
export_nornir_tem_env

echo "Volume folder : $VOLUME_DIR"
echo "Python        : $PYTHON"
echo "Compute lib   : $COMP_LIB"
echo "Options       : ${*:-(none)}"
echo

# Same sequential chain as launch.json "TEM Full Alignment".
# Volume path is first positional (legacy launch order); build reorders to subcommand-first.
# Use -m nornir_buildmanager (not .build) to avoid runpy RuntimeWarning from package __init__ importing build.
exec "$PYTHON" -Xfrozen_modules=off -m nornir_buildmanager \
  -debug -computational_library "$COMP_LIB" "$@" \
  "$VOLUME_DIR" \
  CreateBlobFilter -Channels TEM -InputFilter Leveled -Levels 16,32,64 -OutputFilter Blob -Radius 9 -Median 7 -Max 3 \
  --then AlignSections -NumAdjacentSections 1 -Filters Blob -UseMasks -Downsample 64 -Channels TEM \
  --then AssembleStosOverlays -StosGroup StosBrute -Downsample 64 -StosMap PotentialRegistrationChain \
  --then SelectBestRegistrationChain -StosGroup StosBrute -Downsample 64 -InputStosMap PotentialRegistrationChain -OutputStosMap FinalStosMap \
  --then RefineSectionAlignment -InputGroup StosBrute -InputDownsample 64 -OutputGroup Grid -OutputDownsample 32 -Filter Leveled \
  --then AssembleStosOverlays -StosGroup Grid -Downsample 32 -StosMap FinalStosMap \
  --then CreateVikingXML -StosGroup Grid32 -StosMap FinalStosMap -OutputFile Grid32 \
  --then RefineSectionAlignment -InputGroup Grid -InputDownsample 32 -OutputGroup Grid -OutputDownsample 16 -Filter Leveled \
  --then SliceToVolume -Downsample 16 -InputGroup Grid -OutputGroup SliceToVolume -NoLinearBlend \
  --then ScaleVolumeTransforms -InputGroup SliceToVolume -InputDownsample 16 -OutputDownsample 1 \
  --then LinearizeVolume -InputGroup SliceToVolume -InputDownsample 1 -OutputGroup SliceToVolumeLinear -min_blend 0.005 -max_blend 0.05 -travel_limit 512 -reblend_iterations 8 -reblend_tolerance 0.5 \
  --then CreateVikingXML -OutputFile SliceToVolume -StosGroup SliceToVolume1 -StosGroup SliceToVolumeLinear1 -StosMap SliceToVolume \
  --then MosaicToVolume -InputTransform Grid -OutputTransform ChannelToVolume -Channels '(?!Registered)' \
  --then Assemble -Channels '(?!Registered)' -Filters Leveled -Downsample 32 -NoInterlace -Transform ChannelToVolume -ChannelPrefix Registered_ \
  --then MosaicReport -PruneFilter Raw8 -ContrastFilter Raw8 -AssembleFilter Leveled -AssembleDownsample 32 -Output VolumeReport \
  --then ExportImages -Channels Registered -Filters Leveled -Downsample 32 -Output "$VOLUME_DIR/Registered"
