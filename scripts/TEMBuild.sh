#!/usr/bin/env bash
# TEMBuild.sh
#
# Linux equivalent of the VS Code launch config
# "TEM Build: full sequence (sequential)".
# Assumes the volume is already imported (VolumeData.xml present).
#
# Prompts for a volume folder (or accept $1 / VOLUME_DIR).
#
# Usage:
#   ./TEMBuild.sh
#   ./TEMBuild.sh /storage4/MyVolume
#   VOLUME_DIR=/storage4/MyVolume ./TEMBuild.sh
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

# Same sequential chain as launch.json "TEM Build: full sequence (sequential)".
# Volume path is first positional (legacy launch order); build reorders to subcommand-first.
# Use -m nornir_buildmanager (not .build) to avoid runpy RuntimeWarning from package __init__ importing build.
exec "$PYTHON" -Xfrozen_modules=off -m nornir_buildmanager \
  -debug -computational_library "$COMP_LIB" \
  "$VOLUME_DIR" \
  Prune -InputFilter Raw8 -Downsample 4 -Channels TEM -DefaultThreshold 10.0 \
  --then Histogram -Filters Raw8 -InputTransform Prune -Downsample 4 -Channels TEM \
  --then AdjustContrast -InputFilter Raw8 -OutputFilter Leveled -InputTransform Prune -Channels TEM \
  --then Mosaic -InputFilter Leveled -RegistrationDownsample 4 -InputTransform Prune -OutputTransform Grid -Channels TEM \
  --then Assemble -Channels TEM -Filters Leveled -Downsample 8,16,32 -NoInterlace -Transform Grid \
  --then MosaicReport -PruneFilter Raw8 -ContrastFilter Raw8 -AssembleFilter Leveled -AssembleDownsample 16 -Output MosaicReport \
  --then CreateVikingXML -OutputFile Mosaic
