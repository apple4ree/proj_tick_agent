# Phase 4 Benchmark Baselines

## Tier Role

이 문서는 **Tier 2 baseline anchor**다.
회귀 비교 시 우선 참조하는 최소 baseline 지표/필드 집합을 정의한다.

## Artifact Baseline

- `outputs/benchmarks/phase4_benchmark_freeze.json`
- `outputs/benchmarks/phase4_benchmark_freeze.md`

## Single-Symbol Matrix Baseline

| label | resample | delay_ms | run_id |
|---|---|---:|---|
| A | 1s | 0 | cd0830ab-7251-402b-bd07-9e9d55f3f1b3 |
| B | 1s | 200 | 6c8a95aa-6446-41a6-ab9f-019c2e80c28d |
| C | 500ms | 0 | 67019e1f-0d01-41d3-8cb9-8b601ac0afb1 |
| D | 500ms | 200 | a8b620f1-45f7-4e91-895c-0e87c1402cff |

Primary monitored metrics:
- signal_count, parent_order_count, child_order_count, children_per_parent, n_fills, cancel_rate
- queue_blocked_count, blocked_miss_count, maker_fill_ratio
- adverse_selection_share, timeout_share
- net_pnl, total_commission, total_slippage, total_impact
- loop_s, total_s

## Review/Repair Baseline

| variant | repair_applied | feedback_aware_repair | final_static_passed | execution_policy_explicit |
|---|---:|---:|---:|---:|
| static_only | false | false | true | false |
| llm_review | false | false | true | false |
| auto_repair | true | false | true | true |
| feedback_aware_auto_repair | true | true | true | true |

## Historical Feedback Anchors

- churn-heavy run: `83b123e2-2755-499d-9091-52e96f69a51b`
- improved run: `74322b9d-2096-4e1b-a1f0-ee263dc36666`
- baseline deltas:
  - `child_order_count_delta=-1839`
  - `cancel_rate_delta=-0.2331`

## Contract Freeze Anchors

- summary core realism fields: fixed presence
- realism_diagnostics core sections and nested keys: fixed presence
- review pipeline result keys: fixed presence
- plot freeze set:
  - `dashboard.png`
  - `intraday_cumulative_profit.png`
  - `trade_timeline.png`
