# Phase 4 Benchmark Freeze Protocol

## Tier Role

이 문서는 **Tier 2 baseline/freeze 계약 문서**다.
- Tier 1(README/PIPELINE)이 current behavior를 설명한다.
- 본 문서는 baseline 재검증 절차와 회귀 기준을 고정한다.

## Objective

Phase 4 focuses on baseline revalidation and freeze, not new feature work.
Target loop: generation -> review/repair -> backtest -> reporting/plots.

## Canonical Benchmark Matrix

### Single-symbol canonical runs
- symbol: 005930
- representative date: 20260313
- resample: 1s, 500ms
- market_data_delay_ms: 0, 200
- latency profile: backtest default (latency_ms=100 alias mapping)

Matrix labels:
- A: 1s, delay 0
- B: 1s, delay 200
- C: 500ms, delay 0
- D: 500ms, delay 200

### Review/repair variants
- static
- llm-review
- auto-repair
- feedback-aware auto-repair

### Real saved-run feedback loop anchors
- churn-heavy: 83b123e2-2755-499d-9091-52e96f69a51b
- improved: 74322b9d-2096-4e1b-a1f0-ee263dc36666

## Reproduction Commands

- `PYTHONPATH=src /home/dgu/.conda/envs/alphaagent/bin/python scripts/internal/adhoc/run_phase4_benchmark_freeze.py`
- `PYTHONPATH=src /home/dgu/.conda/envs/alphaagent/bin/python scripts/internal/adhoc/compare_phase4_baselines.py --candidate outputs/benchmarks/phase4_benchmark_freeze.json`

## Freeze Artifacts

- `outputs/benchmarks/phase4_benchmark_freeze.json`
- `outputs/benchmarks/phase4_benchmark_freeze.md`
- `outputs/backtests/<run_id>/...` (matrix/variant runs)

## Contract Freeze Scope

Prompt contracts:
- generation prompt: canonical backtest constraint summary
- review prompt: canonical summary + optional feedback summary/json
- repair prompt: constrained operation model only

Backtest artifacts:
- summary.json core realism fields
- realism_diagnostics.json section and nested field presence

Review artifacts:
- static_review
- llm_review
- repair_plan
- repair_applied
- final_static_review
- final_passed
- repaired_spec
- backtest_feedback
- feedback_aware_repair

## Behavioral Freeze Targets

- short-horizon without explicit execution policy is risky
- env-aware reviewer reflects cadence and latency/tick ratio
- feedback-aware repair reprioritizes by failure pattern
- prompt contract keeps queue/latency/replace/cost friction context

## Regression Tolerance

Exact match required:
- contract field presence
- feedback-aware repair priority ordering for same flags
- core prompt-contract anchors

Numeric drift allowed:
- pnl/cost/timing values

Smoke-only:
- image bytes and run ids

## Deferred Scope

- full staged replace state machine
- deeper queue instrumentation beyond aggregates
- raw csv trace feedback injection
- full universe operational guarantee
- live/replay LLM runtime variance (mock mode is deterministic baseline)
