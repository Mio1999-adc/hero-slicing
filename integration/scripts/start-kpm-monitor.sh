#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FLEXRIC_BUILD="${FLEXRIC_BUILD_DIR:-$ROOT/platforms/flexric/build}"
INTEGRATION_BUILD="${HERO_INTEGRATION_BUILD_DIR:-$ROOT/integration/build}"
PLUGIN_DIR="${HERO_FLEXRIC_PLUGIN_DIR:-$INTEGRATION_BUILD/phase06-plugins}"
XAPP="$FLEXRIC_BUILD/examples/xApp/c/monitor/xapp_kpm_moni"
[[ -x "$XAPP" ]] || { echo "Missing original FlexRIC KPM monitor xApp: $XAPP" >&2; exit 2; }
export XAPP_DURATION="${XAPP_DURATION:-60}"
exec stdbuf -oL -eL "$XAPP" -p "$PLUGIN_DIR/" "$@"
