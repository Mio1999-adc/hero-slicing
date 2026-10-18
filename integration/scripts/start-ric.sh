#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FLEXRIC_BUILD="${FLEXRIC_BUILD_DIR:-$ROOT/platforms/flexric/build}"
INTEGRATION_BUILD="${HERO_INTEGRATION_BUILD_DIR:-$ROOT/integration/build}"
CONFIG="${FLEXRIC_CONFIG:-$ROOT/integration/config/flexric-local.conf}"
PLUGIN_DIR="${HERO_FLEXRIC_PLUGIN_DIR:-$INTEGRATION_BUILD/phase06-plugins}"
RIC_IP="${HERO_RIC_BIND_IP:-127.0.0.1}"
RIC="$FLEXRIC_BUILD/examples/ric/nearRT-RIC"
[[ -x "$RIC" ]] || { echo "Missing original FlexRIC RIC: $RIC" >&2; exit 2; }
[[ -f "$CONFIG" ]] || { echo "Missing FlexRIC config: $CONFIG" >&2; exit 2; }
[[ -f "$PLUGIN_DIR/libkpm_sm.so" && -f "$PLUGIN_DIR/librc_sm.so" ]] || {
  echo "Build integration first; expected KPM/RC plugins under $PLUGIN_DIR" >&2; exit 2;
}
exec stdbuf -oL -eL "$RIC" -c "$CONFIG" -p "$PLUGIN_DIR/" -a "$RIC_IP" "$@"
