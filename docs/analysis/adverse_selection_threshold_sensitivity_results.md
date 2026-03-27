# Adverse Selection Threshold Sensitivity — Results

**Date**: 2026-03-27
**Status**: Final

## Setup used

- Symbol: `005930`
- Date: `20260313`
- Strategy spec: `strategies/examples/stateful_cooldown_momentum_v2.0.json`
- Fixed: `resample=500ms`, `market_data_delay_ms=0`
- Sweep: `adverse_selection_threshold_bps ∈ {10, 15, 20, 30}`
- Raw: `outputs/benchmarks/adverse_selection_threshold_sensitivity.json`

## Table 1 — Threshold sweep summary

| threshold_bps | signal_count | parent_order_count | child_order_count | children_per_parent | n_fills | cancel_rate | avg_child_lifetime_s | loop_s | total_s | net_pnl |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 116 | 116 | 1,366 | 11.776 | 128 | 0.9151 | 1.8016 | 5.86 | 40.15 | -3,080,308 |
| 15 | 48 | 48 | 227 | 4.729 | 52 | 0.7885 | 2.9581 | 4.19 | 18.20 | -1,294,679 |
| 20 | 50 | 50 | 175 | 3.500 | 54 | 0.7143 | 3.7171 | 4.16 | 19.09 | -1,344,585 |
| 30 | 50 | 50 | 162 | 3.240 | 54 | 0.6914 | 4.0154 | 4.17 | 18.88 | -1,344,585 |

### Change vs 10bps baseline

- `child_order_count`: 1,366 → 227/175/162 (`-83.4%`, `-87.2%`, `-88.1%`)
- `children_per_parent`: 11.776 → 4.729/3.500/3.240 (`-59.8%`, `-70.3%`, `-72.5%`)
- `avg_child_lifetime_s`: 1.80 → 2.96/3.72/4.02 (longer child survival)
- Runtime (`total_s`) drops materially from 40.15s to ~19s after threshold >= 15

## Table 2 — Cancel reason shares

| threshold_bps | timeout | adverse_selection | stale_price | max_reprices_reached | micro_event_block | unknown |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 0.1904 | 0.8088 | 0.0000 | 0.0000 | 0.0000 | 0.0008 |
| 15 | 0.5307 | 0.4693 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 20 | 0.8160 | 0.1840 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 30 | 0.9375 | 0.0625 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

Interpretation:

- As threshold rises, `adverse_selection` share collapses (`80.9% -> 6.3%`).
- Cancel mix rotates to `timeout` dominance.
- This is consistent with threshold directly controlling adverse-selection cancel trigger frequency.

## Table 3 — Dominant hotspot parent

| threshold_bps | hotspot_parent_children | hotspot_parent_fills | hotspot_parent_cancels | hotspot_share_of_total_children |
|---:|---:|---:|---:|---:|
| 10 | 264 | 1 | 263 | 0.1933 |
| 15 | 43 | 1 | 42 | 0.1894 |
| 20 | 23 | 1 | 22 | 0.1314 |
| 30 | 15 | 1 | 14 | 0.0926 |

Interpretation:

- Hotspot intensity falls sharply as threshold rises.
- Dominant hotspot parent still exists, but with much lower churn volume.
- Dominant hotspot parent gets at least one fill in all runs.

## Answers to required questions

### 1) Does churn decrease as threshold increases?

Yes. Strongly.

- `child_order_count` drops by ~88% from 10bps to 30bps.
- `children_per_parent` drops by ~72.5%.
- `avg_child_lifetime_seconds` increases monotonically.

### 2) Does the dominant hotspot parent get fills?

Yes in this run set.

- Dominant hotspot parent has `n_fills=1` at all thresholds (10/15/20/30).
- The hotspot still churns, but far less as threshold increases.

### 3) Is `500ms,d=0` explosion effectively a threshold-setting issue?

For the current code state, threshold is a first-order driver of churn.

- Raising threshold alone changes both churn magnitude and cancel reason mix dramatically.
- Therefore adverse-selection threshold is sufficient to explain most observed churn sensitivity in this setup.

Note:

- Absolute child counts in this run (`10bps -> 1,366`) are lower than the older historical report (`15,077`), so this should be interpreted as current-state sensitivity evidence, not a byte-for-byte replay of earlier absolute levels.

### 4) Next step: queue audit needed, or threshold explanation sufficient?

Threshold explanation is sufficient for primary causality at this stage.

- A lightweight queue audit is still recommended as a follow-up only to verify no secondary bottleneck remains at the chosen production threshold (e.g., 15 or 20bps), but it is not required to explain the main churn pattern seen here.

## Root-cause interpretation

- At low threshold (10bps), many children are cancelled by adverse-selection quickly.
- Increasing threshold delays or suppresses those cancels, allowing longer child lifetimes and reducing cancel-reslice churn.
- Cancel pressure shifts from adverse-selection to timeout as threshold rises.

This is the expected direction if adverse-selection threshold is the dominant control knob for this churn mode.
