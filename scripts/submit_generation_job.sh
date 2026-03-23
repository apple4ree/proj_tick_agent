#!/usr/bin/env bash
# ==============================================
# Generation Job 제출
# ==============================================
# 사용법:
#   ./scripts/submit_generation_job.sh "order imbalance alpha"
#   ./scripts/submit_generation_job.sh "spread reversion" --profile prod
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <research_goal> [--profile <name>] [--backend <backend>] [--mode <mode>]"
    exit 1
fi

GOAL="$1"
shift

exec python scripts/generate_strategy.py --goal "${GOAL}" "$@"
