# Backtest Realism Design

## Status

Active — Phase 1 (observation lag) + tick-time semantics alignment implemented

## Purpose

This document captures the next realism-focused changes for the `proj_rl_agent`
backtest engine before implementation.

The goal is not to turn the project into a venue-exact exchange replay engine.
The goal is to improve realism while preserving the current product shape:

- OpenAI strategy generation
- v2 static review
- v2 compiler
- single-symbol backtest
- universe backtest

The proposed work focuses on three areas:

1. observation lag via `observed_state` vs `true_state`
2. queue model interface extraction inside the backtest layer
3. explicit fill-rule ownership between `FillSimulator` and `MatchingEngine`

## Current State

The engine now has a strong research-oriented structure with observation-lag
support fully wired.

- `PipelineRunner` orchestrates signal -> target -> order -> fill -> pnl -> report.
- `observed_state` / `true_state` separation is implemented: strategy decisions
  use delayed observations, fills use the actual current market.
- `FillSimulator` owns queue semantics for passive fills.
- `MatchingEngine` is queue-free and handles pure price/qty/exchange matching.
- `PnLLedger` supports long and short accounting.
- `BacktestConfig` already exposes fee, impact, latency, exchange, and queue settings.

### Supported resample resolutions (current phase)

| Resolution | Purpose |
|------------|---------|
| `1s`       | Default public baseline. Small sub-second lag (< 1000ms) often collapses to same state. |
| `500ms`    | The only realism-oriented resolution in this phase. Moderate lag (>= 200ms) yields distinct `observed_state`. |

Other sub-second values (`100ms`, `250ms`, …) are **not supported** and will
raise `ValueError` at validation time.

### Observation-lag semantics (implemented)

- `observed_state` is a real historical state lookup (`true_state.timestamp - delay_ms`)
- `true_state` is the current market state for fill/exchange-side behavior
- `runtime_v2` lag expressions (`LagExpr`, `RollingExpr`, `PersistExpr`) stack
  on top of observation lag:
  `effective_lookback = observation_delay + strategy_lag_steps × resample_interval`
- Result metadata exposes `configured_market_data_delay_ms`,
  `resample_interval`, `canonical_tick_interval_ms`, and `avg_observation_staleness_ms`

### Tick-time semantics alignment (implemented)

- Canonical tick interval = resample step duration (1000ms for 1s, 500ms for 500ms)
- `cancel_after_ticks` is now computed as `ticks × canonical_tick_interval_ms / 1000`
- Previously, `tick_interval_ms` was incorrectly sourced from `config.latency_ms`
- `latency_ms` (order-to-ack delay) and tick interval (data cadence) are fully decoupled
- Cross-resolution comparison: tick-based params are NOT auto-normalized — this is
  benchmark/experiment responsibility
- See `docs/analysis/tick_time_semantics_alignment.md` for full design

## Design Goals

### Primary goals

- Keep the current state-based research backtester.
- Improve realism without rewriting the engine into a full event-driven exchange simulator.
- Preserve current public workflows and CLI entry points.
- Keep queue semantics in one place only.
- Introduce observation lag in a way that is conceptually correct and testable.

### Non-goals

- full exchange replay
- L3 / MBO reconstruction
- venue-specific exact FIFO / pro-rata behavior
- live OMS redesign
- strategy language redesign

## Proposed Architecture

## 1. Split `observed_state` and `true_state`

### Why

At present, the strategy sees the same state that the fill engine uses. That
means the backtest has order latency, but weak representation of stale market
observation.

To model time lag properly, strategy decisions must use delayed observations,
while fills must still use the actual current market.

### Proposed semantics

- `true_state`
  - current market state at simulation time
  - used for matching, fills, and exchange-side behavior

- `observed_state`
  - latest state at or before `true_state.timestamp - market_data_delay_ms`
  - used for:
    - signal generation
    - target computation
    - parent order creation
    - child order slicing
    - cancel/replace decisions

### Runner sketch

```python
true_state = state
observed_state = lookup_observed_state(symbol, true_state.timestamp, delay_ms)

signal = strategy.generate_signal(observed_state)
target_delta = compute_target_delta(signal, observed_state)
parent = create_parent_order(signal, target_delta, observed_state)
child_orders = slice_order(parent, observed_state)
fills = fill_simulator.simulate_fills(parent, child_orders, true_state)
```

### Important note

This is only meaningful if state resolution is fine enough.

With `1s` resampling, small sub-second lags will often collapse to the same
observable state. For the current phase, the first and only official
realism-oriented step down from `1s` is `500ms`. No additional sub-second
resample modes are part of this design.

## 2. Extract queue model interfaces

### Why

`FillSimulator` is the correct owner of queue semantics, but model behavior is
still mostly encoded as internal branching. That makes further extensions
harder to test and reason about.

### Proposed structure

Keep queue ownership in layer 7, but define queue models as explicit strategy
objects.

Suggested location:

- `src/evaluation_orchestration/layer7_validation/queue_models/`

Suggested modules:

- `base.py`
- `none.py`
- `price_time.py`
- `risk_adverse.py`
- `prob_queue.py`
- `random_queue.py`
- `pro_rata.py`

### Suggested interface

- `new_order(child, state)`
- `advance_trade(child, qty, state)`
- `advance_depth(child, prev_qty, new_qty, state)`
- `ready_to_match(child, state) -> bool`
- `cap_fill(child, state, filled_qty) -> int`

### Ownership after extraction

- `FillSimulator`
  - chooses queue model
  - orchestrates queue state
  - records fills
  - applies fee / impact / pnl wiring

