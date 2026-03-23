"""Tests for v2 strategy generation pipeline (templates + lowering)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_generation.v2.templates_v2 import (
    V2_TEMPLATES, get_v2_template,
)
from strategy_block.strategy_generation.v2.lowering import lower_to_spec_v2
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from strategy_block.strategy_compiler.v2.compiler_v2 import StrategyCompilerV2
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2
from strategy_block.strategy_compiler import compile_strategy


class TestTemplatesV2:

    @pytest.mark.parametrize("name", sorted(V2_TEMPLATES.keys()))
    def test_template_loads(self, name: str):
        template = get_v2_template(name)
        assert isinstance(template, dict)
        assert "name" in template
        assert "entries" in template
        assert "exits" in template

    def test_unknown_template_raises(self):
        with pytest.raises(KeyError, match="Unknown v2 template"):
            get_v2_template("nonexistent_template")


class TestLowering:

    @pytest.mark.parametrize("name", sorted(V2_TEMPLATES.keys()))
    def test_lower_produces_valid_spec(self, name: str):
        template = get_v2_template(name)
        spec = lower_to_spec_v2(template)
        assert isinstance(spec, StrategySpecV2)
        assert spec.spec_format == "v2"
        errors = spec.validate()
        assert errors == [], f"Validation errors for {name}: {errors}"

    @pytest.mark.parametrize("name", sorted(V2_TEMPLATES.keys()))
    def test_lowered_spec_serialization_roundtrip(self, name: str, tmp_path: Path):
        template = get_v2_template(name)
        spec = lower_to_spec_v2(template)
        path = tmp_path / f"{name}.json"
        spec.save(path)
        loaded = StrategySpecV2.load(path)
        assert loaded.name == spec.name
        assert len(loaded.entry_policies) == len(spec.entry_policies)

    @pytest.mark.parametrize("name", sorted(V2_TEMPLATES.keys()))
    def test_lowered_spec_passes_review(self, name: str):
        template = get_v2_template(name)
        spec = lower_to_spec_v2(template)
        reviewer = StrategyReviewerV2()
        result = reviewer.review(spec)
        assert result.passed, (
            f"Review failed for {name}: "
            f"{[i.description for i in result.issues if i.severity == 'error']}"
        )

    @pytest.mark.parametrize("name", sorted(V2_TEMPLATES.keys()))
    def test_lowered_spec_compiles(self, name: str):
        template = get_v2_template(name)
        spec = lower_to_spec_v2(template)
        strategy = StrategyCompilerV2.compile(spec)
        assert strategy is not None
        assert strategy.name.startswith("CompiledV2:")

    @pytest.mark.parametrize("name", sorted(V2_TEMPLATES.keys()))
    def test_lowered_spec_compiles_via_dispatch(self, name: str):
        template = get_v2_template(name)
        spec = lower_to_spec_v2(template)
        strategy = compile_strategy(spec)
        from strategy_block.strategy_compiler.v2.compiler_v2 import CompiledStrategyV2
        assert isinstance(strategy, CompiledStrategyV2)


class TestEndToEndV2:

    def test_full_pipeline_imbalance(self):
        """Generate → review → compile → verify signal generation."""
        from unittest.mock import MagicMock
        from dataclasses import dataclass, field

        template = get_v2_template("imbalance_persist_momentum")
        spec = lower_to_spec_v2(template)

        # Review
        reviewer = StrategyReviewerV2()
        assert reviewer.review(spec).passed

        # Compile
        strategy = compile_strategy(spec)

        # Mock market state with strong imbalance
        @dataclass
        class MockLevel:
            price: float = 0.0
            volume: int = 100

        state = MagicMock()
        state.symbol = "005930"
        state.timestamp = "2026-01-01T09:00:00"
        state.lob = MagicMock()
        state.lob.mid_price = 50000.0
        state.lob.best_bid = 49900.0
        state.lob.best_ask = 50100.0
        state.lob.order_imbalance = 0.5
        state.lob.bid_levels = [MockLevel(49900.0, 200)]
        state.lob.ask_levels = [MockLevel(50100.0, 50)]
        state.spread_bps = 5.0
        state.features = {
            "order_imbalance": 0.5,
            "depth_imbalance": 0.3,
            "spread_bps": 5.0,
            "trade_flow_imbalance": 0.0,
        }
        state.trades = None

        signal = strategy.generate_signal(state)
        assert signal is not None
        assert signal.score > 0  # long entry triggered
        assert signal.tags.get("spec_format") == "v2"

    def test_registry_save_load_v2(self, tmp_path: Path):
        """V2 spec can be saved and loaded via registry."""
        from strategy_block.strategy_registry.registry import StrategyRegistry

        template = get_v2_template("spread_absorption_reversal")
        spec = lower_to_spec_v2(template)

        registry = StrategyRegistry(registry_dir=tmp_path)
        spec_path = registry.save_spec(spec, generation_backend="template")

        # Verify meta has spec_format=v2
        meta = registry.get_metadata(spec.name, spec.version)
        assert meta.spec_format == "v2"

        # Load back
        loaded = StrategySpecV2.load(spec_path)
        assert loaded.name == spec.name
        assert loaded.spec_format == "v2"
