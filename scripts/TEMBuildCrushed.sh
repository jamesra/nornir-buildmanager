#!/usr/bin/env bash
# TEMBuildCrushed.sh
#
# TEMBuild.sh for volumes imported with noise crushing (the ImportIDoc / ImportMRC default, i.e. without
# -NoCrush). Crushed Raw8 tiles hold section-wide noise-step codes; no contrast mapping is ever applied to
# them, so this chain has no Histogram, AdjustContrast or Leveled stages: it prunes, mosaics and assembles
# straight from the crushed Raw8 filter (James, 2026-11-05).
# Assumes the volume is already imported (VolumeData.xml present), without -CameraBpp.
#
# Differences from TEMBuild.sh:
#   Histogram       dropped  (only fed AdjustContrast; refuses crushed filters anyway)
#   AdjustContrast  dropped  (would build Leveled by contrast-mapping crushed codes; refuses crushed filters)
#   Mosaic          -InputFilter Raw8 instead of Leveled (NCC is insensitive to the code scale)
#   Assemble        -Filters Raw8 instead of Leveled (crushed assemblies keep the codes)
#   MosaicReport    -AssembleFilter Raw8; -ContrastFilter Raw8 reports no contrast (none is recorded)
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
#   NORNIR_CRUSHED_FILTER (default Raw8; Raw16 for sections whose codes need more than 8 bits, CrushBits > 8)

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
FILTER="${NORNIR_CRUSHED_FILTER:-Raw8}"

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
  --then Mosaic -InputFilter "$FILTER" -RegistrationDownsample 4 -InputTransform Prune -OutputTransform Grid -Channels TEM \
  --then Assemble -Channels TEM -Filters "$FILTER" -Downsample 8,16,32 -NoInterlace -Transform Grid \
  --then MosaicReport -PruneFilter "$FILTER" -ContrastFilter "$FILTER" -AssembleFilter "$FILTER" -AssembleDownsample 16 -Output MosaicReport \
  --then CreateVikingXML -OutputFile Mosaic
