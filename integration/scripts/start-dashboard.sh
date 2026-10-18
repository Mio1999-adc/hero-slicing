#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -z "${HERO_INTEGRATION_BUILD_DIR:-}" && -x "$ROOT/integration/build-optimized/native-measurements" ]]; then
  export HERO_INTEGRATION_BUILD_DIR="$ROOT/integration/build-optimized"
fi
if [[ ! -x "${HERO_INTEGRATION_BUILD_DIR:-$ROOT/integration/build}/native-measurements" ]]; then
  "$ROOT/integration/scripts/build-integration.sh"
fi
exec python3 "$ROOT/integration/dashboard/server.py"
