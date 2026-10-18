#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD="${HERO_INTEGRATION_BUILD_DIR:-$ROOT/integration/build}"
JOBS="${HERO_BUILD_JOBS:-2}"
NS3_BUILD_DIR="${HERO_NS3_BUILD_DIR:-$ROOT/platforms/ns-3/build}"
NS3_BUILD_PROFILE="${HERO_NS3_BUILD_PROFILE:-debug}"
cmake -S "$ROOT/integration" -B "$BUILD" -DNS3_BUILD_DIR="$NS3_BUILD_DIR" -DNS3_BUILD_PROFILE="$NS3_BUILD_PROFILE"
cmake --build "$BUILD" -j "$JOBS"
ctest --test-dir "$BUILD" --output-on-failure
