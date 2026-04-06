#!/usr/bin/env bash
# =============================================================
# CoSTEER + Optuna hybrid strategy generation loop - smoke test
#
# Runs the full pipeline in mock mode without calling the OpenAI API.
# This still uses real KRX data, so data_dir must be configured correctly
# in conf/paths.yaml.
#
# Usage:
#   bash scripts/run_code_loop_smoke.sh
# =============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

IS_START="${IS_START:-20260313}"
IS_END="${IS_END:-20260313}"
SYMBOLS="${SYMBOLS:-000660}"
N_ITER="${N_ITER:-3}"
OPTIMIZE_N_TRIALS="${OPTIMIZE_N_TRIALS:-5}"
MEMORY_DIR="${MEMORY_DIR:-outputs/memory_code_smoke}"
OUTPUT_DIR="${OUTPUT_DIR:-outputs/backtests_code_smoke}"

echo "============================================================"
echo "  CoSTEER Code Loop (smoke / mock LLM)"
echo "  symbols : ${SYMBOLS}"
echo "  IS      : ${IS_START} ~ ${IS_END}"
echo "  n_iter  : ${N_ITER}  optuna_trials: ${OPTIMIZE_N_TRIALS}"
echo "============================================================"

PYTHONPATH=src python scripts/run_strategy_loop.py \
    --research-goal "order imbalance momentum with spread filter" \
    --symbols "${SYMBOLS}" \
    --is-start "${IS_START}" \
    --is-end "${IS_END}" \
    --mode mock \
    --n-iter "${N_ITER}" \
    --optimize-n-trials "${OPTIMIZE_N_TRIALS}" \
    --memory-dir "${MEMORY_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    --profile code_loop
