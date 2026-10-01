#!/usr/bin/env bash
# Build + test the C++ stack natively on the BeagleY-AI.
#   bash ~/krc-robot/scripts/build-krbot.sh            # RelWithDebInfo, run tests
#   BUILD_TYPE=Debug bash ~/krc-robot/scripts/build-krbot.sh
#   NO_TESTS=1 bash ~/krc-robot/scripts/build-krbot.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)/krbot"
BUILD="${ROOT}/build"
GEN=""
command -v ninja &>/dev/null && GEN="-G Ninja"

echo "==> Configure (${BUILD_TYPE:-RelWithDebInfo})"
cmake -S "$ROOT" -B "$BUILD" $GEN -DCMAKE_BUILD_TYPE="${BUILD_TYPE:-RelWithDebInfo}" >/dev/null
echo "==> Build ($(nproc) jobs)"
cmake --build "$BUILD" -j "$(nproc)"
if [ -z "${NO_TESTS:-}" ] && [ -x "$BUILD/krbot_tests" ]; then
  echo "==> Test"
  ctest --test-dir "$BUILD" --output-on-failure -j "$(nproc)"
fi
echo "==> Binary: $BUILD/krbot"
echo "    dry run:  $BUILD/krbot --dry-run"
echo "    real:     $BUILD/krbot          (stop the KR-Robot Control app first — the motor port is exclusive)"
