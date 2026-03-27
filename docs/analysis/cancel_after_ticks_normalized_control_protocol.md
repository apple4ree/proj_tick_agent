# Normalized `cancel_after_ticks` Control Experiment — Protocol

**Date**: 2026-03-26
**Status**: In Progress
**Purpose**: Diagnostic experiment only — no engine modifications

## Objective

Determine whether doubling `cancel_after_ticks` at 500ms/delay=0 materially
reduces the child-order explosion identified in the prior root-cause analysis.

This isolates the **cancel-timer sensitivity** axis: does the shorter wall-clock
cancel timeout (5s at 500ms vs 10s at 1s) drive the churn, or is the root cause
elsewhere (adverse_selection detection frequency)?

## Source of Truth

- `docs/analysis/child_order_explosion_500ms_d0_results.md`
- `docs/analysis/benchmark_revalidation_tick_time_alignment_results.md`
- `docs/analysis/tick_time_semantics_alignment.md`

## Core Questions

1. Does `cancel_after_ticks ×2` at 500ms/d=0 meaningfully reduce child count?
2. Is the reduction explained by lifecycle churn decrease (not signal/parent change)?
3. Does the dominant hotspot parent escape zero-fill churn?
4. Does this support "wall-clock cancel cadence is a key root-cause axis"?

## Override Rules

**Only `cancel_after_ticks`-related values are doubled. Nothing else changes.**

| parameter | location | baseline | C_ctrl |
|-----------|----------|----------|--------|
| `cancel_after_ticks` | `execution_policy` | 10 | **20** |
| `cancel_after_ticks` | `adaptation_rules[0].override` | 4 | **8** |

**Unchanged parameters:**
- `cooldown_ticks`: 30 (entry_policies)
- `holding_ticks` threshold: 25 (exit_policies)
- `max_reprices`: 2 / 1 (execution_policy / adaptation)
- `LagExpr`, `PersistExpr`, `RollingExpr` steps: unchanged
- `placement_mode`: unchanged (adaptive / passive_only)

## Run Matrix

### Required: Single-symbol (005930, 20260313)

| run_id | resample | delay_ms | cancel_after_ticks | description |
|--------|----------|----------|--------------------|-------------|
| A | 1s | 0 | baseline (10/4) | reference |
| C | 500ms | 0 | baseline (10/4) | explosion case |
| C_ctrl | 500ms | 0 | 2× (20/8) | cancel-timer control |

### Optional

| run_id | resample | delay_ms | cancel_after_ticks | description |
|--------|----------|----------|--------------------|-------------|
| B | 1s | 200 | baseline (10/4) | delay control |
| D | 500ms | 200 | baseline (10/4) | delay+cadence |
| D_ctrl | 500ms | 200 | 2× (20/8) | delay+cancel control |

## Strategy

- Spec: `strategies/examples/stateful_cooldown_momentum_v2.0.json`
- Symbol: 005930 (Samsung Electronics)
- Date: 20260313

## Metrics Collected Per Run

### Basic
1. signal_count
2. parent_order_count
3. child_order_count
4. children_per_parent (mean, median, max)
5. n_fills
6. cancel_rate
7. avg_child_lifetime_seconds
8. avg_holding_seconds
9. total_s / loop_s
10. net_pnl
11. canonical_tick_interval_ms
12. market_data_delay_ms

### Child lifecycle
13. cancel_reason counts / share
14. total_replacements
15. reprice_count histogram

### Hotspot
16. Top 10 parents by child count
17. Dominant parent: children, share, fills, cancel reasons

## Judgment Criteria

### Baseline reproduction
A and C must approximately match prior analysis:
- A: ~54 signals, ~1,979 children, ~36.6 ch/parent
- C: ~56 signals, ~15,077 children, ~269.2 ch/parent

### C vs C_ctrl comparison
1. children/parent reduction: significant = >50% decrease
2. avg_child_lifetime increase: expected if cancel timer matters
3. adverse_selection share change
4. dominant parent fill escape (0 → ≥1)
5. loop_s reduction

### Conclusion (one of three)
- `cancel_after_ticks normalization materially reduces the 500ms child-order explosion.`
- `cancel_after_ticks normalization helps, but does not resolve the dominant churn mechanism.`
- `cancel_after_ticks normalization has little effect; the root cause lies elsewhere.`

## Output Files

1. Protocol: `docs/analysis/cancel_after_ticks_normalized_control_protocol.md` (this file)
2. Progress: `docs/analysis/cancel_after_ticks_normalized_control_progress.md`
3. Results: `docs/analysis/cancel_after_ticks_normalized_control_results.md`
4. Raw JSON: `outputs/benchmarks/cancel_after_ticks_normalized_control.json`
5. Hotspots: `outputs/benchmarks/cancel_after_ticks_normalized_control_hotspots.json`
