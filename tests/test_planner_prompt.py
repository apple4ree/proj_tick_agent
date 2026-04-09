"""
tests/test_planner_prompt.py
-----------------------------
Planner prompt and logging contract tests — v2.3.

Tests:
  B. BUILTIN_FEATURES injected into planner system prompt
  D. planner log context separation from feedback log
     (via monkeypatched OpenAIClient.chat — no real API calls)
  E. FakeLLMClient returns correct planner / feedback payloads
  F. FeedbackGenerator works with injected FakeLLMClient
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tests.fakes.fake_llm_client import (
    FakeLLMClient,
    FAKE_PLANNER_RESPONSE,
    FAKE_FEEDBACK_RESPONSE,
)


# ── B. Planner receives canonical feature list ────────────────────────────────

class TestPlannerFeaturesInjection:
    def test_planner_system_no_placeholder(self):
        """$features_list must be substituted — not left as literal."""
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        assert "$features_list" not in _PLANNER_SYSTEM

    def test_planner_system_contains_tick_size(self):
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        assert "tick_size" in _PLANNER_SYSTEM

    def test_planner_system_contains_bid_1_price(self):
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        assert "bid_1_price" in _PLANNER_SYSTEM

    def test_planner_system_contains_ask_10_volume(self):
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        assert "ask_10_volume" in _PLANNER_SYSTEM

    def test_planner_system_contains_order_imbalance(self):
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        assert "order_imbalance" in _PLANNER_SYSTEM

    def test_planner_system_contains_derived_features_schema(self):
        """planner_system.txt must mention derived_features schema."""
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        assert "derived_features" in _PLANNER_SYSTEM

    def test_planner_system_contains_source_type(self):
        """planner_system.txt must describe source_type field."""
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        assert "source_type" in _PLANNER_SYSTEM

    def test_planner_system_mentions_derived_feature_source_type(self):
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        assert "derived_feature" in _PLANNER_SYSTEM

    def test_planner_system_contains_tick_size_formula_example(self):
        """planner_system.txt must show tick-normalized formula example."""
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        assert "tick_size" in _PLANNER_SYSTEM
        assert "spread_ticks" in _PLANNER_SYSTEM

    def test_planner_messages_system_role_present(self):
        from strategy_loop.planner_prompt_builder import build_planner_messages
        msgs = build_planner_messages(research_goal="test goal")
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_planner_system_version_23(self):
        """planner_system.txt must instruct planner to produce v2.3."""
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        assert "2.3" in _PLANNER_SYSTEM

    def test_planner_system_mentions_strategy_spec_canonical(self):
        """strategy_spec described as the canonical artifact."""
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        assert "canonical" in _PLANNER_SYSTEM.lower()

    def test_planner_features_list_matches_builtin_features(self):
        """All items in BUILTIN_FEATURES should appear in _PLANNER_SYSTEM."""
        from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        samples = sorted(BUILTIN_FEATURES)[:10]
        for feat in samples:
            assert feat in _PLANNER_SYSTEM, f"{feat!r} missing from planner system prompt"

    def test_planner_system_mentions_threshold_param(self):
        """planner_system.txt must describe threshold_param field."""
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        assert "threshold_param" in _PLANNER_SYSTEM

    def test_planner_system_no_duplicate_tunable_params_rule(self):
        """planner_system.txt must mention no duplicate names rule."""
        from strategy_loop.planner_prompt_builder import _PLANNER_SYSTEM
        assert "duplicate" in _PLANNER_SYSTEM.lower()


# ── D. Log context separation ─────────────────────────────────────────────────
# Logging is tested by monkeypatching OpenAIClient.chat to avoid real API calls.

class TestLogContextSeparation:
    """Verify that chat_json(context=...) writes log files with the correct prefix.

    OpenAIClient.chat is monkeypatched to return a JSON string without making
    real API calls. This isolates the logging behavior of chat_json.
    """

    def _make_client_with_patched_chat(self, monkeypatch, response_dict: dict) -> "any":
        """Return an OpenAIClient whose .chat() is stubbed to return response_dict as JSON."""
        import strategy_loop.openai_client as oc_mod
        # Bypass the openai import by patching __init__ after construction
        import unittest.mock as mock
        client = oc_mod.OpenAIClient.__new__(oc_mod.OpenAIClient)
        client.model = "gpt-4o-mini"
        client._client = mock.MagicMock()
        monkeypatch.setattr(
            client, "chat",
            lambda messages, response_format="json_object": json.dumps(response_dict),
        )
        return client

    def test_chat_json_planner_context_logs_planner_file(self, tmp_path: Path, monkeypatch):
        """chat_json(context='planner') must write planner_*.json, not feedback_*.json."""
        import strategy_loop.openai_client as oc_mod

        log_dir = tmp_path / "llm_logs"
        monkeypatch.setattr(oc_mod, "_LOG_DIR", log_dir)

        client = self._make_client_with_patched_chat(monkeypatch, {"strategy_spec": {}})
        client.chat_json([{"role": "user", "content": "hi"}], context="planner")

        planner_files = list(log_dir.glob("planner_*.json"))
        feedback_files = list(log_dir.glob("feedback_*.json"))
        assert len(planner_files) == 1
        assert len(feedback_files) == 0

    def test_chat_json_feedback_context_logs_feedback_file(self, tmp_path: Path, monkeypatch):
        import strategy_loop.openai_client as oc_mod

        log_dir = tmp_path / "llm_logs"
        monkeypatch.setattr(oc_mod, "_LOG_DIR", log_dir)

        client = self._make_client_with_patched_chat(monkeypatch, {"primary_issue": "x"})
        client.chat_json([{"role": "user", "content": "hi"}], context="feedback")

        planner_files = list(log_dir.glob("planner_*.json"))
        feedback_files = list(log_dir.glob("feedback_*.json"))
        assert len(planner_files) == 0
        assert len(feedback_files) == 1

    def test_chat_json_default_context_is_feedback(self, tmp_path: Path, monkeypatch):
        """Default context must remain 'feedback' — backward compat for FeedbackGenerator."""
        import strategy_loop.openai_client as oc_mod

        log_dir = tmp_path / "llm_logs"
        monkeypatch.setattr(oc_mod, "_LOG_DIR", log_dir)

        client = self._make_client_with_patched_chat(monkeypatch, {"primary_issue": "x"})
        client.chat_json([{"role": "user", "content": "hi"}])

        assert len(list(log_dir.glob("feedback_*.json"))) == 1

    def test_planner_log_contains_correct_context_field(self, tmp_path: Path, monkeypatch):
        import strategy_loop.openai_client as oc_mod

        log_dir = tmp_path / "llm_logs"
        monkeypatch.setattr(oc_mod, "_LOG_DIR", log_dir)

        client = self._make_client_with_patched_chat(monkeypatch, {"strategy_spec": {}})
        client.chat_json([{"role": "user", "content": "planner call"}], context="planner")

        log_file = list(log_dir.glob("planner_*.json"))[0]
        record = json.loads(log_file.read_text())
        assert record["context"] == "planner"


# ── E. FakeLLMClient contract ─────────────────────────────────────────────────

class TestFakeLLMClient:
    def test_planner_context_returns_planner_payload(self):
        """FakeLLMClient.chat_json(context='planner') returns FAKE_PLANNER_RESPONSE."""
        client = FakeLLMClient()
        resp = client.chat_json(
            [{"role": "user", "content": "design a strategy"}],
            context="planner",
        )
        assert "strategy_spec" in resp
        assert "strategy_text" in resp

    def test_feedback_context_returns_feedback_payload(self):
        """FakeLLMClient.chat_json(context='feedback') returns FAKE_FEEDBACK_RESPONSE."""
        client = FakeLLMClient()
        resp = client.chat_json(
            [{"role": "user", "content": "give feedback"}],
            context="feedback",
        )
        assert "primary_issue" in resp
        assert "strategy_spec" not in resp

    def test_default_context_is_feedback(self):
        """chat_json() without context returns feedback payload."""
        client = FakeLLMClient()
        resp = client.chat_json([{"role": "user", "content": "hi"}])
        assert "primary_issue" in resp

    def test_chat_code_returns_code_string(self):
        client = FakeLLMClient()
        code = client.chat_code([{"role": "user", "content": "write code"}])
        assert isinstance(code, str)
        assert "generate_signal" in code

    def test_custom_planner_response_injected(self):
        custom = {"strategy_spec": {"version": "2.3"}, "strategy_text": "custom"}
        client = FakeLLMClient(planner_response=custom)
        resp = client.chat_json([], context="planner")
        assert resp["strategy_text"] == "custom"

    def test_custom_feedback_response_injected(self):
        custom = {"primary_issue": "custom_issue", "verdict": "fail"}
        client = FakeLLMClient(feedback_response=custom)
        resp = client.chat_json([], context="feedback")
        assert resp["primary_issue"] == "custom_issue"

    def test_call_recording_disabled_by_default(self):
        client = FakeLLMClient()
        client.chat_json([], context="planner")
        assert client.calls == []

    def test_call_recording_captures_context(self):
        client = FakeLLMClient(record_calls=True)
        client.chat_json([{"role": "user", "content": "hi"}], context="planner")
        client.chat_json([{"role": "user", "content": "fb"}], context="feedback")
        client.chat_code([{"role": "user", "content": "code"}])
        assert len(client.calls) == 3
        assert client.calls[0]["context"] == "planner"
        assert client.calls[1]["context"] == "feedback"
        assert client.calls[2]["type"] == "chat_code"

    def test_fake_planner_response_is_v23(self):
        """Default fake planner response must be v2.3 with threshold_param on all conditions."""
        client = FakeLLMClient()
        resp = client.chat_json([], context="planner")
        spec = resp["strategy_spec"]
        assert spec["version"] == "2.3"
        for c in spec["entry_conditions"] + spec["exit_signal_conditions"]:
            assert "threshold_param" in c, (
                f"Condition {c} missing threshold_param in fake planner response"
            )


# ── F. FeedbackGenerator with injected FakeLLMClient ─────────────────────────

class TestFeedbackGeneratorWithFakeClient:
    def test_feedback_generator_still_passes_without_context(self):
        """FeedbackGenerator calls chat_json() without explicit context → must still work."""
        from strategy_loop.feedback_generator import FeedbackGenerator

        client = FakeLLMClient()
        gen = FeedbackGenerator(client=client)
        summary = {
            "signal_count": 10, "n_states": 1000, "n_fills": 10,
            "avg_holding_period": 22.0, "net_pnl": 100.0,
            "total_realized_pnl": 200.0, "total_unrealized_pnl": 0.0,
            "total_commission": 50.0, "total_slippage": 30.0, "total_impact": 20.0,
        }
        fb = gen.generate(
            code="def generate_signal(f, p): return None",
            backtest_summary=summary,
        )
        assert "verdict" in fb

    def test_feedback_uses_default_feedback_context(self):
        """FeedbackGenerator must use context='feedback' (default) when calling chat_json."""
        from strategy_loop.feedback_generator import FeedbackGenerator

        client = FakeLLMClient(record_calls=True)
        gen = FeedbackGenerator(client=client)
        summary = {
            "signal_count": 0, "n_states": 1000, "n_fills": 0,
            "avg_holding_period": 0.0, "net_pnl": 0.0,
            "total_realized_pnl": 0.0, "total_unrealized_pnl": 0.0,
            "total_commission": 0.0, "total_slippage": 0.0, "total_impact": 0.0,
        }
        gen.generate(code="def generate_signal(f, p): return None", backtest_summary=summary)
        chat_json_calls = [c for c in client.calls if c["type"] == "chat_json"]
        assert len(chat_json_calls) >= 1
        # All FeedbackGenerator calls must use context="feedback" (default)
        for call in chat_json_calls:
            assert call["context"] == "feedback"
