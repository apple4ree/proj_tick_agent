# Phase 4 Benchmark Freeze Results

## Tier Role

이 문서는 **Tier 2 freeze 결과 스냅샷**이다.
- current behavior 설명은 Tier 1 문서를 본다.
- 본 문서는 baseline run 결과/지표를 고정해 회귀 비교에 사용한다.

## Run Metadata

- status: complete
- generated artifact: `outputs/benchmarks/phase4_benchmark_freeze.json`
- markdown snapshot: `outputs/benchmarks/phase4_benchmark_freeze.md`

## Canonical Matrix Results

| label | run_id | resample | delay_ms | signal_count | child_order_count | cancel_rate | net_pnl | loop_s | total_s |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | cd0830ab-7251-402b-bd07-9e9d55f3f1b3 | 1s | 0 | 8 | 8 | 0.0000 | -231936.36 | 0.024 | 14.698 |
| B | 6c8a95aa-6446-41a6-ab9f-019c2e80c28d | 1s | 200 | 3 | 92 | 0.9783 | -49315.85 | 0.067 | 12.484 |
| C | 67019e1f-0d01-41d3-8cb9-8b601ac0afb1 | 500ms | 0 | 16 | 16 | 0.0000 | -503108.80 | 0.041 | 12.810 |
| D | a8b620f1-45f7-4e91-895c-0e87c1402cff | 500ms | 200 | 3 | 107 | 0.9813 | -53303.30 | 0.104 | 12.066 |

## Review/Repair Variant Results

| variant | review_mode | repair_applied | feedback_aware_repair | final_static_passed | execution_policy_explicit | backtest_run_id |
|---|---|---:|---:|---:|---:|---|
| static_only | static | false | false | true | false | 9572e7f1-ffef-4f0b-818c-f68cdcc4ca02 |
| llm_review | llm-review | false | false | true | false | 5636eadb-0291-42a2-b39d-0cdae630cd5b |
| auto_repair | auto-repair | true | false | true | true | f5862b65-d888-4195-a046-a7cee11dc779 |
| feedback_aware_auto_repair | auto-repair | true | true | true | true | d0f1124f-97a0-426e-822a-2076d60ebd52 |

## Historical Feedback Loop Anchors

| case | run_id | child_order_count | cancel_rate | adverse_selection_share | timeout_share | loop_s | total_s |
|---|---|---:|---:|---:|---:|---:|---:|
| churn_heavy | 83b123e2-2755-499d-9091-52e96f69a51b | 1975 | 0.9610 | 0.8978 | 0.1022 | 8.022 | 31.515 |
| improved | 74322b9d-2096-4e1b-a1f0-ee263dc36666 | 136 | 0.7279 | 0.9596 | 0.0303 | 4.840 | 23.565 |

Derived deltas:
- child_order_count: -1839
- cancel_rate: -0.2331

## Strengths

- generation/review/backtest loop is executable on deterministic mock path
- feedback-aware auto-repair path is active and measurable
- summary/diagnostics/report/plot artifacts are consistently emitted

## Residual Risks

- numeric outputs are regime/data-sensitive
- historical anchors and synthetic matrix differ in data characteristics
- universe-scale behavior is not frozen in this phase

## Deferred Scope

- staged replace lifecycle model
- deep queue instrumentation beyond aggregate
- raw-trace feedback-to-prompt loop
- universe operational SLO freeze
