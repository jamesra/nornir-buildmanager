#!/usr/bin/env bash
# TEMBuild-import.sh
#
# Imports SerialEM .idoc trees with TEMImport.sh, then builds every section's mosaic with TEMBuild.sh
# (TEMBuildLegacy.sh for Raw8 sections, TEMBuildCrushed.sh for Quantized sections).
#
# Prompts for:
#   1) Import folder containing SerialEM .idoc trees
#   2) Output folder for the new volume (VolumeData.xml root)
#
# Usage:
#   ./TEMBuild-import.sh
#   ./TEMBuild-import.sh /data/idoc /storage4/MyVolume
#   IMPORT_DIR=/data/idoc VOLUME_DIR=/storage4/MyVolume ./TEMBuild-import.sh
#   ./TEMBuild-import.sh /data/idoc /storage4/MyVolume -start-at Mosaic
#
# Arguments after the import and volume folders are passed to TEMBuild.sh (not to the import) as nornir-build
# global options, e.g. -start-at / -stop-after; see TEMBuild.sh.
#
# Environment (optional overrides):
#   NORNIR_HEADLESS, PYTHONUNBUFFERED, NORNIR_MQTT_* ,
#   NORNIR_COMPUTATIONAL_LIBRARY (default: cupy when a GPU is usable, else numpy),
#   NORNIR_BUILD_PYTHON (python executable; default: python3 then python)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_nornir_tem_common.sh
source "$SCRIPT_DIR/_nornir_tem_common.sh"

# First two arguments are the import and volume folders unless they are options;
# the rest are passed to TEMBuild.sh (e.g. -start-at Mosaic).
IMPORT_DIR="${IMPORT_DIR:-}"
VOLUME_DIR="${VOLUME_DIR:-}"
if [[ $# -gt 0 && "$1" != -* ]]; then
  IMPORT_DIR="${1:-$IMPORT_DIR}"
  shift
fi
if [[ $# -gt 0 && "$1" != -* ]]; then
  VOLUME_DIR="${1:-$VOLUME_DIR}"
  shift
fi

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

bash "$(tem_script "$SCRIPT_DIR" TEMImport)" "$IMPORT_DIR" "$VOLUME_DIR"
exec bash "$(tem_script "$SCRIPT_DIR" TEMBuild)" "$VOLUME_DIR" "$@"
