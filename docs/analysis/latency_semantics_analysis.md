# Latency Semantics Analysis

## Status

Implemented

## Purpose

This document fixes latency precedence semantics to a final-freeze-pre level.
The goal is to remove ambiguity across single-symbol and universe backtests
without redesigning the exchange simulator.

Scope in this note:
- precedence and source-of-truth rules
- legacy `latency_ms` alias policy
- minimal replace exception semantics
- diagnostics interpretation

Out of scope:
- staged replace state machine
- queue model redesign
- matching/fill engine redesign

## Canonical Source-of-Truth Table

| Semantic area | Canonical field(s) | Used for | Not used for |
|---|---|---|---|
| Observation lag | `market_data_delay_ms` (top-level) | delayed historical lookup on decision path | venue submit/cancel/ack lifecycle |
| Decision compute delay | `decision_compute_ms` (top-level) | additional decision-path lookup delay | venue submit/cancel/ack lifecycle |
| Venue submit latency | `latency.order_submit_ms` | child venue-arrival gating | observation lag derivation |
| Venue cancel latency | `latency.cancel_ms` | cancel-effective gating | observation lag derivation |
| Venue ack latency | `latency.order_ack_ms` | reporting/status aggregate | fill gating (current phase) |

Additional constraint:
- `latency.market_data_delay_ms` is compatibility-only and is not a canonical source
  for strategy observation lag.

## Final Precedence Rules

1. `market_data_delay_ms` is the only observation-lag source.
2. `decision_compute_ms` is the only strategy compute-delay source.
3. Nested `latency.order_submit_ms/order_ack_ms/cancel_ms` are the only venue-latency source.
4. Flat `latency_ms` is a legacy shorthand only when nested `latency` is absent (`latency is None`).
5. If nested `latency` exists (profile-only, partial, or full), flat alias is fully disabled.

This removes the old partial-backfill behavior where missing nested fields could
still be filled from flat `latency_ms`.

## Legacy `latency_ms` Alias Policy

Alias mapping is fixed to:
- `order_submit_ms = latency_ms * 0.3`
- `order_ack_ms = latency_ms * 0.7`
- `cancel_ms = latency_ms * 0.2`

Alias applies only when `latency is None`.

Alias never derives:
- `market_data_delay_ms`
- `decision_compute_ms`

Single-symbol and universe entrypoints follow the same rule.

## Separation of Timing Domains

Timing domains are intentionally separated:
- Observation domain: what state the strategy sees (`market_data_delay_ms`).
- Decision domain: when action can be formed after observation (`decision_compute_ms`).
- Venue lifecycle domain: when orders/cancels become effective at venue (`latency.*`).

This avoids double-counting and preserves `tick != latency` semantics.

## Replace Path Exception (Intentional Minimal Model)

Current replace semantics are intentionally minimal:
- old child is immediately cancelled on replace decision
- replacement child is created as a new lifecycle
- replacement child receives fresh submit/arrival latency metadata

This is an explicit exception, not an accidental behavior.

## Why Staged Replace Is Deferred

A staged replace state machine was deferred because:
- it adds event-queue complexity beyond current project scope
- it interacts with cancel-effective and queue state transitions non-trivially
- current minimal model already supports submit/cancel gating for non-replace flows
- diagnostics and regressions can remain stable without expanding lifecycle states

## Diagnostics Interpretation (Current Schema)

Latency diagnostics fields are interpreted as:
- `configured_order_submit_ms`, `configured_order_ack_ms`, `configured_cancel_ms`:
  configured/effective venue-latency snapshot used by the run
- `latency_alias_applied`: whether flat `latency_ms` shorthand was used
- `order_ack_used_for_fill_gating`: always `false` in current phase

These diagnostics are interpretation/reporting artifacts and do not alter
queue or matching semantics.
