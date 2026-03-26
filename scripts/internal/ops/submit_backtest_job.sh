#!/usr/bin/env bash
# ==============================================
# Backtest Job 제출
# ==============================================
# 사용법:
#   ./scripts/internal/ops/submit_backtest_job.sh --strategy imbalance_momentum --version 1.0 \
#       --symbol 005930 --start-date 2026-03-13
#
#   ./scripts/internal/ops/submit_backtest_job.sh --strategy imbalance_momentum --version 1.0 \
#       --universe --start-date 2026-03-13
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

cd "${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src"

exec python scripts/internal/ops/submit_backtest_job.py "$@"
