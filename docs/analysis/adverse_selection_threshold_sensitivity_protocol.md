# Adverse Selection Threshold Sensitivity — Protocol

**Date**: 2026-03-27
**Status**: Final
**Scope**: Experiment/analysis only (no engine semantics change)

## Objective

Measure how sensitive `500ms, market_data_delay_ms=0` child churn is to
`adverse_selection_threshold_bps`.

Primary question: if adverse-selection cancel churn was the dominant mechanism,
does raising the threshold materially reduce child-order churn and runtime cost?

## Source of truth

- `docs/analysis/child_order_explosion_500ms_d0_results.md`
- `docs/backtest_realism_design.md`

## Fixed setup

- Symbol: `005930`
- Date: `20260313` (same as child explosion analysis)
- Strategy spec: `strategies/examples/stateful_cooldown_momentum_v2.0.json`
- Resample: `500ms`
- `market_data_delay_ms`: `0`
- Backtest path: single-symbol only
- Engine/public CLI changes: none

## Sensitivity matrix

| run_id | adverse_selection_threshold_bps |
|---|---:|
| T10 | 10 |
| T15 | 15 |
| T20 | 20 |
| T30 | 30 |

## Required metrics

1. `signal_count`
2. `parent_order_count`
3. `child_order_count`
4. `children_per_parent`
5. `n_fills`
6. `cancel_rate`
7. `cancel_reason` shares
8. `avg_child_lifetime_seconds`
9. dominant hotspot parent (`n_children`, `n_fills`)
10. `loop_s`
11. `total_s`
12. `net_pnl`

## Execution plan

1. Reuse existing backtest pipeline with script-local threshold injection only.
2. Run the 4 thresholds under identical market data / strategy conditions.
3. Save raw output as JSON.
4. Produce progress + results markdown with table and interpretation.

## Artifacts

- Protocol: `docs/analysis/adverse_selection_threshold_sensitivity_protocol.md` (this file)
- Progress: `docs/analysis/adverse_selection_threshold_sensitivity_progress.md`
- Results: `docs/analysis/adverse_selection_threshold_sensitivity_results.md`
- Raw JSON: `outputs/benchmarks/adverse_selection_threshold_sensitivity.json`
