Generate a structured strategy plan for the following research goal.

## Research Goal
{research_goal}

## Additional Context
- Strategy style preference: {strategy_style}
- Legacy generation latency hint: {latency_ms} ms
- Constraints: {constraints}
{backtest_environment_block}

## Backtest-Aware Guidance
- Treat the canonical backtest constraint summary above as the primary runtime contract.
- Interpret tick-based parameters such as `holding_ticks`, `cooldown_ticks`, and `cancel_after_ticks` as cadence-dependent wall-clock durations.
- Ensure the strategy remains viable under queue waiting, venue latency, and minimal-immediate replace semantics.

## Execution Policy — Explicit Specification (MANDATORY)
- For short-horizon strategies, do not omit execution_policy.
- Explicitly specify placement_mode, cancel_after_ticks, and max_reprices when the strategy relies on short holding horizons or passive execution.
- A strategy without an explicit execution policy may be treated as unsafe in review.
- If the holding horizon is short (e.g. `holding_ticks` < 30), you MUST include an explicit `execution_policy` block with conservative defaults.
- Conservative execution policy example for short-horizon strategies:
  - `placement_mode`: "passive_join"
  - `cancel_after_ticks`: 10–20
  - `max_reprices`: 2–3
- When using passive placement, always pair with bounded `cancel_after_ticks` and `max_reprices`.

## Execution Policy — Churn Avoidance (MANDATORY)
- Prefer low-churn execution policies.
- Avoid frequent cancel/repost loops under queue and latency friction.
- If the strategy horizon is short, keep repricing bounded and exits robust.
- Do not rely on passive fills that require aggressive repeated repricing.
- Short-horizon strategies are especially vulnerable to queue position loss, adverse selection, and submit/cancel latency — design execution policies conservatively.
- Do not propose execution policies that depend on rapid cancel/repost cycles to capture small edges.
- Avoid setting `cancel_after_ticks` to very small values (e.g. 1-3 ticks); order lifetime should be meaningful relative to the cadence.
- Avoid large `max_reprices` values (e.g. > 5) for short-horizon strategies; each reprice resets queue position and incurs latency.
- If the strategy horizon is short (e.g. `holding_ticks` < 30), keep the execution policy simple: prefer fewer reprices, longer cancel horizons, and robust time/stop-loss exits.
- If the expected edge is small relative to realistic execution costs (queue slippage, adverse selection, latency), choose a more conservative execution policy rather than relying on aggressive passive repricing.
- For passive placement modes, always pair with a bounded `cancel_after_ticks` to prevent stale orders.

## Requirements
- Produce a StrategyPlan JSON conforming to the schema
- The strategy must be realistic for Korean equity tick-level LOB data
- Include both long and short entry policies when appropriate
- Name the strategy descriptively in snake_case

## Exit policy requirements (MANDATORY)
- Include at least one `close_all` exit rule using `position_attr`
- A stop-loss on `unrealized_pnl_bps` (e.g. <= -25.0) is required
- A time exit on `holding_ticks` (e.g. >= 100) is strongly recommended
- Do NOT rely solely on market-feature-based exits — they are not robust fail-safes

## Namespace rules (MANDATORY)
- Exit conditions for stop-loss and time exits MUST use `position_attr`, NOT `feature`
- Entry triggers and preconditions MUST use `feature` (market data), NOT `position_attr`
- `cross_feature` and `rolling_feature` MUST be market features, NOT position attributes
- If you use state_policy with increment, you MUST include a corresponding reset event
- Use only features from the allowed list in the system prompt
