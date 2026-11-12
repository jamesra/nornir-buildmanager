#!/usr/bin/env bash
# TEMBuildQuantized.sh
#
# The mosaic build for crushed sections: noise-step codes in the Quantized filter (the ImportIDoc / ImportMRC
# default, i.e. without -NoCrush). Sections without Quantized (legacy sections, in Raw8) are skipped;
# TEMBuildLegacy.sh builds those. TEMBuild.sh runs this script.
# Assumes the volume is already imported (VolumeData.xml present), without -CameraBpp.
#
# The mosaic is built and assembled on Quantized. AdjustAssembledContrast then writes the Leveled filter from the
# assembled Quantized images (8-bit, mapped linearly over the display range CrushDisplayMin/Max the import recorded),
# so TEMAlign reads Leveled images for crushed and legacy sections alike. Leveled has no tiles in crushed sections.
#
# Differences from TEMBuildLegacy.sh:
#   Prune/Mosaic     read Quantized instead of Raw8 / Leveled (NCC is insensitive to the code scale)
#   Histogram, AdjustContrast   not run; the import stores the code histogram and display range
#   Assemble         -Filters Quantized
#   AdjustAssembledContrast     Quantized assembled images -> Leveled assembled images, same -Downsample levels
#   Prune            -DefaultThreshold 2000 instead of 10.0 (James, 2026-11-12). Crushed scores are rescaled to
#                    the contrast-stretched 8-bit scale with the filter's CrushCodeSpan (PruneObj.CrushedScoreScale),
#                    so they match legacy scores within ~1% (KTEF_Candidates). 10.0 pruned nothing there: the
#                    lowest tile scored 1151.
#
# Prompts for a volume folder (or accept $1 / VOLUME_DIR).
#
# Usage:
#   ./TEMBuildQuantized.sh
#   ./TEMBuildQuantized.sh /storage4/MyVolume
#   VOLUME_DIR=/storage4/MyVolume ./TEMBuildQuantized.sh
#   ./TEMBuildQuantized.sh /storage4/MyVolume -start-at Mosaic
#
# Arguments after the volume folder are passed to nornir-build as global options,
# e.g. -start-at / -stop-after to run part of the chain (step number, Name#k, or a
# Name that occurs once).
#
# Environment (optional overrides; defaults match TEMBuildLegacy.sh):
#   NORNIR_HEADLESS, PYTHONUNBUFFERED, NORNIR_MQTT_* ,
#   NORNIR_COMPUTATIONAL_LIBRARY (default: cupy when a GPU is usable, else numpy),
#   NORNIR_BUILD_PYTHON (python executable; default: python3 then python)

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

echo "Volume folder : $VOLUME_DIR"
echo "Python        : $PYTHON"
echo "Compute lib   : $COMP_LIB"
echo "Options       : ${*:-(none)}"
echo

# Use -m nornir_buildmanager (not .build) to avoid runpy RuntimeWarning from package __init__ importing build.
exec "$PYTHON" -Xfrozen_modules=off -m nornir_buildmanager \
  -debug -computational_library "$COMP_LIB" "$@" \
  "$VOLUME_DIR" \
  Prune -InputFilter Quantized -Downsample 4 -Channels TEM -DefaultThreshold 2000 \
  --then Mosaic -InputFilter Quantized -RegistrationDownsample 4 -InputTransform Prune -OutputTransform Grid -Channels TEM \
  --then Assemble -Channels TEM -Filters Quantized -Downsample 8,16,32 -NoInterlace -Transform Grid \
  --then AdjustAssembledContrast -InputFilter Quantized -OutputFilter Leveled -Downsample 8,16,32 -Channels TEM \
  --then MosaicReport -PruneFilter Quantized -ContrastFilter Quantized -AssembleFilter Leveled -AssembleDownsample 16 -Output MosaicReport \
  --then CreateVikingXML -OutputFile Mosaic
