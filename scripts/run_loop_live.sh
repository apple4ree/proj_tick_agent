#!/usr/bin/env bash
# Live mode - uses the real OpenAI API
# OPENAI_API_KEY must be set before running
#   export OPENAI_API_KEY=sk-...
set -euo pipefail

cd "$(dirname "$0")/.."

: "${OPENAI_API_KEY:?OPENAI_API_KEY is not set}"

PYTHONPATH=src python scripts/run_strategy_loop.py \
    --research-goal "order imbalance momentum" \
    --symbols 000660 \
    --is-start 20260313 --is-end 20260313 \
    --optimize-n-trials 20 \
    --mode live \
    --model gpt-4o \
    --n-iter 30
