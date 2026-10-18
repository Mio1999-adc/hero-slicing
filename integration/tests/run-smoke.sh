#!/usr/bin/env bash
set -euo pipefail
integration_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cmake -S "$integration_root" -B "$integration_root/build" "$@" 2>&1 | tee "$integration_root/configure.log"
cmake --build "$integration_root/build" -j2 2>&1 | tee "$integration_root/build.log"
ctest --test-dir "$integration_root/build" --output-on-failure 2>&1 | tee "$integration_root/test.log"
