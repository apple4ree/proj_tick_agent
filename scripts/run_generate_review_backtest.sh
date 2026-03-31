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
#       --backend openai
#
#   # With auto-repair review
#   ./scripts/run_generate_review_backtest.sh \
#       --goal "spread mean reversion" \
#       --symbol 005930 --start-date 2026-03-13 \
#       --review-mode auto-repair
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
REVIEW_MODE="static"

GEN_LOG="/tmp/proj_gen_e2e.log"
REVIEW_LOG="/tmp/proj_review_e2e.log"
BACKTEST_LOG="/tmp/proj_backtest_e2e.log"

RESOLVED_CONFIG_BACKEND=""
RESOLVED_GENERATION_BACKEND=""
RESOLVED_GENERATION_MODE="unknown"

# ── Argument parsing ─────────────────────────────────────────────────
usage() {
    cat <<EOF_HELP
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
  --review-mode <mode>  Review mode (static | llm-review | auto-repair)
  -h, --help            Show this help message
EOF_HELP
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
        --review-mode) REVIEW_MODE="$2"; shift 2 ;;
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

case "${REVIEW_MODE}" in
    static|llm-review|auto-repair) ;;
    *)
        echo "ERROR: --review-mode must be one of: static, llm-review, auto-repair" >&2
        exit 1
        ;;
esac

extract_kv() {
    local key="$1"
    local log_path="$2"

    awk -v key="${key}" '
        index($0, key "=") == 1 {
            line = substr($0, length(key) + 2)
            sub(/\r$/, "", line)
            print line
        }
    ' "${log_path}" | tail -n 1
}

extract_prefixed_value() {
    local prefix="$1"
    local log_path="$2"

    awk -v prefix="${prefix}" '
        index($0, prefix) == 1 {
            line = substr($0, length(prefix) + 1)
            sub(/^[[:space:]]+/, "", line)
            sub(/\r$/, "", line)
            print line
        }
    ' "${log_path}" | tail -n 1
}

print_log_tail() {
    local log_path="$1"

    if [[ -f "${log_path}" ]]; then
        echo ""
        echo "Last 40 log lines (${log_path}):"
        tail -n 40 "${log_path}" || true
    fi
}

print_log_matches() {
    local log_path="$1"
    local heading="$2"
    local pattern="$3"
    local matches=""

    matches="$(grep -E "${pattern}" "${log_path}" | tail -n 12 || true)"
    if [[ -n "${matches}" ]]; then
        echo ""
        echo "${heading}:"
        printf '%s\n' "${matches}"
    fi
}

run_logged_step() {
    local log_path="$1"
    shift

    set +e
    PYTHONUNBUFFERED=1 "$@" 2>&1 | tee "${log_path}"
    local cmd_exit=${PIPESTATUS[0]}
    set -e

    return "${cmd_exit}"
}

resolve_generation_runtime() {
    local output=""

    if output="$(python - "${PROJECT_ROOT}" "${CONFIG}" "${PROFILE}" <<'PYCFG'
from pathlib import Path
import sys

project_root = Path(sys.argv[1])
config_path = sys.argv[2] or None
profile = sys.argv[3] or None

for path in (project_root, project_root / "src"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from utils.config import get_generation, load_config

cfg = load_config(config_path=config_path, profile=profile)
gen = get_generation(cfg)

print(f"GENERATION_DEFAULT_BACKEND={gen.get('backend', '')}")
print(f"GENERATION_MODE={gen.get('mode', '')}")
PYCFG
    )"; then
        RESOLVED_CONFIG_BACKEND="$(printf '%s\n' "${output}" | awk -F= '/^GENERATION_DEFAULT_BACKEND=/{print substr($0, index($0, "=") + 1)}' | tail -n 1)"
        RESOLVED_GENERATION_MODE="$(printf '%s\n' "${output}" | awk -F= '/^GENERATION_MODE=/{print substr($0, index($0, "=") + 1)}' | tail -n 1)"
    else
        echo "WARNING: could not resolve generation config; backend/mode banner may be incomplete." >&2
    fi

    if [[ -n "${BACKEND}" ]]; then
        RESOLVED_GENERATION_BACKEND="${BACKEND}"
    else
        RESOLVED_GENERATION_BACKEND="${RESOLVED_CONFIG_BACKEND:-template}"
    fi

    if [[ -z "${RESOLVED_GENERATION_MODE}" ]]; then
        RESOLVED_GENERATION_MODE="unknown"
    fi
}

summarize_generation_failure() {
    local exit_code="$1"

    echo ""
    echo "ERROR: generation failed" >&2
    echo "  exit-code: ${exit_code}" >&2
    echo "  log: ${GEN_LOG}" >&2
    print_log_matches "${GEN_LOG}" "Generation hints" "StaticReviewError|PlanParseError|fallback|Traceback|ERROR" >&2
    print_log_tail "${GEN_LOG}" >&2
}

