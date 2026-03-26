# Decision Latency Analysis

## Status

Accepted

## Purpose

This document makes the decision-latency design concrete enough for
implementation planning.

The engine already models:

- observation lag (`market_data_delay_ms`)
- order / exchange latency (`order_submit_ms`, `order_ack_ms`, `cancel_ms`)

What remains missing is explicit strategy reaction time.

## Scope

This document covers:

- whether `decision_compute_ms` should exist
- where it belongs in the timing chain
- which lifecycle timestamps should move
- how to avoid double-counting with observation lag and order latency

This document does **not** cover:

- state resolution policy
- queue-model refinement
- report/diagnostic schema

## Background

Relevant files:

- `/home/dgu/tick/proj_rl_agent/src/evaluation_orchestration/layer7_validation/pipeline_runner.py`
- `/home/dgu/tick/proj_rl_agent/src/evaluation_orchestration/layer7_validation/backtest_config.py`
- `/home/dgu/tick/proj_rl_agent/src/market_simulation/layer5_simulator/latency_model.py`
- `/home/dgu/tick/proj_rl_agent/src/execution_planning/layer4_execution/cancel_replace.py`

## Current State

### Already modeled

- stale market observation through `observed_state`
- order submission / acknowledgement / cancel transport delay
- exchange-side fill timing through `FillSimulator` + `MatchingEngine`

### Not explicitly modeled

- strategy compute time between seeing a state and acting on it
- separate reaction timing for cancel/replace
- a clear timestamp chain from observation to order arrival at venue

## Problem Statement

Observation lag and decision latency are different.

Two strategies may see the same delayed market state, but one can still react
faster than the other. Without explicit decision latency, the engine risks
understating:

- short-horizon opportunity decay
- stale cancel/replace behavior
- slow strategy reaction that is not explained by data staleness

## Working Definitions

- **Observation lag**
  - when the strategy sees the market
- **Decision latency**
  - how long the strategy takes to turn that observation into an order intent
- **Order submission latency**
  - how long the venue takes to receive the already-formed order

These must remain separate in code and in docs.

## Timing Chain

Let:

- `t_true` = current runner step timestamp (`true_state.timestamp`)
- `t_obs_state` = `observed_state.timestamp`
- `t_decision` = the time at which the strategy acts on what it sees
- `t_submit_ready` = time the order intent is ready to leave the strategy layer
- `t_venue_arrival` = time the order reaches the simulated venue

### Working interpretation

At runner step `t_true`:

- the strategy *observes* `observed_state`
- that observation is available to it at **current wall-clock simulation time** `t_true`
- the strategy does **not** act at `t_obs_state`; that timestamp belongs to the historical state snapshot

Therefore the first implementation should define:

- `t_decision = t_true`
- `t_submit_ready = t_true + decision_compute_ms`
- `t_venue_arrival = t_submit_ready + order_submit_ms`

This preserves the distinction:

- stale information is modeled by `observed_state`
- slow reaction is modeled by `decision_compute_ms`

## Lifecycle Rules

### New parent order

1. signal is generated from `observed_state`
2. target delta is computed from `observed_state`
3. parent order intent is timestamped at `t_decision = t_true`
4. parent becomes submit-ready at `t_submit_ready`

### Child order creation

- child orders produced from the same decision inherit the same `t_submit_ready`
- child `submitted_time` should reflect strategy decision latency, not only venue latency

### Replace order

- a replace action is a fresh decision
- it gets a fresh `t_decision = current true runner step`
- it gets a fresh `t_submit_ready = t_decision + decision_compute_ms`
- it should **not** inherit the original child's strategy timing wholesale

### Cancel action

First implementation should reuse the same `decision_compute_ms` rule:

- cancel decision happens at current runner step
- cancel request becomes submit-ready after `decision_compute_ms`
- transport `cancel_ms` then applies after that

No separate `cancel_decision_delay_ms` is recommended in the first phase.

## Options Considered

### Option A: no explicit decision latency

Pros:

- simplest model
- fewer settings

Cons:

- conflates stale observation with slow reaction
- weaker fit for short-horizon strategies

### Option B: single `decision_compute_ms`

Pros:

- minimal extension
- easiest to explain
- captures the largest missing timing gap

Cons:

- cancel/replace uses the same reaction speed as entry decisions

### Option C: separate `decision_compute_ms` and `cancel_decision_delay_ms`

Pros:

- more realistic for passive execution management

Cons:

- more settings
- more interpretation overhead
- more regression surface

## Recommended Approach

Recommend **Option B** for the next phase.

### Working decision

- add `decision_compute_ms`
- use it for new orders, replacements, and cancels in the first implementation
- do **not** add `cancel_decision_delay_ms` yet

## Concrete Implementation Guidance

### Config

Add:

- `decision_compute_ms: float = 0.0`

Do not add more timing knobs yet.

### Runner

The runner should treat decision latency as a strategy-layer delay before order
submission begins.

The cleanest practical rule is:

- strategy logic runs on `observed_state` at runner step `t_true`
- any new or modified order gets an effective strategy timestamp of
  `t_true + decision_compute_ms`
- venue latency starts after that point

### Order timestamps

The following timestamps should become explicit or at least be interpreted
explicitly in code/comments/tests:

- `decision_time`
- `submitted_time` / `effective_submit_time`
- venue-side arrival timing after submission latency

If the project chooses not to add new fields, it should still define which
existing timestamps represent each phase.

## Double-Count Rules

To avoid semantic overlap:

- `market_data_delay_ms` changes **which state** is seen
- `decision_compute_ms` changes **when action becomes ready**
- `order_submit_ms` changes **when the venue receives the action**
- `order_ack_ms` / `cancel_ms` change **venue-side acknowledgement timing**

No one field should stand in for more than one of these roles.

## Tests Required

1. `decision_compute_ms=0` preserves current behavior
2. positive `decision_compute_ms` delays new order eligibility
3. replacement orders receive fresh decision timing
4. cancel actions also pay the same decision delay in phase 1
5. decision latency does not double-count with order submission latency

## Risks

- time-axis semantics become harder to reason about
- broad integration tests may need timestamp expectation updates
- users may confuse decision latency with observation lag

## Mitigations

- add one new field only
- document the timing chain explicitly
- keep default at `0.0`
- add focused tests before adjusting broad integration baselines

## Open Questions

None for the current phase.

Deferred follow-ups:

1. Whether `decision_time` should eventually become a first-class dataclass field
   instead of a metadata value.
2. Whether reviewer/universe tooling should later warn on very large decision
   latency relative to short-horizon strategies.
3. Whether cancel decisions should eventually get their own dedicated latency field.

## Decision

### Accepted

- add a single `decision_compute_ms`
- apply it to new, replace, and cancel decisions alike in the first phase
- define timing as:
  - observe delayed state at current runner time
  - pay decision latency
  - then pay venue submission latency
- record `decision_time` and effective submit timing in order metadata in the
  first implementation rather than expanding order dataclasses immediately
- do not add a separate `cancel_decision_delay_ms` in this phase
