Review this strategy with environment-aware semantics.

[SPEC SUMMARY JSON]
{spec_summary}

[STATIC REVIEW JSON]
{static_review_json}

[BACKTEST CONSTRAINT SUMMARY]
{backtest_environment_summary}

[BACKTEST ENVIRONMENT JSON]
{backtest_environment_json}

## Recent Backtest Feedback
{backtest_feedback_summary}

## Recent Backtest Feedback (JSON)
{backtest_feedback_json}

Produce one LLMReviewReport JSON object:
- overall_assessment: pass_with_notes | revise_recommended | high_risk
- summary: concise assessment
- issues: list of issue objects with severity/category/description/rationale/suggested_fix
- repair_recommended: boolean
- focus_areas: list of concrete areas

Rules:
- Use high_risk only for issues that can materially break robustness.
- Do not claim final PASS/FAIL authority.
- Keep suggestions compatible with deterministic patching constraints.
- When execution policy parameters are aggressive (high max_reprices, low cancel_after_ticks) relative to the backtest environment latency, flag as churn_risk or queue_latency_risk.
- For short-horizon strategies with passive placement and active repricing, always include execution_policy in focus_areas and recommend bounded repricing.
- When suggesting fixes for churn issues, prefer: increase cancel_after_ticks, reduce max_reprices, add robust time/stop-loss exit — in that order.
- If recent backtest feedback is provided, ground critique in those aggregate outcomes (churn/queue/cost/cancel-mix) rather than spec-only interpretation.
