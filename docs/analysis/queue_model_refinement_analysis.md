# Queue Model Refinement Analysis

## Status

Accepted

## Purpose

This document decides how far queue-model refinement should go in the current
state-based backtest engine.

The queue system is already implemented and structurally sound. The question now
is not whether queue semantics belong in the engine, but how much more model
complexity is justified.

## Scope

This document covers:

- whether current queue models are sufficient
- whether any current models should be treated as aliases
- which refinements are worth doing next
- which models should be considered baseline vs advanced

This document does **not** cover:

- full venue-exact queue replay
- L3 / MBO reconstruction
- queue logic inside `MatchingEngine`

## Background

Relevant files:

- `/home/dgu/tick/proj_rl_agent/src/evaluation_orchestration/layer7_validation/fill_simulator.py`
- `/home/dgu/tick/proj_rl_agent/src/evaluation_orchestration/layer7_validation/queue_models/`
- `/home/dgu/tick/proj_rl_agent/src/market_simulation/layer5_simulator/matching_engine.py`
- `/home/dgu/tick/proj_rl_agent/tests/test_queue_position.py`
- `/home/dgu/tick/proj_rl_agent/tests/test_matching_engine.py`

Current queue models:

- `none`
- `price_time`
- `risk_adverse`
- `prob_queue`
- `random`
- `pro_rata`

## Current State

### Strengths

- queue ownership is clean: `FillSimulator` only
- passive fill over-optimism is reduced
- model implementations are explicit modules
- `MatchingEngine` remains queue-free
- seeded randomness is already supported

### Current limitations

- L2 approximation only
- `price_time` and `risk_adverse` are behaviorally identical today
- `prob_queue` uses a single hard-coded depth-credit rule
- `random` uses only a uniform distribution
- `pro_rata` is an approximation, not a venue-specific matching engine

## Problem Statement

The current model surface is useful, but it is slightly misleading unless the
project is explicit about which models are:

- true alternatives
- current aliases
- advanced sensitivity tools
- deferred future work

Without that, model names can imply more realism than the engine is actually
trying to provide.

## Working Decisions by Model

### `none`

Decision:

- keep unchanged
- treat as the baseline control and regression reference

### `price_time`

Decision:

- keep the config value
- treat it as a **documented alias** of the current conservative FIFO
  approximation
- in the current architecture, its semantics are intentionally the same as
  `risk_adverse`

Rationale:

- users understand `price_time` quickly
- the name is useful even if the present implementation is only an L2
  conservative approximation

### `risk_adverse`

Decision:

- keep as the **canonical conservative baseline**
- continue to use it as the default realism-oriented queue baseline

Rationale:

- it is already the clearest baseline for passive-fill pessimism
- no extra differentiation is justified yet

### `prob_queue`

Decision:

- keep as the primary refinement target
- if one queue model is extended next, it should be this one

Refinement direction:

- allow internal variation in depth-drop crediting
- do **not** add new public queue model names yet
- start with a pluggable internal `depth_credit_mode`, defaulting to the current
  linear rule

### `random`

Decision:

- keep, but treat as advanced / sensitivity-analysis-only
- do not treat it as a default benchmark model in public-facing comparisons

Refinement direction:

- only consider alternate distributions after `prob_queue` is settled
- keep uniform as the initial default stochastic behavior

### `pro_rata`

Decision:

- keep as-is
- do not expand toward venue-specific pro-rata matching in the current project

Rationale:

- the current implementation is enough for approximate size-sharing studies
- deeper pro-rata realism would outgrow the rest of the engine's realism level

## Baseline vs Advanced Usage Policy

### Baseline comparison set

Use these for default research comparisons:

- `none`
- `risk_adverse`
- `prob_queue`

### Advanced / specialized set

Use these only when there is a specific reason:

- `price_time`
- `random`
- `pro_rata`

This does not remove them from config. It clarifies expected usage.

## Options Considered

### Option A: keep everything as-is

Pros:

- no extra work
- already functional

Cons:

- model names stay slightly misleading
- no clear prioritization for future refinement

### Option B: minor refinement only

Scope:

- document alias/role decisions clearly
- refine `prob_queue` first if refinement is needed
- keep advanced models available but non-default

Pros:

- best value per added complexity
- aligned with the rest of the engine's realism level

Cons:

- still approximate by design

### Option C: aggressive expansion

Scope:

- more queue variants
- more public knobs
- more venue-like semantics

Pros:

- richer experimentation

Cons:

- likely to exceed the realism budget of the rest of the state-based engine
- higher maintenance burden

## Recommended Approach

Recommend **Option B**.

## Concrete Implementation Guidance

### Immediate documentation / testing changes

- explicitly document `price_time` == `risk_adverse` in current behavior
- keep regression tests that guarantee equivalence
- document `risk_adverse` as the canonical conservative baseline
- document `random` and `pro_rata` as advanced models

### Next refinement target

If queue work continues, do it in this order:

1. internal `prob_queue` depth-credit function abstraction
2. optional alternate distribution for `random`
3. no further work on `pro_rata` unless a concrete venue need appears

### Not recommended now

- new public queue model names
- moving queue logic back into `MatchingEngine`
- venue-specific exact FIFO or pro-rata
- queue work that depends on L3/MBO data not present in the engine

## Tests Required

1. preserve existing queue regressions
2. preserve `MatchingEngine` queue-free regressions
3. explicitly test `price_time` and `risk_adverse` equivalence
4. if `prob_queue` gets internal depth-credit modes, test each mode independently

## Risks

- users may assume more realism from model names than actually exists
- too many public queue knobs can reduce interpretability
- queue refinement can outpace the realism of observation timing and data resolution

## Mitigations

- keep the public model surface stable
- clarify baseline vs advanced usage
- prioritize timing and diagnostics work before deeper queue sophistication

## Open Questions

None for the current phase.

Deferred follow-ups:

1. Whether internal `prob_queue` refinement should remain private until
   realism diagnostics are fully in place.
2. Whether `random` should be omitted from the most basic user docs while
   remaining supported in advanced documentation.

## Decision

### Accepted

- keep the current six queue models
- treat `price_time` as a documented alias of the current `risk_adverse`
  semantics in both basic and advanced queue documentation
- treat `risk_adverse` as the canonical conservative baseline
- prioritize `prob_queue` if any queue refinement is done next
- keep `random` and `pro_rata` as advanced, non-default models
- do not add new public queue-model names in the next refinement phase
