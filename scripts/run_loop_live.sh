#!/usr/bin/env bash
# live 모드 — 실제 OpenAI API 사용
# 실행 전 OPENAI_API_KEY 환경변수 설정 필요
#   export OPENAI_API_KEY=sk-...
set -euo pipefail

cd "$(dirname "$0")/.."

: "${OPENAI_API_KEY:?OPENAI_API_KEY is not set}"

PYTHONPATH=src python scripts/run_strategy_loop.py \
    --research-goal "order imbalance momentum" \
    --symbol 005930 \
    --start-date 20260313 \
    --mode live \
    --model gpt-4o-mini \
    --n-iter 5
