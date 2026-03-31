#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# run_generate_review_backtest_batch.sh
#
# Sequential universe batch launcher built on top of:
#   scripts/run_generate_review_backtest.sh
#
# Notes:
# - No parallel execution (intentional: underlying wrapper uses fixed /tmp logs)
# - Goal-level success/failure is always recorded in summary.csv/summary.md
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

GOALS_FILE=""
START_DATE=""
END_DATE=""
PROFILE=""
CONFIG=""
BACKEND=""
REVIEW_MODE=""
CONTINUE_ON_ERROR=true
FAIL_FAST=false
OUT_DIR=""

RUN_TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

usage() {
    cat <<'EOF_HELP'
Usage: run_generate_review_backtest_batch.sh [OPTIONS]

Run multiple goals sequentially in universe mode by reusing:
  scripts/run_generate_review_backtest.sh

Required:
  --goals-file <path>                Text file, one goal per line
  --start-date <YYYYMMDD|YYYY-MM-DD>

Optional:
  --end-date <YYYYMMDD|YYYY-MM-DD>
  --profile <name>
  --config <path>
  --backend <template|openai>
  --review-mode <static|llm-review|auto-repair>
  --continue-on-error                Continue after goal failure (default)
  --fail-fast                        Stop on first goal failure
  --out-dir <path>                   Default: outputs/batch_runs/<timestamp>
  -h, --help

goals-file format:
  - One goal per line
  - Empty lines ignored
  - Lines starting with '#' ignored
EOF_HELP
    exit 0
}

require_value() {
    local opt="$1"
    if [[ $# -lt 2 || -z "${2}" ]]; then
        echo "ERROR: ${opt} requires a value" >&2
        exit 1
    fi
}

trim_whitespace() {
    local s="$1"
    printf '%s' "${s}" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//'
}

slugify() {
    local raw="$1"
    local clipped="${raw:0:256}"
    local slug=""
    slug="$(printf '%s' "${clipped}" \
        | tr '[:upper:]' '[:lower:]' \
        | sed -E 's/[^a-z0-9_-]+/-/g; s/^-+//; s/-+$//; s/-+/-/g')"

    if [[ -z "${slug}" ]]; then
        slug="goal"
    fi

    printf '%s' "${slug:0:48}"
}

preview_goal() {
    local goal="$1"
    local max_len=120
    local goal_len=${#goal}

    if (( goal_len <= max_len )); then
        printf '%s' "${goal}"
        return 0
    fi

    printf '%s... (len=%d)' "${goal:0:max_len}" "${goal_len}"
}

csv_escape() {
    local s="$1"
    s="${s//$'\r'/ }"
    s="${s//$'\n'/ }"
    s="${s//\"/\"\"}"
    printf '"%s"' "${s}"
}

md_escape() {
    local s="$1"
    s="${s//$'\r'/ }"
    s="${s//$'\n'/ }"
    s="${s//|/\\|}"
    printf '%s' "${s}"
}

json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\t'/\\t}"
    printf '%s' "${s}"
}

append_csv_row() {
    local is_first=true
    local field

    {
        for field in "$@"; do
            if [[ "${is_first}" == true ]]; then
                is_first=false
            else
                printf ','
            fi
            csv_escape "${field}"
        done
        printf '\n'
    } >> "${SUMMARY_CSV}"
}

append_md_row() {
    local idx="$1"
    local status="$2"
    local exit_code="$3"
    local duration_sec="$4"
    local review_status="$5"
    local goal="$6"
    local generated_spec="$7"
    local backtest_spec="$8"
    local artifact_dir="$9"
    local backtest_dir="${10}"
    local log_path="${11}"

    printf '| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |\n' \
        "$(md_escape "${idx}")" \
        "$(md_escape "${status}")" \
        "$(md_escape "${exit_code}")" \
        "$(md_escape "${duration_sec}")" \
        "$(md_escape "${review_status}")" \
        "$(md_escape "${goal}")" \
        "$(md_escape "${generated_spec}")" \
        "$(md_escape "${backtest_spec}")" \
        "$(md_escape "${artifact_dir}")" \
        "$(md_escape "${backtest_dir}")" \
        "$(md_escape "${log_path}")" \
        >> "${SUMMARY_MD}"
}

extract_prefixed_value() {
    local prefix="$1"
    local log_path="$2"

    awk -v prefix="${prefix}" '
        {
            line = $0
            sub(/\r$/, "", line)
            sub(/^[[:space:]]+/, "", line)
            if (index(line, prefix) == 1) {
                value = substr(line, length(prefix) + 1)
                sub(/^[[:space:]]+/, "", value)
                print value
            }
        }
    ' "${log_path}" | tail -n 1
}

run_logged_step() {
    local log_path="$1"
    shift

    set +e
    "$@" 2>&1 | tee "${log_path}"
    local cmd_exit=${PIPESTATUS[0]}
    set -e

    return "${cmd_exit}"
}

if [[ $# -eq 0 ]]; then
    usage
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --goals-file)
            require_value "$1" "${2:-}"
            GOALS_FILE="$2"
            shift 2
            ;;
        --start-date)
            require_value "$1" "${2:-}"
            START_DATE="$2"
            shift 2
            ;;
        --end-date)
            require_value "$1" "${2:-}"
            END_DATE="$2"
            shift 2
            ;;
        --profile)
            require_value "$1" "${2:-}"
            PROFILE="$2"
            shift 2
            ;;
        --config)
            require_value "$1" "${2:-}"
            CONFIG="$2"
            shift 2
            ;;
        --backend)
            require_value "$1" "${2:-}"
            BACKEND="$2"
            shift 2
            ;;
        --review-mode)
            require_value "$1" "${2:-}"
            REVIEW_MODE="$2"
            shift 2
            ;;
        --continue-on-error)
            CONTINUE_ON_ERROR=true
            FAIL_FAST=false
            shift
            ;;
        --fail-fast)
            FAIL_FAST=true
            CONTINUE_ON_ERROR=false
            shift
            ;;
        --out-dir)
            require_value "$1" "${2:-}"
            OUT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

