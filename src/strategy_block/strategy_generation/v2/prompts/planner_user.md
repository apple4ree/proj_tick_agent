Generate a structured strategy plan for the following research goal.

## Research Goal
{research_goal}

## Additional Context
- Strategy style preference: {strategy_style}
- Target latency: {latency_ms} ms
- Constraints: {constraints}
<!-- TODO: include market_data_delay_ms when observation lag is surfaced
     to the generation pipeline. This helps the planner account for stale
     market data when designing entry/exit timing. -->

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
