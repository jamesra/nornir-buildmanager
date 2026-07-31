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
#
# Environment (optional overrides; defaults match the launch config):
#   NORNIR_HEADLESS, PYTHONUNBUFFERED, NORNIR_MQTT_* ,
#   NORNIR_COMPUTATIONAL_LIBRARY (default: cupy),
#   NORNIR_BUILD_PYTHON (python executable; default: python3 then python)

set -euo pipefail

# shellcheck source=_nornir_tem_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_nornir_tem_common.sh"

VOLUME_DIR="$(resolve_volume_dir "${1:-}")"
PYTHON="$(resolve_python)"
COMP_LIB="${NORNIR_COMPUTATIONAL_LIBRARY:-cupy}"
export_nornir_tem_env

echo "Volume folder : $VOLUME_DIR"
echo "Python        : $PYTHON"
echo "Compute lib   : $COMP_LIB"
echo

# Same sequential chain as launch.json "TEM Full Alignment".
# Volume path is first positional (legacy launch order);
# nornir_buildmanager.build reorders to subcommand-first.
exec "$PYTHON" -Xfrozen_modules=off -m nornir_buildmanager.build \
  -debug -computational_library "$COMP_LIB" \
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