if [[ -z "${GOALS_FILE}" ]]; then
    echo "ERROR: --goals-file is required" >&2
    exit 1
fi
if [[ -z "${START_DATE}" ]]; then
    echo "ERROR: --start-date is required" >&2
    exit 1
fi
if [[ ! -f "${GOALS_FILE}" ]]; then
    echo "ERROR: goals file not found: ${GOALS_FILE}" >&2
    exit 1
fi

case "${BACKEND}" in
    ""|template|openai) ;;
    *)
        echo "ERROR: --backend must be one of: template, openai" >&2
        exit 1
        ;;
esac

case "${REVIEW_MODE}" in
    ""|static|llm-review|auto-repair) ;;
    *)
        echo "ERROR: --review-mode must be one of: static, llm-review, auto-repair" >&2
        exit 1
        ;;
esac

if [[ -z "${OUT_DIR}" ]]; then
    OUT_DIR="outputs/batch_runs/${RUN_TIMESTAMP}"
fi

LOG_DIR="${OUT_DIR}/logs"
mkdir -p "${LOG_DIR}"

OUT_DIR_ABS="$(cd "${OUT_DIR}" && pwd)"
SUMMARY_CSV="${OUT_DIR_ABS}/summary.csv"
SUMMARY_MD="${OUT_DIR_ABS}/summary.md"
META_JSON="${OUT_DIR_ABS}/meta.json"

