#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# run_generate_review_backtest.sh
#
# End-to-end launcher: generate strategy → review → backtest
# Uses direct CLI chaining (no job queue / worker).
#
# Usage:
#   # Single-symbol
#   ./scripts/run_generate_review_backtest.sh \
#       --goal "order imbalance alpha" \
#       --symbol 005930 --start-date 2026-03-13
#
#   # Universe mode
#   ./scripts/run_generate_review_backtest.sh \
#       --goal "order imbalance alpha" \
#       --universe --start-date 2026-03-13
#
#   # With OpenAI backend
#   ./scripts/run_generate_review_backtest.sh \
#       --goal "spread mean reversion" \
#       --symbol 005930 --start-date 2026-03-13 \
#       --backend openai --mode mock
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Project root ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

export PYTHONPATH="${PROJECT_ROOT}/src"

# ── Defaults ─────────────────────────────────────────────────────────
GOAL=""
SYMBOL=""
START_DATE=""
END_DATE=""
UNIVERSE=false
PROFILE=""
CONFIG=""
BACKEND=""
MODE=""
AUTO_APPROVE=false

# ── Argument parsing ─────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

End-to-end: generate strategy -> review -> backtest

Required:
  --goal <text>         Research goal for strategy generation

Single-symbol mode (default):
  --symbol <code>       KRX symbol code (e.g. 005930)

Universe mode:
  --universe            Run backtest across all available symbols

Backtest dates:
  --start-date <date>   Start date (YYYYMMDD or YYYY-MM-DD)
  --end-date <date>     End date (optional, defaults to start-date)

Optional:
  --profile <name>      Config profile (dev, smoke, prod)
  --config <path>       YAML override config file
  --backend <type>      Generation backend (template | openai)
  --mode <type>         OpenAI mode (live | mock | replay)
  --auto-approve        Auto-approve generated spec
  -h, --help            Show this help message
EOF
    exit 0
}

if [[ $# -eq 0 ]]; then
    usage
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --goal)        GOAL="$2"; shift 2 ;;
        --symbol)      SYMBOL="$2"; shift 2 ;;
        --start-date)  START_DATE="$2"; shift 2 ;;
        --end-date)    END_DATE="$2"; shift 2 ;;
        --universe)    UNIVERSE=true; shift ;;
        --profile)     PROFILE="$2"; shift 2 ;;
        --config)      CONFIG="$2"; shift 2 ;;
        --backend)     BACKEND="$2"; shift 2 ;;
        --mode)        MODE="$2"; shift 2 ;;
        --auto-approve) AUTO_APPROVE=true; shift ;;
        -h|--help)     usage ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

# ── Validation ───────────────────────────────────────────────────────
if [[ -z "${GOAL}" ]]; then
    echo "ERROR: --goal is required" >&2
    exit 1
fi

if [[ "${UNIVERSE}" == false && -z "${SYMBOL}" ]]; then
    echo "ERROR: --symbol is required in single-symbol mode (or use --universe)" >&2
    exit 1
fi

if [[ -z "${START_DATE}" ]]; then
    echo "ERROR: --start-date is required" >&2
    exit 1
fi

# ── OpenAI env check ─────────────────────────────────────────────────
if [[ "${BACKEND}" == "openai" && "${MODE}" == "live" ]]; then
    : "${OPENAI_API_KEY:?OPENAI_API_KEY is required for backend=openai, mode=live}"
fi

# ── Build CLI fragments ──────────────────────────────────────────────
GEN_ARGS=( --goal "${GOAL}" --direct )
REVIEW_ARGS=()
BT_ARGS=( --start-date "${START_DATE}" )

if [[ -n "${PROFILE}" ]]; then
    GEN_ARGS+=( --profile "${PROFILE}" )
    BT_ARGS+=( --profile "${PROFILE}" )
fi
if [[ -n "${CONFIG}" ]]; then
    GEN_ARGS+=( --config "${CONFIG}" )
    BT_ARGS+=( --config "${CONFIG}" )
