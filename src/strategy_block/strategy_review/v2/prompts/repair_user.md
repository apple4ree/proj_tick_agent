Build a constrained repair plan.

[SPEC SUMMARY JSON]
{spec_summary}

[STATIC REVIEW JSON]
{static_review_json}

[LLM REVIEW JSON]
{llm_review_json}

[BACKTEST CONSTRAINT SUMMARY]
{backtest_environment_summary}

[BACKTEST ENVIRONMENT JSON]
{backtest_environment_json}

## Recent Backtest Feedback
{backtest_feedback_summary}

## Recent Backtest Feedback (JSON)
{backtest_feedback_json}

Produce one RepairPlan JSON object:
- summary
- operations: list of RepairOperation objects
  fields: op, target, value, reason
- expected_effect
- requires_manual_followup

Rules:
- Use only allowed operation types.
- Prefer deterministic small patches over broad rewrites.
- If no safe deterministic fix is available, return empty operations and requires_manual_followup=true.
- Prefer minimal changes that directly target the observed failure pattern.
- If churn is high, reduce churn before changing alpha logic.
- If queue effectiveness is poor, do not keep proposing passive repricing loops.
- If costs dominate pnl, reduce turnover and sizing before rewriting the strategy.
- Do not rewrite the whole strategy if execution policy adjustments can make it safer.
- Bound repricing and extend cancellation horizon before altering the entry logic.
- When execution_policy_too_aggressive or churn_risk_high issues are present, always emit set_cancel_after_ticks and/or set_max_reprices operations FIRST.
- If recent backtest feedback is provided, prioritize operations using feedback pattern order and keep output compact with stable deduped ordering.