summarize_review_failure() {
    local exit_code="$1"
    local review_status="$2"
    local artifact_dir="$3"

    echo ""
    echo "ERROR: review failed" >&2
    echo "  exit-code: ${exit_code}" >&2
    echo "  REVIEW_STATUS=${review_status:-missing}" >&2
    echo "  review-mode: ${REVIEW_MODE}" >&2
    [[ -n "${artifact_dir}" ]] && echo "  ARTIFACT_DIR=${artifact_dir}" >&2
    echo "  log: ${REVIEW_LOG}" >&2
    print_log_matches "${REVIEW_LOG}" "Review hints" "REVIEW_STATUS=|ARTIFACT_DIR=|Traceback|ERROR|FAILED" >&2
    print_log_tail "${REVIEW_LOG}" >&2
}

extract_backtest_dir() {
    local log_path="$1"
    local run_dir=""

    run_dir="$(extract_prefixed_value "Saved run artifacts:" "${log_path}")"
    if [[ -n "${run_dir}" ]]; then
        printf '%s\n' "${run_dir}"
        return 0
    fi

    run_dir="$(extract_prefixed_value "Results:" "${log_path}")"
    printf '%s\n' "${run_dir}"
}

summarize_backtest_failure() {
    local exit_code="$1"
    local backtest_dir="$2"

    echo ""
    echo "ERROR: backtest failed" >&2
    echo "  exit-code: ${exit_code}" >&2
    [[ -n "${backtest_dir}" ]] && echo "  backtest-dir: ${backtest_dir}" >&2
    echo "  log: ${BACKTEST_LOG}" >&2
    print_log_matches "${BACKTEST_LOG}" "Backtest hints" "Saved run artifacts:|Results:|Failure report:|Traceback|ERROR|FAILED" >&2
    print_log_tail "${BACKTEST_LOG}" >&2
}

# ── Build CLI fragments ──────────────────────────────────────────────
GEN_ARGS=( --goal "${GOAL}" --direct )
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
if [[ -n "${END_DATE}" ]]; then
    BT_ARGS+=( --end-date "${END_DATE}" )
fi

resolve_generation_runtime

# ══════════════════════════════════════════════════════════════════════
echo "================================================================"
echo " End-to-End: Generate -> Review -> Backtest"
echo "================================================================"
echo "  goal:         ${GOAL}"
if [[ "${UNIVERSE}" == true ]]; then
    echo "  mode:         universe"
else
    echo "  symbol:       ${SYMBOL}"
fi
echo "  start-date:   ${START_DATE}"
[[ -n "${END_DATE}" ]] && echo "  end-date:     ${END_DATE}"
echo "  backend:      ${RESOLVED_GENERATION_BACKEND}"
echo "  gen-mode:     ${RESOLVED_GENERATION_MODE}"
echo "  profile:      ${PROFILE:-default}"
echo "  review-mode:  ${REVIEW_MODE}"
echo "================================================================"
echo ""

if [[ "${BACKEND}" == "openai" ]]; then
    if [[ "${RESOLVED_GENERATION_MODE}" == "mock" ]]; then
        echo "WARNING: --backend openai requested, but resolved generation mode is mock; generation will not use live OpenAI calls." >&2
    elif [[ "${RESOLVED_GENERATION_MODE}" == "live" && -z "${OPENAI_API_KEY:-}" ]]; then
        echo "ERROR: --backend openai requested with resolved generation mode=live, but OPENAI_API_KEY is not set." >&2
        exit 1
    fi
fi

# ── Step 1: Generate strategy ────────────────────────────────────────
echo "[1/3] Generating strategy..."
echo "────────────────────────────────────────────────────────────────"
echo "  log: ${GEN_LOG}"

if run_logged_step "${GEN_LOG}" python -u scripts/generate_strategy.py "${GEN_ARGS[@]}"; then
    GEN_EXIT=0
else
    GEN_EXIT=$?
fi

if [[ ${GEN_EXIT} -ne 0 ]]; then
    summarize_generation_failure "${GEN_EXIT}"
    exit 1
fi

SPEC_PATH="$(extract_kv "GENERATED_SPEC" "${GEN_LOG}")"

if [[ -z "${SPEC_PATH}" ]]; then
    echo "" >&2
    echo "ERROR: Could not parse GENERATED_SPEC from generation output" >&2
    summarize_generation_failure "${GEN_EXIT}"
    exit 1
fi

if [[ ! -f "${SPEC_PATH}" ]]; then
    echo "" >&2
    echo "ERROR: Parsed GENERATED_SPEC does not exist: ${SPEC_PATH}" >&2
    summarize_generation_failure "${GEN_EXIT}"
    exit 1
fi

echo ""
echo "  -> Generated spec: ${SPEC_PATH}"
echo ""

# ── Step 2: Review strategy ─────────────────────────────────────────
echo "[2/3] Reviewing strategy..."
echo "────────────────────────────────────────────────────────────────"
echo "  log: ${REVIEW_LOG}"

