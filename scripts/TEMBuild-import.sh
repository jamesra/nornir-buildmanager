#!/usr/bin/env bash
# TEMBuild-import.sh
#
# Linux equivalent of the VS Code launch config
# "TEM Build: full sequence (sequential)", with an ImportIDoc step first.
#
# Prompts for:
#   1) Import folder containing SerialEM .idoc trees
#   2) Output folder for the new volume (VolumeData.xml root)
#
# Then runs ImportIDoc into the output folder, followed by the same sequential
# Prune → Histogram → AdjustContrast → Mosaic → Assemble → MosaicReport →
# CreateVikingXML chain as the launch config.
#
# Usage:
#   ./TEMBuild-import.sh
#   ./TEMBuild-import.sh /data/idoc /storage4/MyVolume
#   IMPORT_DIR=/data/idoc VOLUME_DIR=/storage4/MyVolume ./TEMBuild-import.sh
#
# Environment (optional overrides; defaults match the launch config):
#   NORNIR_HEADLESS, PYTHONUNBUFFERED, NORNIR_MQTT_* ,
#   NORNIR_COMPUTATIONAL_LIBRARY (default: cupy),
#   NORNIR_BUILD_PYTHON (python executable; default: python3 then python)

set -euo pipefail

# shellcheck source=_nornir_tem_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_nornir_tem_common.sh"

IMPORT_DIR="${1:-${IMPORT_DIR:-}}"
VOLUME_DIR="${2:-${VOLUME_DIR:-}}"

if [[ -z "$IMPORT_DIR" ]]; then
  IMPORT_DIR="$(prompt_path "Import folder (SerialEM .idoc root)")"
fi
if [[ -z "$VOLUME_DIR" ]]; then
  VOLUME_DIR="$(prompt_path "Output volume folder")"
fi

if [[ -z "$IMPORT_DIR" || -z "$VOLUME_DIR" ]]; then
  echo "error: import folder and output volume folder are required" >&2
  exit 1
fi

require_existing_dir "import folder" "$IMPORT_DIR"
mkdir -p "$VOLUME_DIR"

PYTHON="$(resolve_python)"
COMP_LIB="${NORNIR_COMPUTATIONAL_LIBRARY:-cupy}"
export_nornir_tem_env

echo "Import folder : $IMPORT_DIR"
echo "Volume folder : $VOLUME_DIR"
echo "Python        : $PYTHON"
echo "Compute lib   : $COMP_LIB"
echo

# Same sequential chain as launch.json "TEM Build: full sequence (sequential)",
# with ImportIDoc prepended. Volume path is first positional (legacy launch order);
# nornir_buildmanager.build reorders to subcommand-first.
exec "$PYTHON" -Xfrozen_modules=off -m nornir_buildmanager.build \
  -debug -computational_library "$COMP_LIB" \
  "$VOLUME_DIR" \
  ImportIDoc "ImportDir=${IMPORT_DIR}" \
  --then Prune -InputFilter Raw8 -Downsample 4 -Channels TEM -DefaultThreshold 10.0 \
  --then Histogram -Filters Raw8 -InputTransform Prune -Downsample 4 -Channels TEM \
  --then AdjustContrast -InputFilter Raw8 -OutputFilter Leveled -InputTransform Prune -Channels TEM \
  --then Mosaic -InputFilter Leveled -RegistrationDownsample 4 -InputTransform Prune -OutputTransform Grid -Channels TEM \
  --then Assemble -Channels TEM -Filters Leveled -Downsample 8,16,32 -NoInterlace -Transform Grid \
  --then MosaicReport -PruneFilter Raw8 -ContrastFilter Raw8 -AssembleFilter Leveled -AssembleDownsample 16 -Output MosaicReport \
  --then CreateVikingXML -OutputFile Mosaic
