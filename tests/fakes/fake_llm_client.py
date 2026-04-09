"""
tests/fakes/fake_llm_client.py
--------------------------------
FakeLLMClient — injected into LoopRunner / FeedbackGenerator in tests.

Replaces OpenAIClient(mode="mock"). Provides explicit, per-test-controllable
fixtures for planner, feedback, and code responses.

Usage:

    from tests.fakes.fake_llm_client import FakeLLMClient, FAKE_PLANNER_RESPONSE

    client = FakeLLMClient()               # uses default fixtures
    client = FakeLLMClient(
        planner_response=my_spec_dict,
        feedback_response={"primary_issue": "fee_dominated", ...},
        code_response="ORDER_IMBALANCE_THRESHOLD = 0.3\n...",
        record_calls=True,
    )
    runner = LoopRunner(client=client, ...)

    # inspect calls
    assert client.calls[0] == {"type": "chat_json", "context": "planner", "messages": [...]}
"""
from __future__ import annotations

from typing import Any


# ── Default fixtures (v2.3, threshold_param on all conditions) ─────────────────

FAKE_PLANNER_RESPONSE: dict[str, Any] = {
    "strategy_text": (
        "## Fake Strategy\n\n"
        "Entry: order_imbalance > 0.3 and spread_bps < 50.\n"
        "Exit: after 20 ticks or when order_imbalance < -0.05."
    ),
    "strategy_spec": {
        "version": "2.3",
        "archetype": 1,
        "archetype_name": "liquidity imbalance continuation",
        "derived_features": [],
        "entry_conditions": [
            {
                "source_type": "feature", "source": "order_imbalance",
                "op": ">", "threshold": 0.3,
                "threshold_param": "ORDER_IMBALANCE_THRESHOLD",
            },
            {
                "source_type": "feature", "source": "spread_bps",
                "op": "<", "threshold": 50.0,
                "threshold_param": "SPREAD_MAX_BPS",
            },
        ],
        "exit_time_ticks": 20,
        "exit_signal_conditions": [
            {
                "source_type": "feature", "source": "order_imbalance",
                "op": "<", "threshold": -0.05,
                "threshold_param": "REVERSAL_THRESHOLD",
            },
        ],
        "tunable_params": [
            {"name": "ORDER_IMBALANCE_THRESHOLD", "default": 0.3,
             "type": "float", "range": [-0.9, 0.9]},
            {"name": "SPREAD_MAX_BPS", "default": 50.0,
             "type": "float", "range": [1.0, 200.0]},
            {"name": "HOLDING_TICKS_EXIT", "default": 20,
             "type": "int", "range": [5, 120]},
            {"name": "REVERSAL_THRESHOLD", "default": -0.05,
             "type": "float", "range": [-0.9, 0.9]},
        ],
        "features_used": ["order_imbalance", "order_imbalance_ema", "spread_bps"],
        "rationale": "Buy on sustained buy-side order book pressure with acceptable spread.",
    },
}

FAKE_FEEDBACK_RESPONSE: dict[str, Any] = {
    "evidence": ["Fake evidence — no real analysis."],
    "primary_issue": "Fake feedback — no real analysis performed.",
    "issues": [],
    "suggestions": [
        "Try a higher order_imbalance threshold for fewer but higher-quality signals.",
    ],
}

FAKE_CODE_RESPONSE: str = """\
ORDER_IMBALANCE_THRESHOLD = 0.30
SPREAD_MAX_BPS = 50.0
HOLDING_TICKS_EXIT = 20
REVERSAL_THRESHOLD = -0.05

def generate_signal(features, position):
    holding = position["holding_ticks"]
    in_pos = position["in_position"]

    if in_pos:
        if holding >= HOLDING_TICKS_EXIT:
            return -1
        if features.get("order_imbalance", 0.0) < REVERSAL_THRESHOLD:
            return -1
        return None

    oi = features.get("order_imbalance", 0.0)
    spread = features.get("spread_bps", 999.0)

    if oi > ORDER_IMBALANCE_THRESHOLD and spread < SPREAD_MAX_BPS:
        return 1

    return None
"""


# ── FakeLLMClient ──────────────────────────────────────────────────────────────

class FakeLLMClient:
    """Deterministic stub for LLM client — suitable for LoopRunner / FeedbackGenerator tests.

    Replaces OpenAIClient without calling the OpenAI API.

    Args:
        planner_response:  Dict returned by chat_json(context="planner").
                           Defaults to FAKE_PLANNER_RESPONSE (v2.3 valid spec).
        feedback_response: Dict returned by chat_json(context="feedback" or any non-planner).
                           Defaults to FAKE_FEEDBACK_RESPONSE.
        code_response:     String returned by chat_code().
                           Defaults to FAKE_CODE_RESPONSE.
        record_calls:      If True, every call is appended to self.calls.
    """

    def __init__(
        self,
        planner_response: dict[str, Any] | None = None,
        feedback_response: dict[str, Any] | None = None,
        code_response: str | None = None,
        record_calls: bool = False,
    ) -> None:
        self._planner = planner_response if planner_response is not None else FAKE_PLANNER_RESPONSE
        self._feedback = feedback_response if feedback_response is not None else FAKE_FEEDBACK_RESPONSE
        self._code = code_response if code_response is not None else FAKE_CODE_RESPONSE
        self._record = record_calls
        self.calls: list[dict[str, Any]] = []

    def chat_json(self, messages: list[dict], context: str = "feedback") -> dict[str, Any]:
        """Return planner or feedback dict based on context."""
        resp = self._planner if context == "planner" else self._feedback
        if self._record:
            self.calls.append({
                "type": "chat_json",
                "context": context,
                "messages": messages,
                "response": resp,
            })
        return resp

    def chat_code(self, messages: list[dict]) -> str:
        """Return the canned code string."""
        if self._record:
            self.calls.append({
                "type": "chat_code",
                "messages": messages,
                "response": self._code,
            })
        return self._code
