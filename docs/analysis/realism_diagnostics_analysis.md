# Realism Diagnostics Analysis

## Status

Accepted

## Purpose

This document makes the realism-diagnostics design concrete enough for
implementation planning.

The engine already supports realism-oriented behavior, but the current outputs
mostly describe trading outcomes rather than whether realism mechanisms were
actually active.

## Scope

This document covers:

- which realism metrics should be reported
- how those metrics should be computed
- where they should be written
- how single-symbol and universe outputs should differ

This document does **not** cover:

- event-level tracing of every queue transition
- specialized plots in the first iteration
- public CLI redesign

## Background

Relevant files:

- `/home/dgu/tick/proj_rl_agent/src/evaluation_orchestration/layer7_validation/report_builder.py`
- `/home/dgu/tick/proj_rl_agent/src/evaluation_orchestration/layer7_validation/pipeline_runner.py`
- `/home/dgu/tick/proj_rl_agent/src/evaluation_orchestration/layer7_validation/fill_simulator.py`

## Problem Statement

Without realism diagnostics, the engine cannot answer questions such as:

- was configured observation lag materially realized?
- were passive fills reduced by queue blocking or by signal scarcity?
- did resolution collapse make a delay setting effectively meaningless?
- how much passive waiting happened before fills or misses?

That makes realism features harder to validate and harder to compare.

## Working Output Decision

### Summary output

Add a small realism slice to `summary.json`.

### Dedicated artifact

Add **one** dedicated run-level artifact:

- `realism_diagnostics.json`

### Do not add yet

- realism-specific plots
- per-order trace dumps by default
- CSV as the primary realism artifact

JSON is preferred first because:

- the data is mostly structured aggregates
- universe runs may need nested per-symbol summaries
- schema evolution is easier than with a rigid CSV first

## Summary Fields

The following fields should be added to `summary.json`:

- `market_data_delay_ms`
- `queue_model`
- `resample_interval`
- `avg_observation_staleness_ms`
- `max_observation_staleness_ms`
- `effective_delay_resolution_ratio`
- `queue_gate_block_count`
- `queue_gate_pass_count`
- `avg_queue_wait_ticks`
- `queue_blocked_fill_miss_count`

Fields should be nullable where they do not apply.

## Metric Definitions

### Observation metrics

#### `avg_observation_staleness_ms`

Mean of:

`(true_state.timestamp - observed_state.timestamp)`

across **decision-evaluated actionable steps**.

A step is included when:

- the state is actionable for trading
- the runner evaluates the strategy/order-decision path

#### `max_observation_staleness_ms`

Maximum of the same quantity over the same step set.

#### `stale_decision_ratio`

Defined for the dedicated diagnostics artifact, not necessarily summary:

`stale_decision_steps / decision_evaluated_steps`

where a stale decision step is one with:

`observed_state.timestamp != true_state.timestamp`

#### `effective_delay_resolution_ratio`

Defined as:

`avg_observation_staleness_ms / market_data_delay_ms`

when `market_data_delay_ms > 0`, else `null`.

Interpretation:

- near `1.0`: configured delay is being represented fairly closely
- much greater than `1.0`: coarse resolution is amplifying effective staleness
- much smaller than `1.0`: configured delay is frequently collapsing under resolution limits

### Queue metrics

#### `queue_gate_block_count`

Increment by `1` each time a passive queue candidate is blocked on a runner step
because `queue_ahead_qty > 0` and matching is not yet allowed.

This is a **per-child per-step** count, not a unique-order count.

#### `queue_gate_pass_count`

Increment by `1` once per child when it first transitions from blocked to ready.

This is a **per-child transition** count.

#### `avg_initial_queue_ahead_qty`

Mean of initial `queue_ahead_qty` across passive children that initialize queue
state.

#### `avg_queue_wait_ticks`

Mean number of runner ticks from:

- `queue_enter_ts`

to:

- first `ready_to_match == True`

across passive children that actually reach ready state.

Children that never reach ready state are excluded from this average and counted
instead by `queue_blocked_fill_miss_count`.

#### `avg_queue_wait_ms`

Mean wall-clock difference in milliseconds from:

- `queue_enter_ts`

to:

- first ready-to-match timestamp

across the same denominator as `avg_queue_wait_ticks`.

#### `queue_blocked_fill_miss_count`

Count passive children that:

- initialized queue state
- never reached ready-to-match
- and terminated by cancel / expiry / parent completion before becoming ready

## Dedicated Artifact Schema

File:

- `realism_diagnostics.json`

Working schema:

```json
{
  "config": {
    "market_data_delay_ms": 0.0,
    "queue_model": "risk_adverse",
    "resample_interval": "1s"
  },
  "aggregate": {
    "decision_evaluated_steps": 0,
    "stale_decision_steps": 0,
    "avg_observation_staleness_ms": 0.0,
    "max_observation_staleness_ms": 0.0,
    "effective_delay_resolution_ratio": null,
    "queue_gate_block_count": 0,
    "queue_gate_pass_count": 0,
    "avg_initial_queue_ahead_qty": null,
    "avg_queue_wait_ticks": null,
    "avg_queue_wait_ms": null,
    "queue_blocked_fill_miss_count": 0
  },
  "symbols": {
    "005930": {
      "decision_evaluated_steps": 0,
      "stale_decision_steps": 0,
      "avg_observation_staleness_ms": 0.0,
      "queue_gate_block_count": 0,
      "queue_gate_pass_count": 0,
      "avg_queue_wait_ticks": null,
      "queue_blocked_fill_miss_count": 0
    }
  }
}
```

## Single-Symbol vs Universe Policy

### Single-symbol

- always write aggregate metrics
- `symbols` section may be omitted or mirror aggregate

### Universe

- always write aggregate metrics
- include per-symbol metrics under `symbols`
- do not emit per-order diagnostics by default

## Concrete Implementation Guidance

### Runner / Fill Simulator responsibilities

`PipelineRunner` should collect:

- step-level observation staleness
- decision-evaluated step counts

`FillSimulator` should expose:

- queue gate blocked counts
- gate-pass transition counts
- initial queue-ahead values
- queue wait timing
- blocked-miss counts

### Report builder responsibilities

`ReportBuilder` should:

- merge realism counters into `summary.json`
- emit `realism_diagnostics.json`
- preserve backward-compatible existing artifacts

## Tests Required

1. `summary.json` contains the new realism fields
2. `realism_diagnostics.json` is written and structurally valid
3. queue-on vs queue-off changes queue metrics materially
4. lag-on vs lag-off changes observation metrics materially
5. single-symbol and universe outputs follow the same schema contract

## Risks

- summary clutter
- misleadingly precise metrics under coarse resolution
- oversized universe diagnostics if schema grows too much

## Mitigations

- keep summary slice compact
- keep rich detail in one dedicated JSON artifact
- report both configured delay and realized staleness
- avoid per-order diagnostics until a concrete need appears

## Open Questions

None for the current phase.

Deferred follow-ups:

1. Whether realism diagnostics should later become partially config-gated if
   output size or runtime overhead becomes material.
2. Whether `stale_decision_ratio` should eventually be promoted into
   `summary.json` after practical usefulness is proven.

## Decision

### Accepted

- add a compact realism slice to `summary.json`
- add `realism_diagnostics.json` as the dedicated artifact
- define metrics using the formulas in this document
- keep diagnostics always-on in the first implementation
- keep `stale_decision_ratio` in `realism_diagnostics.json`, not in
  `summary.json`
- keep queue wait in both ticks and milliseconds in the JSON artifact, but only
  expose ticks in the compact summary slice
- defer realism-specific plots and per-order tracing
