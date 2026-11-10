#!/usr/bin/env bash
# TEMBuildCrushed.sh
#
# TEMBuild.sh for volumes imported with noise crushing (the ImportIDoc / ImportMRC default, i.e. without
# -NoCrush). Crushed tiles hold section-wide noise-step codes in the Quantized filter (legacy imports stay in
# Raw8). The mosaic is built against Quantized exactly as for any crushed section; Histogram and AdjustContrast then
# build a Leveled filter from the Quantized tiles so the standard TEMAlign scripts work on crushed and legacy
# sections alike.
# Assumes the volume is already imported (VolumeData.xml present), without -CameraBpp.
#
# Differences from TEMBuild.sh:
#   Prune/Histogram/AdjustContrast  read Quantized instead of Raw8 (AdjustContrast still writes Leveled)
#   Mosaic          -InputFilter Quantized instead of Leveled (NCC is insensitive to the code scale)
#   Assemble        -Filters Quantized and Leveled (the assembled codes, plus the Leveled images TEMAlign uses)
#   MosaicReport    -AssembleFilter Leveled as in TEMBuild.sh
# Prune keeps -DefaultThreshold 10.0: crushed scores are rescaled to the contrast-stretched 8-bit scale
# with the filter's CrushCodeSpan (PruneObj.CrushedScoreScale).
#
# Prompts for a volume folder (or accept $1 / VOLUME_DIR).
#
# Usage:
#   ./TEMBuildCrushed.sh
#   ./TEMBuildCrushed.sh /storage4/MyVolume
#   VOLUME_DIR=/storage4/MyVolume ./TEMBuildCrushed.sh
#   ./TEMBuildCrushed.sh /storage4/MyVolume -start-at Mosaic
#
# Arguments after the volume folder are passed to nornir-build as global options,
# e.g. -start-at / -stop-after to run part of the chain (step number, Name#k, or a
# Name that occurs once).
#
# Environment (optional overrides; defaults match TEMBuild.sh):
#   NORNIR_HEADLESS, PYTHONUNBUFFERED, NORNIR_MQTT_* ,
#   NORNIR_COMPUTATIONAL_LIBRARY (default: cupy when a GPU is usable, else numpy),
#   NORNIR_BUILD_PYTHON (python executable; default: python3 then python),
#   NORNIR_CRUSHED_FILTER (default Quantized, the filter ImportIDoc/ImportMRC write for crushed tiles)

set -euo pipefail

# shellcheck source=_nornir_tem_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_nornir_tem_common.sh"

VOLUME_ARG=""
if [[ $# -gt 0 && "$1" != -* ]]; then
  VOLUME_ARG="$1"
  shift
fi
VOLUME_DIR="$(resolve_volume_dir "$VOLUME_ARG")"
PYTHON="$(resolve_python)"
COMP_LIB="${NORNIR_COMPUTATIONAL_LIBRARY:-$(default_computational_library "$PYTHON")}"
export_nornir_tem_env
FILTER="${NORNIR_CRUSHED_FILTER:-Quantized}"

echo "Volume folder : $VOLUME_DIR"
echo "Python        : $PYTHON"
echo "Compute lib   : $COMP_LIB"
echo "Filter        : $FILTER"
echo "Options       : ${*:-(none)}"
echo

# Use -m nornir_buildmanager (not .build) to avoid runpy RuntimeWarning from package __init__ importing build.
exec "$PYTHON" -Xfrozen_modules=off -m nornir_buildmanager \
  -debug -computational_library "$COMP_LIB" "$@" \
  "$VOLUME_DIR" \
  Prune -InputFilter "$FILTER" -Downsample 4 -Channels TEM -DefaultThreshold 10.0 \
  --then Histogram -Filters "$FILTER" -InputTransform Prune -Downsample 4 -Channels TEM \
  --then AdjustContrast -InputFilter "$FILTER" -OutputFilter Leveled -InputTransform Prune -Channels TEM \
  --then Mosaic -InputFilter "$FILTER" -RegistrationDownsample 4 -InputTransform Prune -OutputTransform Grid -Channels TEM \
  --then Assemble -Channels TEM -Filters "($FILTER|Leveled)" -Downsample 8,16,32 -NoInterlace -Transform Grid \
  --then MosaicReport -PruneFilter "$FILTER" -ContrastFilter "$FILTER" -AssembleFilter Leveled -AssembleDownsample 16 -Output MosaicReport \
  --then CreateVikingXML -OutputFile Mosaic
