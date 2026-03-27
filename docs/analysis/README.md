# Backtest Realism Analysis

## Status

Draft

## Purpose

This directory holds focused analysis documents for the next realism-oriented
backtest changes in `proj_rl_agent`.

The design source of truth remains:

- `/home/dgu/tick/proj_rl_agent/docs/backtest_realism_design.md`

These analysis notes go one level deeper. Each document exists to narrow a
single implementation area before code changes are made.

## Documents

1. [`observation_lag_analysis.md`](./observation_lag_analysis.md)
   - How `observed_state` / `true_state` should behave in practice
   - Whether finer resample resolution is required for lag to matter

2. [`decision_latency_analysis.md`](./decision_latency_analysis.md)
   - Whether explicit strategy compute / reaction delay should be modeled
   - How to keep it separate from observation lag and order latency

3. [`queue_model_refinement_analysis.md`](./queue_model_refinement_analysis.md)
   - Whether current queue models should remain as-is or be refined
   - Where additional realism is worth the added complexity

4. [`latency_semantics_analysis.md`](./latency_semantics_analysis.md)
   - Canonical latency precedence across observation, decision, and venue lifecycle timing
   - Legacy `latency_ms` alias policy and replace-path exception semantics

5. [`realism_diagnostics_analysis.md`](./realism_diagnostics_analysis.md)
   - What metrics and reports are needed to make realism features observable
   - How to expose lag/queue effects in backtest results

## Recommended Reading Order

1. `observation_lag_analysis.md`
2. `decision_latency_analysis.md`
3. `latency_semantics_analysis.md`
4. `queue_model_refinement_analysis.md`
5. `realism_diagnostics_analysis.md`

This order is intentional:

- observation lag changes the meaning of the state seen by the strategy
- decision latency builds on top of that timing model
- latency semantics then fixes precedence and alias behavior before deeper lifecycle work
- queue refinement should be evaluated only after timing and precedence semantics are stable
- diagnostics should summarize the final semantics rather than guess them

## Status Model

- `Draft` — analysis in progress
- `Accepted` — recommended implementation direction chosen
- `Implemented` — code changes completed and verified
- `Superseded` — replaced by a newer document

## Current Implementation Baseline

As of this draft:

- `PipelineRunner` already separates `observed_state` and `true_state`
- `FillSimulator` is the single owner of queue semantics
- `MatchingEngine` is queue-free
- `market_data_delay_ms` is wired into the runner
- queue models are split into explicit modules under
  `src/evaluation_orchestration/layer7_validation/queue_models/`

What remains is to decide how far to push:

- resample resolution and lag usefulness
- explicit decision latency
- queue model refinement depth
- realism diagnostics and reporting

## Expected Outcome

After these documents are completed, the project should be able to:

1. prioritize implementation work with less ambiguity
2. avoid mixing realism features that overlap semantically
3. preserve the current product shape while improving backtest realism