fi
if [[ -n "${BACKEND}" ]]; then
    GEN_ARGS+=( --backend "${BACKEND}" )
fi
if [[ -n "${MODE}" ]]; then
    GEN_ARGS+=( --mode "${MODE}" )
fi
if [[ "${AUTO_APPROVE}" == true ]]; then
    GEN_ARGS+=( --auto-approve )
fi
if [[ -n "${END_DATE}" ]]; then
    BT_ARGS+=( --end-date "${END_DATE}" )
fi

# ══════════════════════════════════════════════════════════════════════
echo "================================================================"
echo " End-to-End: Generate -> Review -> Backtest"
echo "================================================================"
echo "  goal:       ${GOAL}"
if [[ "${UNIVERSE}" == true ]]; then
    echo "  mode:       universe"
else
    echo "  symbol:     ${SYMBOL}"
fi
echo "  start-date: ${START_DATE}"
[[ -n "${END_DATE}" ]] && echo "  end-date:   ${END_DATE}"
[[ -n "${BACKEND}" ]] && echo "  backend:    ${BACKEND}"
[[ -n "${MODE}" ]]    && echo "  mode:       ${MODE}"
echo "================================================================"
echo ""

# ── Step 1: Generate strategy ────────────────────────────────────────
echo "[1/3] Generating strategy..."
echo "────────────────────────────────────────────────────────────────"

GEN_OUTPUT=$(python scripts/generate_strategy.py "${GEN_ARGS[@]}" 2>&1)
GEN_EXIT=$?

echo "${GEN_OUTPUT}"

if [[ ${GEN_EXIT} -ne 0 ]]; then
    echo ""
    echo "ERROR: Strategy generation failed (exit code ${GEN_EXIT})" >&2
    exit 1
fi

# Parse GENERATED_SPEC= from output
SPEC_PATH=$(echo "${GEN_OUTPUT}" | grep '^GENERATED_SPEC=' | tail -1 | cut -d'=' -f2-)

if [[ -z "${SPEC_PATH}" ]]; then
    echo ""
    echo "ERROR: Could not parse GENERATED_SPEC from generation output" >&2
    exit 1
fi

echo ""
echo "  -> Spec saved: ${SPEC_PATH}"
echo ""

# ── Step 2: Review strategy ─────────────────────────────────────────
echo "[2/3] Reviewing strategy..."
echo "────────────────────────────────────────────────────────────────"

REVIEW_OUTPUT=$(python scripts/review_strategy.py "${SPEC_PATH}" 2>&1) || true
echo "${REVIEW_OUTPUT}"

# Parse REVIEW_STATUS= from output
REVIEW_STATUS=$(echo "${REVIEW_OUTPUT}" | grep '^REVIEW_STATUS=' | tail -1 | cut -d'=' -f2-)

if [[ "${REVIEW_STATUS}" != "PASSED" ]]; then
    echo ""
    echo "ERROR: Strategy review FAILED — aborting backtest." >&2
    exit 1
fi

echo ""
echo "  -> Review: PASSED"
echo ""

# ── Step 3: Backtest ─────────────────────────────────────────────────
if [[ "${UNIVERSE}" == true ]]; then
    echo "[3/3] Running universe backtest..."
    echo "────────────────────────────────────────────────────────────────"
    python scripts/backtest_strategy_universe.py \
        --spec "${SPEC_PATH}" \
        "${BT_ARGS[@]}"
else
    echo "[3/3] Running single-symbol backtest..."
    echo "────────────────────────────────────────────────────────────────"
    python scripts/backtest.py \
        --spec "${SPEC_PATH}" \
        --symbol "${SYMBOL}" \
        "${BT_ARGS[@]}"
fi

echo ""
echo "================================================================"
echo " End-to-End Complete"
echo "================================================================"
echo "  spec:    ${SPEC_PATH}"
echo "  review:  PASSED"
echo "  backtest: done"
echo "================================================================"
