#!/usr/bin/env bash
# Shared helpers for TEM*.sh pipeline wrappers. Sourced by entry scripts; not for direct invocation.

prompt_path() {
  local label="$1"
  local default="${2:-}"
  local value=""

  if [[ -n "$default" ]]; then
    read -r -p "${label} [${default}]: " value
    value="${value:-$default}"
  else
    read -r -p "${label}: " value
  fi

  # Expand leading ~ and trim surrounding whitespace
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  if [[ "$value" == ~* ]]; then
    value="${value/#\~/$HOME}"
  fi
  printf '%s\n' "$value"
}

resolve_python() {
  if [[ -n "${NORNIR_BUILD_PYTHON:-}" ]]; then
    printf '%s\n' "$NORNIR_BUILD_PYTHON"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi
  echo "error: no python3/python on PATH; set NORNIR_BUILD_PYTHON" >&2
  exit 1
}

export_nornir_tem_env() {
  export NORNIR_HEADLESS="${NORNIR_HEADLESS:-1}"
  export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
  export NORNIR_MQTT_HOST="${NORNIR_MQTT_HOST:-host.docker.internal}"
  export NORNIR_MQTT_PORT="${NORNIR_MQTT_PORT:-1883}"
  export NORNIR_MQTT_ENABLE="${NORNIR_MQTT_ENABLE:-1}"
}

require_existing_dir() {
  local label="$1"
  local path="$2"
  if [[ -z "$path" ]]; then
    echo "error: ${label} is required" >&2
    exit 1
  fi
  if [[ ! -d "$path" ]]; then
    echo "error: ${label} does not exist: $path" >&2
    exit 1
  fi
}

resolve_volume_dir() {
  local volume_dir="${1:-${VOLUME_DIR:-}}"
  if [[ -z "$volume_dir" ]]; then
    volume_dir="$(prompt_path "Volume folder")"
  fi
  require_existing_dir "volume folder" "$volume_dir"
  printf '%s\n' "$volume_dir"
}
