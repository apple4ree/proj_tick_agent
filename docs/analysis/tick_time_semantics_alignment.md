# Tick-Time Semantics Alignment

**Date**: 2026-03-26
**Status**: Implemented

## Problem

Prior to this change, three distinct time concepts were conflated in the
backtest engine:

1. **Resample tick** — the data cadence (1s or 500ms between MarketState snapshots)
2. **Latency** — network/processing delay for order submission and acknowledgement
3. **Execution tick interval** — used to convert `cancel_after_ticks` to wall-clock seconds

The bug: `PipelineRunner._setup_components()` passed `config.latency_ms` as the
`tick_interval_ms` parameter to `CancelReplaceLogic`:

```python
# BEFORE (wrong)
self._cancel_replace = CancelReplaceLogic(
    tick_interval_ms=config.latency_ms,
)
```

This meant `cancel_after_ticks=10` at `latency_ms=1.0` would produce a 0.01s timeout,
while at `latency_ms=100.0` it would produce a 1.0s timeout — making the execution
cancel timer dependent on network latency rather than market data cadence.

## Solution

### Canonical tick interval

Every backtest run has a **canonical tick interval** equal to the resample step
duration of the market state stream:

| resample_freq | canonical tick interval |
|---|---|
| `1s` | 1000.0 ms |
| `500ms` | 500.0 ms |
| `None` (default) | 1000.0 ms |

This is computed by `PipelineRunner._resample_freq_to_ms()` and derived from
`states[0].meta["resample_freq"]` at the start of each `run()` call.

### Injection fix

```python
# AFTER (correct)
self._cancel_replace = CancelReplaceLogic(
    tick_interval_ms=self._canonical_tick_ms,
)
```

### Wall-clock semantics

| param | 1s resample | 500ms resample |
|---|---|---|
| `cancel_after_ticks=10` | 10.0s timeout | 5.0s timeout |
| `cancel_after_ticks=20` | 20.0s timeout | 10.0s timeout |
| `holding_ticks=25` | exits after 25 ticks (25s) | exits after 25 ticks (12.5s) |
| `cooldown_ticks=30` | 30s cooldown | 15s cooldown |
| `LagExpr(steps=3)` | looks back 3s | looks back 1.5s |

## Tick-Based Parameter Categories

### Category 1: Resample step count (tick = one MarketState)

These parameters count MarketState snapshots. Their wall-clock meaning changes
with resample frequency.

- **Strategy / runtime**:
  - `holding_ticks` (exit condition)
  - `LagExpr.steps` (feature lookback)
  - `RollingExpr.window` (rolling aggregation window)
  - `PersistExpr.window` (condition persistence window)
  - `cooldown_ticks` (entry cooldown)

- **Execution**:
  - `cancel_after_ticks` (order cancel timeout)

### Category 2: Milliseconds (absolute wall-clock)

These parameters are in absolute time units and are unaffected by resample frequency.

- **Latency**:
  - `market_data_delay_ms` — observation lag
  - `order_submit_ms` — order submission latency
  - `order_ack_ms` — acknowledgement latency
  - `cancel_ms` — cancel request latency

- **Config**:
  - `latency_ms` — flat-config order-to-ack latency

### Key rule: `tick != latency`

`tick_interval_ms` and `latency_ms` serve completely different purposes:
- `tick_interval_ms` = resample step duration (data cadence)
- `latency_ms` = network/processing delay (order lifecycle)

They must never be used interchangeably.

## Cross-Resolution Comparison

When comparing results between `1s` and `500ms` resample:

**Tick-based params are NOT auto-normalized.** The engine treats tick counts
as raw step counts. If you run the same strategy spec at both resolutions:

- `cooldown_ticks=30` → 30s at 1s, 15s at 500ms
- `holding_ticks=25` → 25s at 1s, 12.5s at 500ms
- `cancel_after_ticks=10` → 10s at 1s, 5s at 500ms

This is intended behavior. The engine does not (and should not) auto-normalize
because normalization depends on experimental intent.

For fair cross-resolution comparison, scale tick params by the resolution ratio:
- 1s → 500ms: multiply tick-based params by 2
- e.g. `cooldown_ticks=30` (1s) → `cooldown_ticks=60` (500ms)

This normalization is the responsibility of the benchmark/experiment protocol,
not the engine. See `docs/analysis/observation_lag_2x2_protocol_results.md`
for an example of normalized cross-resolution comparison.

## Files Changed

- `pipeline_runner.py` — added `_resample_freq_to_ms()`, changed CancelReplaceLogic injection
- `cancel_replace.py` — updated docstring, changed default `tick_interval_ms` to 1000.0
- `tests/test_tick_time_alignment.py` — 16 new tests

## Verification

- All existing tests pass (observation lag, execution hints, stronger integration)
- 16 new tests verify:
  - `_resample_freq_to_ms` correctness
  - cancel_after_ticks wall-clock scaling at 1s and 500ms
  - latency_ms independence from tick interval
  - PipelineRunner injection correctness
  - observation_lag metadata includes canonical_tick_interval_ms
  - strategy tick semantics preservation
