#!/usr/bin/env bash
# =============================================================
# Spec-Centric strategy generation loop - live mode
#
# Prerequisite:
#   export OPENAI_API_KEY=sk-...
#
# Examples:
#   bash scripts/run_spec_loop_live.sh
#
#   # Override date / symbols:
#   IS_START=20260314 IS_END=20260314 SYMBOLS=005930 bash scripts/run_spec_loop_live.sh
#
#   # Separate IS and OOS:
#   IS_START=20260313 IS_END=20260317 OOS_START=20260320 OOS_END=20260321 \
#     bash scripts/run_spec_loop_live.sh
#
#   # Override research goal:
#   GOAL="spread mean reversion" bash scripts/run_spec_loop_live.sh
# =============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

: "${OPENAI_API_KEY:?OPENAI_API_KEY is not set. Run: export OPENAI_API_KEY=sk-...}"

PROFILE="${PROFILE:-spec_loop}"

# Profile defaults (conf/profiles/<PROFILE>.yaml)
mapfile -t _PROFILE_DEFAULTS < <(
    PROFILE_NAME="${PROFILE}" PYTHONPATH=src python - <<'PY'
import os
from utils.config import load_config

profile = os.environ.get("PROFILE_NAME", "spec_loop")
cfg = load_config(profile=profile)
spec_loop = cfg.get("spec_loop", {})
opt = cfg.get("optimization", {})

print(str(spec_loop.get("research_goal", "")))
print(str(spec_loop.get("symbols", "")))
print(str(spec_loop.get("model", "")))
print(str(spec_loop.get("max_plan_iterations", "")))
print(str(spec_loop.get("max_code_attempts", "")))
print(str(opt.get("n_trials", "")))
print(str(spec_loop.get("is_start", "")))
print(str(spec_loop.get("is_end", "")))
print(str(spec_loop.get("oos_start", "")))
print(str(spec_loop.get("oos_end", "")))
PY
)

PROFILE_GOAL="${_PROFILE_DEFAULTS[0]:-}"
PROFILE_SYMBOLS="${_PROFILE_DEFAULTS[1]:-}"
PROFILE_MODEL="${_PROFILE_DEFAULTS[2]:-}"
PROFILE_MAX_PLAN_ITERATIONS="${_PROFILE_DEFAULTS[3]:-}"
PROFILE_MAX_CODE_ATTEMPTS="${_PROFILE_DEFAULTS[4]:-}"
PROFILE_OPT_TRIALS="${_PROFILE_DEFAULTS[5]:-}"
PROFILE_IS_START="${_PROFILE_DEFAULTS[6]:-}"
PROFILE_IS_END="${_PROFILE_DEFAULTS[7]:-}"
PROFILE_OOS_START="${_PROFILE_DEFAULTS[8]:-}"
PROFILE_OOS_END="${_PROFILE_DEFAULTS[9]:-}"

# Parameters (can be overridden by environment variables)
GOAL="${GOAL:-${PROFILE_GOAL:-order imbalance momentum with spread filter}}"
SYMBOLS="${SYMBOLS:-${PROFILE_SYMBOLS:-000660}}"
IS_START="${IS_START:-${PROFILE_IS_START:-20260313}}"
IS_END="${IS_END:-${PROFILE_IS_END:-20260313}}"
OOS_START="${OOS_START:-${PROFILE_OOS_START:-}}"
OOS_END="${OOS_END:-${PROFILE_OOS_END:-}}"
MODEL="${MODEL:-${PROFILE_MODEL:-gpt-4o}}"
MAX_PLAN_ITERATIONS="${MAX_PLAN_ITERATIONS:-${PROFILE_MAX_PLAN_ITERATIONS:-10}}"
MAX_CODE_ATTEMPTS="${MAX_CODE_ATTEMPTS:-${PROFILE_MAX_CODE_ATTEMPTS:-3}}"
OPTIMIZE_N_TRIALS="${OPTIMIZE_N_TRIALS:-${PROFILE_OPT_TRIALS:-30}}"
MEMORY_DIR="${MEMORY_DIR:-outputs/memory_spec}"
OUTPUT_DIR="${OUTPUT_DIR:-outputs/backtests_spec}"

# Build OOS arguments
OOS_ARGS=()
if [[ -n "${OOS_START}" && -n "${OOS_END}" ]]; then
    OOS_ARGS=(--oos-start "${OOS_START}" --oos-end "${OOS_END}")
fi

echo "============================================================"
echo "  Spec-Centric Strategy Loop (live)"
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
echo "  strategy: spec"
echo "  plan_iter: ${MAX_PLAN_ITERATIONS}  code_attempts: ${MAX_CODE_ATTEMPTS}  optuna_trials: ${OPTIMIZE_N_TRIALS}"
echo "============================================================"

PYTHONPATH=src python scripts/run_strategy_loop.py \
    --research-goal "${GOAL}" \
    --symbols "${SYMBOLS}" \
    --is-start "${IS_START}" \
    --is-end "${IS_END}" \
    "${OOS_ARGS[@]}" \
    --model "${MODEL}" \
    --strategy-mode spec \
    --max-plan-iterations "${MAX_PLAN_ITERATIONS}" \
    --max-code-attempts "${MAX_CODE_ATTEMPTS}" \
    --optimize-n-trials "${OPTIMIZE_N_TRIALS}" \
    --memory-dir "${MEMORY_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    --profile "${PROFILE}"
