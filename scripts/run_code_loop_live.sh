#!/usr/bin/env bash
# =============================================================
# CoSTEER + Optuna hybrid strategy generation loop - live mode
#
# Prerequisite:
#   export OPENAI_API_KEY=sk-...
#
# Examples:
#   bash scripts/run_code_loop_live.sh
#
#   # Override date / symbols:
#   IS_START=20260314 IS_END=20260314 SYMBOLS=005930 bash scripts/run_code_loop_live.sh
#
#   # Separate IS and OOS:
#   IS_START=20260313 IS_END=20260317 OOS_START=20260320 OOS_END=20260321 \
#     bash scripts/run_code_loop_live.sh
#
#   # Override research goal:
#   GOAL="spread mean reversion" bash scripts/run_code_loop_live.sh
# =============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

: "${OPENAI_API_KEY:?OPENAI_API_KEY is not set. Run: export OPENAI_API_KEY=sk-...}"

PROFILE="${PROFILE:-code_loop}"

# Profile defaults (conf/profiles/<PROFILE>.yaml)
mapfile -t _PROFILE_DEFAULTS < <(
    PROFILE_NAME="${PROFILE}" PYTHONPATH=src python - <<'PY'
import os
from utils.config import load_config

profile = os.environ.get("PROFILE_NAME", "code_loop")
cfg = load_config(profile=profile)
code_loop = cfg.get("code_loop", {})
opt = cfg.get("optimization", {})

print(str(code_loop.get("research_goal", "")))
print(str(code_loop.get("symbols", "")))
print(str(code_loop.get("model", "")))
print(str(code_loop.get("n_iter", "")))
print(str(opt.get("n_trials", "")))
PY
)

PROFILE_GOAL="${_PROFILE_DEFAULTS[0]:-}"
PROFILE_SYMBOLS="${_PROFILE_DEFAULTS[1]:-}"
PROFILE_MODEL="${_PROFILE_DEFAULTS[2]:-}"
PROFILE_N_ITER="${_PROFILE_DEFAULTS[3]:-}"
PROFILE_OPT_TRIALS="${_PROFILE_DEFAULTS[4]:-}"

# Parameters (can be overridden by environment variables)
GOAL="${GOAL:-${PROFILE_GOAL:-order imbalance momentum with spread filter}}"
SYMBOLS="${SYMBOLS:-${PROFILE_SYMBOLS:-000660}}"
IS_START="${IS_START:-20260313}"
IS_END="${IS_END:-20260313}"
OOS_START="${OOS_START:-}"
OOS_END="${OOS_END:-}"
MODEL="${MODEL:-${PROFILE_MODEL:-gpt-4o}}"
N_ITER="${N_ITER:-${PROFILE_N_ITER:-20}}"
OPTIMIZE_N_TRIALS="${OPTIMIZE_N_TRIALS:-${PROFILE_OPT_TRIALS:-30}}"
MEMORY_DIR="${MEMORY_DIR:-outputs/memory_code}"
OUTPUT_DIR="${OUTPUT_DIR:-outputs/backtests_code}"

# Build OOS arguments
OOS_ARGS=()
if [[ -n "${OOS_START}" && -n "${OOS_END}" ]]; then
    OOS_ARGS=(--oos-start "${OOS_START}" --oos-end "${OOS_END}")
fi

echo "============================================================"
echo "  CoSTEER Code Loop (live)"
echo "  profile : ${PROFILE}"
echo "  goal    : ${GOAL}"
echo "  symbols : ${SYMBOLS}"
echo "  IS      : ${IS_START} ~ ${IS_END}"
if [[ ${#OOS_ARGS[@]} -gt 0 ]]; then
    echo "  OOS     : ${OOS_START} ~ ${OOS_END}"
else
    echo "  OOS     : (none)"
fi
echo "  model   : ${MODEL}"
echo "  strategy: code"
echo "  n_iter  : ${N_ITER}  optuna_trials: ${OPTIMIZE_N_TRIALS}"
echo "============================================================"

PYTHONPATH=src python scripts/run_strategy_loop.py \
    --research-goal "${GOAL}" \
    --symbols "${SYMBOLS}" \
    --is-start "${IS_START}" \
    --is-end "${IS_END}" \
    "${OOS_ARGS[@]}" \
    --mode live \
    --model "${MODEL}" \
    --n-iter "${N_ITER}" \
    --optimize-n-trials "${OPTIMIZE_N_TRIALS}" \
    --memory-dir "${MEMORY_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    --profile "${PROFILE}"
