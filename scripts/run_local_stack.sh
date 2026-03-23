#!/usr/bin/env bash
# ==============================================
# 로컬 개발 스택 — Generation + Backtest Worker
# ==============================================
# 두 worker를 dev 프로필로 동시에 실행합니다.
# Ctrl+C로 모두 종료됩니다.
#
# 사용법:
#   ./scripts/run_local_stack.sh              # dev 프로필 (기본)
#   ./scripts/run_local_stack.sh prod         # prod 프로필
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src"

PROFILE="${1:-dev}"

echo "=== Local Stack (profile=${PROFILE}) ==="
echo "Starting generation worker + backtest worker..."
echo "Press Ctrl+C to stop both workers."
echo ""

# Trap to kill both background processes on exit
cleanup() {
    echo ""
    echo "Stopping workers..."
    kill "${GEN_PID}" "${BT_PID}" 2>/dev/null || true
    wait "${GEN_PID}" "${BT_PID}" 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT INT TERM

# Start generation worker in background
python scripts/run_generation_worker.py --profile "${PROFILE}" &
GEN_PID=$!
echo "[generation-worker] PID=${GEN_PID}"

# Start backtest worker in background
python scripts/run_backtest_worker.py --profile "${PROFILE}" &
BT_PID=$!
echo "[backtest-worker]   PID=${BT_PID}"

echo ""

# Wait for either to exit
wait -n "${GEN_PID}" "${BT_PID}" 2>/dev/null || true
