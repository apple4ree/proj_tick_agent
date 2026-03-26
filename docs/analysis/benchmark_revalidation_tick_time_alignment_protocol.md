# Benchmark Revalidation: Tick-Time Semantics Alignment

**Date**: 2026-03-26
**Status**: In Progress
**Prerequisite**: tick-time semantics alignment (canonical tick = resample step)

## Purpose

Re-run the 2x2 observation-lag vs cadence decomposition benchmark after the
tick-time semantics alignment fix to verify that conclusions still hold under
corrected cancel-timer behavior.

### What changed

`CancelReplaceLogic.tick_interval_ms` was sourced from `config.latency_ms`
(dataclass default 1.0) instead of the canonical resample step duration.

| parameter | before fix | after fix (1s) | after fix (500ms) |
|---|---|---|---|
| `tick_interval_ms` | 1.0 (= `latency_ms`) | 1000.0 | 500.0 |
| `cancel_after_ticks=10` timeout | **0.01s** | **10.0s** | **5.0s** |
| `cancel_after_ticks=4` timeout | 0.004s | 4.0s | 2.0s |

This is a ~1000× change in cancel timeout for 1s resample. Orders that were
effectively cancelled every tick now survive for 10 seconds, fundamentally
altering order lifecycle, fill counts, and cancel rates.

### Expected behavioral changes

1. **Dramatically fewer child orders** — orders are no longer cancelled/resubmitted
   every tick at delay=0
2. **Much faster delay=0 runs** — the pathological cancel/resubmit churn that
   caused 700–930s loop times should disappear
3. **Lower cancel rates** — orders stay active longer before cancellation
4. **Different signal/fill dynamics** — may change the cadence vs lag decomposition

## Experimental Setup

### Common parameters

| Parameter | Value |
|---|---|
| Symbol (single) | 005930 (Samsung Electronics) |
| Date | 20260313 |
| Strategy | `stateful_cooldown_momentum` v2.0 |
| Universe | up to 5 symbols (capped), 180s/symbol timeout |
| `placement_style` | `aggressive` |
| `compute_attribution` | `False` |
| `seed` | 42 |
| `latency_ms` | 1.0 (BacktestConfig default) |
| Data source | KIS H0STASP0 L2 tick data |

### Strategy tick parameters

| parameter | original | normalized (2×) | wall-clock at 1s | wall-clock at 500ms (orig) | wall-clock at 500ms (norm) |
|---|---|---|---|---|---|
| `cooldown_ticks` | 30 | 60 | 30s | **15s** | 30s |
| `holding_ticks` (exit) | 25 | 50 | 25s | **12.5s** | 25s |
| `cancel_after_ticks` | 10 | 20 | **10.0s** | **5.0s** | **10.0s** |
| `cancel_after_ticks` (adaptation) | 4 | 8 | **4.0s** | **2.0s** | **4.0s** |

**Bold** values show where the fix produces different behavior from pre-alignment.

### Experiment matrix

#### Phase 1: Single-symbol raw 2x2

| run_id | resample | delay_ms | strategy |
|--------|----------|----------|----------|
| A | 1s | 0 | original |
| B | 1s | 200 | original |
| C | 500ms | 0 | original |
| D | 500ms | 200 | original |

#### Phase 2: Single-symbol normalized 2x2

| run_id | resample | delay_ms | strategy |
|--------|----------|----------|----------|
| A | 1s | 0 | original (reused from Phase 1) |
| B | 1s | 200 | original (reused from Phase 1) |
| C_n | 500ms | 0 | normalized 2× |
| D_n | 500ms | 200 | normalized 2× |

#### Phase 3: Universe raw 2x2

| run_id | resample | delay_ms | strategy |
|--------|----------|----------|----------|
| A | 1s | 0 | original |
| B | 1s | 200 | original |
| C | 500ms | 0 | original |
| D | 500ms | 200 | original |

### Effect decomposition formulas

- **Cadence effect**: A → C (or A → C_n for normalized)
- **Lag at 1s**: A → B
- **Lag at 500ms**: C → D (or C_n → D_n for normalized)
- **Identifiability gain**: (D − C) − (B − A) [or (D_n − C_n) − (B − A)]

### Metrics collected

- `signal_count`, `parent_order_count`, `child_order_count`
- `n_fills`, `cancel_rate`, `fill_rate`
- `avg_holding_period_steps`, `avg_holding_seconds`
- `net_pnl`, `total_realized_pnl`, `total_commission`, `total_slippage`, `total_impact`
- `avg_observation_staleness_ms`
- `wall_clock_s`, `loop_s`, `total_pipeline_s`
- `peak_rss_mb`

## Questions to Answer

1. **Does 500ms remain valid as the realism-oriented resolution?**
2. **How did the cadence confound proportion change?** (was ~70% pre-alignment)
3. **Are delay=0 runs now fast enough for universe benchmarks?** (were 4/5 and 5/5 timeouts)
4. **Did the cancel-timer fix change the qualitative decomposition conclusions?**
5. **What is the new signal/fill/PnL profile compared to pre-alignment?**

## Output files

1. Protocol: `docs/analysis/benchmark_revalidation_tick_time_alignment_protocol.md` (this file)
2. Progress: `docs/analysis/benchmark_revalidation_tick_time_alignment_progress.md`
3. Results: `docs/analysis/benchmark_revalidation_tick_time_alignment_results.md`
4. Raw JSON: `outputs/benchmarks/benchmark_revalidation_tick_time_alignment.json`

## Comparison baseline

Pre-alignment results: `outputs/benchmarks/observation_lag_2x2.json`
Pre-alignment report: `docs/analysis/observation_lag_2x2_protocol_results.md`
