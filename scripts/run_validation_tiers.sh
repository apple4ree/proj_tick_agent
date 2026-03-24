#!/usr/bin/env bash
set -euo pipefail

# Validation tiers:
# - smoke: quick wiring checks only (not product-grade quality gate)
# - stronger: integration/regression checks with real fill + latency + impact behavior

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TIER="${1:-all}"

run_help_tests() {
  echo "[SMOKE][HELP] CLI wiring"
  PYTHONPATH=src python scripts/generate_strategy.py --help >/dev/null
  PYTHONPATH=src python scripts/review_strategy.py --help >/dev/null
  PYTHONPATH=src python scripts/backtest.py --help >/dev/null
  PYTHONPATH=src python scripts/backtest_strategy_universe.py --help >/dev/null
  PYTHONPATH=src python scripts/run_backtest_worker.py --help >/dev/null
}

run_smoke_unit_tests() {
  echo "[SMOKE][FAST] lightweight tests"
  PYTHONPATH=src python -m pytest     tests/test_generation_direct_mode.py     tests/test_generation_worker.py     tests/test_v2_execution_hint_integration.py     -q
}

run_smoke_short_run_tests() {
  echo "[SMOKE][SHORT-RUN] minimal runtime path checks"
  PYTHONPATH=src python -m pytest     tests/test_backtest_script.py     -q
}

run_smoke() {
  echo "[SMOKE] quick wiring checks (non-gating for product quality)"
  run_help_tests
  run_smoke_unit_tests
  run_smoke_short_run_tests
}

run_stronger() {
  echo "[STRONGER] integration + regression (quality gate)"
  PYTHONPATH=src python -m pytest     tests/test_v2_stronger_integration.py     tests/test_pipeline_runner.py     tests/test_v2_phase3.py     tests/test_registry_v2_integration.py     tests/test_backtest_worker.py     -q
}

case "$TIER" in
  smoke)
    run_smoke
    ;;
  stronger)
    run_stronger
    ;;
  all)
    run_smoke
    run_stronger
    ;;
  *)
    echo "Usage: $0 [smoke|stronger|all]" >&2
    exit 2
    ;;
esac
