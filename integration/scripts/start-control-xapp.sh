#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INTEGRATION_BUILD="${HERO_INTEGRATION_BUILD_DIR:-$ROOT/integration/build}"
PLUGIN_DIR="${HERO_FLEXRIC_PLUGIN_DIR:-$INTEGRATION_BUILD/phase06-plugins}"
XAPP="$INTEGRATION_BUILD/xapp-hero-control"
[[ -x "$XAPP" ]] || { echo "Build integration first; missing $XAPP" >&2; exit 2; }
export HERO_TARGET_GNB="${HERO_TARGET_GNB:-102}"
export HERO_MAX_DL_MCS="${HERO_MAX_DL_MCS:-1}"
exec stdbuf -oL -eL "$XAPP" -p "$PLUGIN_DIR/" "$@"