REVIEW_ARGS=( "${SPEC_PATH}" --mode "${REVIEW_MODE}" )
if [[ -n "${PROFILE}" ]]; then
    REVIEW_ARGS+=( --profile "${PROFILE}" )
fi
if [[ -n "${CONFIG}" ]]; then
    REVIEW_ARGS+=( --config "${CONFIG}" )
fi

if run_logged_step "${REVIEW_LOG}" python -u scripts/review_strategy.py "${REVIEW_ARGS[@]}"; then
    REVIEW_EXIT=0
else
    REVIEW_EXIT=$?
fi

REVIEW_STATUS="$(extract_kv "REVIEW_STATUS" "${REVIEW_LOG}")"
ARTIFACT_DIR="$(extract_kv "ARTIFACT_DIR" "${REVIEW_LOG}")"

if [[ -z "${REVIEW_STATUS}" ]]; then
    echo "" >&2
    echo "ERROR: Could not parse REVIEW_STATUS from review output" >&2
    summarize_review_failure "${REVIEW_EXIT}" "${REVIEW_STATUS}" "${ARTIFACT_DIR}"
    exit 1
fi

if [[ "${REVIEW_MODE}" != "static" && -z "${ARTIFACT_DIR}" ]]; then
    echo "" >&2
    echo "ERROR: Could not parse ARTIFACT_DIR from review output for review mode ${REVIEW_MODE}" >&2
    summarize_review_failure "${REVIEW_EXIT}" "${REVIEW_STATUS}" "${ARTIFACT_DIR}"
    exit 1
fi

if [[ -n "${ARTIFACT_DIR}" && ! -d "${ARTIFACT_DIR}" ]]; then
    echo "" >&2
    echo "ERROR: Parsed ARTIFACT_DIR does not exist: ${ARTIFACT_DIR}" >&2
    summarize_review_failure "${REVIEW_EXIT}" "${REVIEW_STATUS}" "${ARTIFACT_DIR}"
    exit 1
fi

if [[ ${REVIEW_EXIT} -ne 0 || "${REVIEW_STATUS}" != "PASSED" ]]; then
    summarize_review_failure "${REVIEW_EXIT}" "${REVIEW_STATUS}" "${ARTIFACT_DIR}"
    exit 1
fi

FINAL_SPEC_PATH="${SPEC_PATH}"
if [[ "${REVIEW_MODE}" == "auto-repair" && -n "${ARTIFACT_DIR}" ]]; then
    REPAIRED_SPEC_PATH="${ARTIFACT_DIR}/repaired_spec.json"
    if [[ -f "${REPAIRED_SPEC_PATH}" ]]; then
        FINAL_SPEC_PATH="${REPAIRED_SPEC_PATH}"
    fi
fi

echo ""
echo "  -> Review: PASSED"
[[ -n "${ARTIFACT_DIR}" ]] && echo "  -> Artifact dir: ${ARTIFACT_DIR}"
echo "  -> Backtest spec: ${FINAL_SPEC_PATH}"
echo ""

# ── Step 3: Backtest ─────────────────────────────────────────────────
if [[ "${UNIVERSE}" == true ]]; then
    echo "[3/3] Running universe backtest..."
    echo "────────────────────────────────────────────────────────────────"
    echo "  log: ${BACKTEST_LOG}"
    if run_logged_step "${BACKTEST_LOG}" python -u scripts/backtest_strategy_universe.py \
        --spec "${FINAL_SPEC_PATH}" \
        "${BT_ARGS[@]}"; then
        BACKTEST_EXIT=0
    else
        BACKTEST_EXIT=$?
    fi
else
    echo "[3/3] Running single-symbol backtest..."
    echo "────────────────────────────────────────────────────────────────"
    echo "  log: ${BACKTEST_LOG}"
    if run_logged_step "${BACKTEST_LOG}" python -u scripts/backtest.py \
        --spec "${FINAL_SPEC_PATH}" \
        --symbol "${SYMBOL}" \
        "${BT_ARGS[@]}"; then
        BACKTEST_EXIT=0
    else
        BACKTEST_EXIT=$?
    fi
fi

BACKTEST_DIR="$(extract_backtest_dir "${BACKTEST_LOG}")"

if [[ ${BACKTEST_EXIT} -ne 0 ]]; then
    summarize_backtest_failure "${BACKTEST_EXIT}" "${BACKTEST_DIR}"
    exit 1
fi

echo ""
echo "================================================================"
echo " End-to-End Complete"
echo "================================================================"
echo "  generated-spec: ${SPEC_PATH}"
echo "  backtest-spec:  ${FINAL_SPEC_PATH}"
echo "  review:         ${REVIEW_STATUS}"
[[ -n "${ARTIFACT_DIR:-}" ]] && echo "  artifact-dir:   ${ARTIFACT_DIR}"
[[ -n "${BACKTEST_DIR:-}" ]] && echo "  backtest-dir:   ${BACKTEST_DIR}"
echo "  backtest:       done"
echo "================================================================"
