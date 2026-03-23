#!/usr/bin/env bash
# ==============================================
# Generation Worker 런처
# ==============================================
# 사용법:
#   ./scripts/run_generation_worker.sh              # 기본 config
#   ./scripts/run_generation_worker.sh --profile dev # dev 프로필
#   ./scripts/run_generation_worker.sh --once        # 단일 실행
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src"

exec python scripts/run_generation_worker.py "$@"