- queue model implementation
  - queue advancement rules
  - gate behavior
  - optional fill-cap behavior

- `MatchingEngine`
  - remains queue-free

### Model classification

Gate-only:

- `price_time`
- `risk_adverse`
- `prob_queue`
- `random`

Gate + allocation:

- `pro_rata`

## 3. Freeze fill-rule ownership

### Why

The queue cleanup already moved semantics out of `MatchingEngine`, but the
design should be made explicit so future changes do not leak queue logic back
into layer 5.

### Final ownership

`FillSimulator`

- identify passive queue candidates
- initialize queue state
- advance queue on trades / depth changes
- decide whether an order is ready to match
- apply pro-rata caps when needed

`MatchingEngine`

- determine marketable / non-marketable matching behavior
- apply exchange model (`partial_fill`, `no_partial_fill`)
- compute raw fill qty / fill price
- remain independent from queue state

### Documentation impact

The following should remain aligned with this contract:

- `src/evaluation_orchestration/layer7_validation/README.md`
- `src/market_simulation/layer5_simulator/README.md`
- queue tests
- matching-engine tests

## Impact on Other Project Areas

## Compiler

Expected impact: low

Relevant file:

- `src/strategy_block/strategy_compiler/v2/compiler_v2.py`

Reason:

- The compiler consumes a `MarketState` and emits executable strategy logic.
- It does not need to know whether the runner supplied an observed or true state.
- No schema change is required for the initial lag design.

Potential follow-up:

- none required for phase 1

## Runtime evaluator

Expected impact: medium, mostly semantic rather than structural

Relevant file:

- `src/strategy_block/strategy_compiler/v2/runtime_v2.py`

Reason:

- `LagExpr`, `RollingExpr`, and `PersistExpr` already exist inside the strategy language.
- Once observation lag is added outside the strategy, those semantics stack:
  stale observed state + internal feature lag/rolling.

Required action:

- document this interaction clearly
- add tests covering combined semantics

No immediate code redesign is required.

## Reviewer

Expected impact: low to medium

Relevant file:

- `src/strategy_block/strategy_review/v2/reviewer_v2.py`

Reason:

- The reviewer already checks structural latency-related issues such as very
  large lag/rolling windows.
- Observation lag does not require a schema change, so the reviewer will still work.

Possible future enhancement:

- warn when a very short-horizon strategy is paired with large observation lag
- warn when strategy-side lag plus engine-side lag likely makes exits too stale

These are optional follow-ups, not blockers.

## Generation

Expected impact: low

Relevant files:

- `src/strategy_block/strategy_generation/generator.py`
- `src/strategy_block/strategy_generation/v2/prompts/planner_user.md`

Reason:

- OpenAI/template generation can remain unchanged for the first implementation.
- The generation path does not need a new spec field to support observation lag.

Possible future enhancement:

- include `market_data_delay_ms` in prompt context, alongside `latency_ms`
- let the planner generate more conservative execution assumptions when lag is high

Again, this is optional.

## Review + Generation hard gates

Expected impact: low

Reason:

- The current hard gates around `position_attr`, exit semantics, and review
  pass/fail do not depend on observation lag.
- No immediate changes are required.

## Config surface

Expected impact: medium

Relevant files:

- `src/evaluation_orchestration/layer7_validation/backtest_config.py`
- `conf/backtest_base.yaml`

Minimum required config support:

- `market_data_delay_ms`
- existing queue fields

Optional follow-up:

- `decision_compute_ms`

The recommendation is to keep the new surface small.

## Testing impact

Expected impact: high

Backtest realism changes should be carried mainly by tests.

Required new test coverage:

- `delay=0` preserves current behavior
- `observed_state` lookup chooses the latest stale state correctly
- signal generation uses delayed state
- fill simulation still uses current state
- cancel/replace logic uses delayed state
- queue models still behave identically after interface extraction
- `MatchingEngine` remains queue-free

Important semantic tests:

- strategy-side `lag()` plus engine-side observation lag
- `1s` resample with small delay behaves almost like no delay
- tighter resample shows larger lag effect

## Rollout Plan

### Phase 1 (done)

Observation lag in the runner.

- `observed_state` / `true_state` separation implemented in `PipelineRunner`
- `market_data_delay_ms` threaded through the main loop
- actual past-state lookup via `bisect_right` on per-symbol timestamp list
- supported resample resolutions enforced: `1s`, `500ms` only
- `500ms` at moderate lag yields distinct `observed_state`
- observation-lag diagnostics exposed in result metadata
- queue implementation unchanged
- runner + integration tests added

### Phase 2

Extract queue model interfaces.

- refactor `FillSimulator`
- preserve existing queue behavior
- keep `MatchingEngine` unchanged
- expand queue regression tests

### Phase 3

Freeze fill-rule documentation and semantics.

- update layer README files
- align tests with documented ownership
- optionally refine reviewer warnings

## Risks

- `1s` resample may hide the effect of small lag values
- stale-state decisions may change regression baselines substantially
- queue interface extraction could add abstraction without payoff if over-designed

## Mitigations

- start with runner-level observed-state separation only
- keep config additions minimal
- rely on targeted regression tests
- document interaction between observed-state lag and strategy-side lag expressions

## Decision Summary

The recommended direction is:

1. separate `observed_state` from `true_state`
2. keep queue semantics in `FillSimulator`, but extract explicit queue model interfaces
3. formalize fill-rule ownership between layer 7 and layer 5

This path materially improves realism without breaking the current strategy
generation / review / compile / backtest product shape.
