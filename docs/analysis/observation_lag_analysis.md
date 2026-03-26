# Observation Lag Analysis

## Status

Accepted

## Purpose

This document makes the observation-lag design concrete enough to guide
implementation work.

The engine already separates `observed_state` and `true_state`. The remaining
question is not whether lag exists in principle, but whether it is meaningful in
practice under the current state resolution and workload mix.

## Scope

This document covers:

- how observation lag should be interpreted
- which resample resolution should be treated as the first realism-oriented option
- how single-symbol and universe backtests should handle that option
- how external observation lag interacts with strategy-level lag operators

This document does **not** cover:

- explicit decision latency
- queue-model refinement
- venue-exact replay

## Background

Relevant files:

- `/home/dgu/tick/proj_rl_agent/src/evaluation_orchestration/layer7_validation/pipeline_runner.py`
- `/home/dgu/tick/proj_rl_agent/src/evaluation_orchestration/layer7_validation/backtest_config.py`
- `/home/dgu/tick/proj_rl_agent/conf/backtest_base.yaml`
- `/home/dgu/tick/proj_rl_agent/src/data/layer0_data/state_builder.py`
- `/home/dgu/tick/proj_rl_agent/src/strategy_block/strategy_compiler/v2/runtime_v2.py`

Current semantics:

- `true_state`
  - current market state used for fills and exchange-side behavior
- `observed_state`
  - latest historical state at or before `true_state.timestamp - market_data_delay_ms`
  - used for signal generation, target computation, and order decisions

## Current State

### Already implemented

- per-symbol state history is accumulated in `PipelineRunner`
- `market_data_delay_ms` is wired into the runner
- `_lookup_observed_state(...)` performs actual past-state lookup
- strategy decisions use `observed_state`
- fills use `true_state`

### Still unresolved

- the default public resample is still `1s`
- small lag values may collapse under coarse resolution
- no official single realism-oriented resolution policy exists yet
- no explicit performance budget exists for the first finer-resolution rollout

## Problem Statement

Observation lag is now conceptually correct, but its experimental value depends
on the time resolution of the state stream.

At `1s` resolution, small sub-second delays can map to the same `observed_state`
for many steps. In that regime, lag is still a valid model, but it is not a
strong knob for experimentation.

This matters most for:

- short-horizon strategies
- queue-sensitive strategies
- passive entry / fast-exit strategies
- any study that claims sensitivity to sub-second delay

## Working Definitions

- **Configured observation lag**
  - `market_data_delay_ms` from config
- **Actual observation staleness**
  - `true_state.timestamp - observed_state.timestamp`
- **Resolution collapse**
  - the case where configured lag changes but actual observed states often do not

These must remain distinct in reporting and discussion.

## Runtime Interaction

The runtime evaluator already supports `LagExpr`, `RollingExpr`, and
`PersistExpr`.

Working interpretation:

- `PipelineRunner` applies observation lag first by selecting `observed_state`
- the runtime evaluator then applies strategy-defined lag/rolling/persist on top
  of the delayed features

Effective lookback therefore stacks:

`effective_lookback ~= actual_observation_staleness + strategy_lag_steps * resample_interval`

This is the intended semantics and should be preserved.

## Resolution Policy Options

### Option A: keep `1s` only

Pros:

- simplest
- cheapest
- preserves current speed profile

Cons:

- observation lag remains weak for sub-second studies
- easy to over-interpret `market_data_delay_ms`

### Option B: support one additional realism-oriented resolution

Candidate chosen for analysis:

- `500ms`

Pros:

- materially better than `1s` for lag experiments
- much lower option surface than supporting multiple sub-second values
- more likely to remain practical for universe runs

Cons:

- still coarse for highly latency-sensitive strategies
- does not attempt to cover very fine-grained sub-second studies

## Recommended Approach

Recommend **Option B**.

### Working decision: supported resolutions

| Workflow | Default | Supported realism-oriented resolution |
|---|---:|---:|
| Single-symbol | `1s` | `500ms` |
| Universe | `1s` | `500ms` |

### Working decision: interpretation

- `1s` remains the public baseline for routine strategy iteration
- `500ms` is the only official realism-oriented resample resolution in the current phase
- no additional sub-second public modes are part of the current design

## Working Performance Budget

These are design targets, not claims about the current implementation.

### Single-symbol

- `500ms`: target <= `2x` wall-clock cost versus `1s`

### Universe

- `500ms`: target <= `2.5x` wall-clock cost versus `1s`

If these budgets cannot be met, `500ms` should remain experimental until the
engine is optimized enough to support it credibly.

## Concrete Implementation Guidance

### State builder

`state_builder.py` should explicitly support, for the current phase:

- `1s`
- `500ms`

Other sub-second values should not be exposed as supported realism modes.

### Single-symbol CLI

`backtest.py` should allow `1s` and `500ms` as the supported public options for
this phase.

### Universe CLI

`backtest_strategy_universe.py` should allow `1s` and `500ms` only for this
phase.

### Reporting

Every run using observation lag should report:

- configured `market_data_delay_ms`
- actual average observation staleness
- resample interval used

This prevents confusion between configured delay and realized stale view.

## Tests Required

1. `market_data_delay_ms=0` preserves existing behavior
2. `1s` resolution + small lag often resolves to the same state
3. `500ms` resolution + moderate lag yields a materially different `observed_state`
4. `runtime_v2` lag semantics remain stacked and documented
5. universe backtests do not silently ignore `500ms`

## Risks

- performance degradation in universe runs
- larger state histories and memory use
- users may still assume `500ms` is enough for all short-horizon use cases

## Mitigations

- keep `1s` as default
- expose only one realism-oriented resolution in this phase
- pair this work with bounded history retention
- include resolution metadata in reports and summaries

## Open Questions

None for the current phase.

## Decision

### Accepted

- keep `1s` as the default public baseline
- adopt `500ms` as the only official realism-oriented resample resolution in the current phase
- do not introduce any additional sub-second public modes in the current phase
- do not represent sub-second lag experiments as meaningful unless they are backed by a supported resolution