GOALS=()
while IFS= read -r raw_line || [[ -n "${raw_line}" ]]; do
    raw_line="${raw_line%$'\r'}"
    line="$(trim_whitespace "${raw_line}")"
    if [[ -z "${line}" ]]; then
        continue
    fi
    if [[ "${line}" == \#* ]]; then
        continue
    fi
    GOALS+=( "${line}" )
done < "${GOALS_FILE}"

GOAL_COUNT="${#GOALS[@]}"
if [[ "${GOAL_COUNT}" -eq 0 ]]; then
    echo "ERROR: no runnable goals found in ${GOALS_FILE}" >&2
    exit 1
fi

COMMON_ARGS=( --universe --start-date "${START_DATE}" )
if [[ -n "${END_DATE}" ]]; then
    COMMON_ARGS+=( --end-date "${END_DATE}" )
fi
if [[ -n "${PROFILE}" ]]; then
    COMMON_ARGS+=( --profile "${PROFILE}" )
fi
if [[ -n "${CONFIG}" ]]; then
    COMMON_ARGS+=( --config "${CONFIG}" )
fi
if [[ -n "${BACKEND}" ]]; then
    COMMON_ARGS+=( --backend "${BACKEND}" )
fi
if [[ -n "${REVIEW_MODE}" ]]; then
    COMMON_ARGS+=( --review-mode "${REVIEW_MODE}" )
fi

printf '%s\n' 'idx,goal,status,exit_code,started_at,ended_at,duration_sec,generated_spec,backtest_spec,review_status,artifact_dir,backtest_dir,log_path' > "${SUMMARY_CSV}"

{
    echo "# Batch Run Summary"
    echo ""
    echo "- run_timestamp: ${RUN_TIMESTAMP}"
    echo "- goals_file: ${GOALS_FILE}"
    echo "- out_dir: ${OUT_DIR_ABS}"
    echo "- goal_count: ${GOAL_COUNT}"
    echo "- mode: universe (sequential)"
    echo "- continue_on_error: ${CONTINUE_ON_ERROR}"
    echo "- fail_fast: ${FAIL_FAST}"
    echo ""
    echo "| idx | status | exit_code | duration_sec | review_status | goal | generated_spec | backtest_spec | artifact_dir | backtest_dir | log_path |"
    echo "|---|---|---:|---:|---|---|---|---|---|---|---|"
} > "${SUMMARY_MD}"

PASSED_COUNT=0
FAILED_COUNT=0
EXECUTED_COUNT=0

echo "================================================================"
echo " Batch Run: Generate -> Review -> Backtest (Universe, Sequential)"
echo "================================================================"
echo "  goals-file:     ${GOALS_FILE}"
echo "  out-dir:        ${OUT_DIR_ABS}"
echo "  goal-count:     ${GOAL_COUNT}"
echo "  start-date:     ${START_DATE}"
[[ -n "${END_DATE}" ]] && echo "  end-date:       ${END_DATE}"
[[ -n "${PROFILE}" ]] && echo "  profile:        ${PROFILE}"
[[ -n "${CONFIG}" ]] && echo "  config:         ${CONFIG}"
[[ -n "${BACKEND}" ]] && echo "  backend:        ${BACKEND}"
[[ -n "${REVIEW_MODE}" ]] && echo "  review-mode:    ${REVIEW_MODE}"
echo "  continue-error: ${CONTINUE_ON_ERROR}"
echo "  fail-fast:      ${FAIL_FAST}"
echo "================================================================"

for i in "${!GOALS[@]}"; do
    idx="$((i + 1))"
    idx_pad="$(printf '%03d' "${idx}")"
    goal="${GOALS[$i]}"
    slug="$(slugify "${goal}")"
    log_path="${LOG_DIR}/${idx_pad}_${slug}.log"

    started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    start_epoch="$(date -u +%s)"

    run_args=( --goal "${goal}" "${COMMON_ARGS[@]}" )

    goal_display="$(preview_goal "${goal}")"

    echo ""
    echo "[${idx}/${GOAL_COUNT}] goal: ${goal_display}"
    echo "  log: ${log_path}"

    if run_logged_step "${log_path}" bash scripts/run_generate_review_backtest.sh "${run_args[@]}"; then
        exit_code=0
    else
        exit_code=$?
    fi

    ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    end_epoch="$(date -u +%s)"
    duration_sec="$((end_epoch - start_epoch))"

    generated_spec="$(extract_prefixed_value "generated-spec:" "${log_path}")"
    backtest_spec="$(extract_prefixed_value "backtest-spec:" "${log_path}")"
    review_status="$(extract_prefixed_value "review:" "${log_path}")"
    artifact_dir="$(extract_prefixed_value "artifact-dir:" "${log_path}")"
    backtest_dir="$(extract_prefixed_value "backtest-dir:" "${log_path}")"

    status="PASSED"
    if [[ "${exit_code}" -ne 0 ]]; then
        status="FAILED"
        FAILED_COUNT="$((FAILED_COUNT + 1))"
    else
        PASSED_COUNT="$((PASSED_COUNT + 1))"
    fi
    EXECUTED_COUNT="$((EXECUTED_COUNT + 1))"

    append_csv_row \
        "${idx_pad}" \
        "${goal}" \
        "${status}" \
        "${exit_code}" \
        "${started_at}" \
        "${ended_at}" \
        "${duration_sec}" \
        "${generated_spec}" \
        "${backtest_spec}" \
        "${review_status}" \
        "${artifact_dir}" \
        "${backtest_dir}" \
        "${log_path}"

    append_md_row \
        "${idx_pad}" \
        "${status}" \
        "${exit_code}" \
        "${duration_sec}" \
        "${review_status}" \
        "${goal}" \
        "${generated_spec}" \
        "${backtest_spec}" \
        "${artifact_dir}" \
        "${backtest_dir}" \
        "${log_path}"

    if [[ "${status}" == "FAILED" && "${FAIL_FAST}" == true ]]; then
        echo "  -> failed, stopping immediately due to --fail-fast"
        break
    fi
done

FINAL_EXIT=0
if [[ "${FAILED_COUNT}" -gt 0 ]]; then
    FINAL_EXIT=1
fi

RUN_ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

{
    echo ""
    echo "## Totals"
    echo ""
    echo "- executed_goal_count: ${EXECUTED_COUNT}"
    echo "- passed_count: ${PASSED_COUNT}"
    echo "- failed_count: ${FAILED_COUNT}"
    echo "- final_exit_code: ${FINAL_EXIT}"
} >> "${SUMMARY_MD}"

cat > "${META_JSON}" <<EOF_META
{
  "run_timestamp": "$(json_escape "${RUN_TIMESTAMP}")",
  "started_at": "$(json_escape "${RUN_STARTED_AT}")",
  "ended_at": "$(json_escape "${RUN_ENDED_AT}")",
  "goal_count": ${GOAL_COUNT},
  "executed_goal_count": ${EXECUTED_COUNT},
  "passed_count": ${PASSED_COUNT},
  "failed_count": ${FAILED_COUNT},
  "final_exit_code": ${FINAL_EXIT},
  "out_dir": "$(json_escape "${OUT_DIR_ABS}")",
  "options": {
    "goals_file": "$(json_escape "${GOALS_FILE}")",
    "start_date": "$(json_escape "${START_DATE}")",
    "end_date": "$(json_escape "${END_DATE}")",
    "profile": "$(json_escape "${PROFILE}")",
    "config": "$(json_escape "${CONFIG}")",
    "backend": "$(json_escape "${BACKEND}")",
    "review_mode": "$(json_escape "${REVIEW_MODE}")",
    "continue_on_error": ${CONTINUE_ON_ERROR},
    "fail_fast": ${FAIL_FAST}
  }
}
EOF_META

echo ""
echo "================================================================"
echo " Batch Run Complete"
echo "================================================================"
echo "  summary.csv:   ${SUMMARY_CSV}"
echo "  summary.md:    ${SUMMARY_MD}"
echo "  meta.json:     ${META_JSON}"
echo "  passed:        ${PASSED_COUNT}"
echo "  failed:        ${FAILED_COUNT}"
echo "  final-exit:    ${FINAL_EXIT}"
echo "================================================================"

exit "${FINAL_EXIT}"
