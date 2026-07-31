#!/usr/bin/env bash
# TEMImport.sh
#
# Import SerialEM .idoc trees into a volume (ImportIDoc only).
#
# Prompts for:
#   1) Import folder containing SerialEM .idoc trees
#   2) Output folder for the new volume (VolumeData.xml root)
#
# Usage:
#   ./TEMImport.sh
#   ./TEMImport.sh /data/idoc /storage4/MyVolume
#   IMPORT_DIR=/data/idoc VOLUME_DIR=/storage4/MyVolume ./TEMImport.sh
#
# Environment (optional overrides; defaults match launch configs):
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

exec "$PYTHON" -Xfrozen_modules=off -m nornir_buildmanager.build \
  -debug -computational_library "$COMP_LIB" \
  "$VOLUME_DIR" \
  ImportIDoc "ImportDir=${IMPORT_DIR}"
