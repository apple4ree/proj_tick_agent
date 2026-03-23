"""Tests for v2 registry/execution coexistence with v1."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_specs.schema import StrategySpec, SignalRule, ExitRule
from strategy_block.strategy_specs.v2.schema_v2 import (
    StrategySpecV2, EntryPolicyV2, ExitPolicyV2, ExitRuleV2,
    ExitActionV2, RiskPolicyV2,
)
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ConstExpr
from strategy_block.strategy_registry.registry import (
    StrategyRegistry, _detect_spec_format, _load_spec_by_format,
)
from strategy_block.strategy_registry.models import StrategyStatus
from strategy_block.strategy_compiler import compile_strategy
from strategy_block.strategy_compiler.v2.compiler_v2 import CompiledStrategyV2
from strategy_block.strategy_compiler.compiler import CompiledStrategy


def _v1_spec() -> StrategySpec:
    return StrategySpec(
        name="test_v1",
        signal_rules=[
            SignalRule(feature="order_imbalance", operator=">",
                       threshold=0.3, score_contribution=0.5),
        ],
        exit_rules=[
            ExitRule(exit_type="stop_loss", threshold_bps=15.0),
        ],
    )


def _v2_spec() -> StrategySpecV2:
    return StrategySpecV2(
        name="test_v2",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry", side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
                strength=ConstExpr(value=0.5),
            ),
        ],
        exit_policies=[
            ExitPolicyV2(name="exits", rules=[
                ExitRuleV2(
                    name="stop", priority=1,
                    condition=ComparisonExpr(feature="order_imbalance", op="<", threshold=-0.2),
                    action=ExitActionV2(type="close_all"),
                ),
            ]),
        ],
        risk_policy=RiskPolicyV2(max_position=500, inventory_cap=1000),
    )


class TestRegistrySaveLoadV2:

    def test_v2_spec_save_load_preserves_type(self, tmp_path):
        """V2 spec saved to registry loads back as StrategySpecV2."""
        registry = StrategyRegistry(registry_dir=tmp_path)
        spec = _v2_spec()
        registry.save_spec(spec)

        loaded = registry.load_spec("test_v2", "2.0")
        assert isinstance(loaded, StrategySpecV2)
        assert loaded.spec_format == "v2"
        assert loaded.name == "test_v2"
        assert len(loaded.entry_policies) == 1

    def test_v1_spec_save_load_preserves_type(self, tmp_path):
        """V1 spec saved to registry still loads as StrategySpec."""
        registry = StrategyRegistry(registry_dir=tmp_path)
        spec = _v1_spec()
        registry.save_spec(spec)

        loaded = registry.load_spec("test_v1", "1.0")
        assert isinstance(loaded, StrategySpec)
        assert loaded.name == "test_v1"
        assert len(loaded.signal_rules) == 1

    def test_v1_v2_coexist_in_same_registry(self, tmp_path):
        """Both v1 and v2 specs can live in the same registry."""
        registry = StrategyRegistry(registry_dir=tmp_path)
        registry.save_spec(_v1_spec())
        registry.save_spec(_v2_spec())

        v1 = registry.load_spec("test_v1", "1.0")
        v2 = registry.load_spec("test_v2", "2.0")
        assert isinstance(v1, StrategySpec)
        assert isinstance(v2, StrategySpecV2)

    def test_metadata_tracks_spec_format(self, tmp_path):
        """Metadata correctly records spec_format for v2."""
        registry = StrategyRegistry(registry_dir=tmp_path)
        registry.save_spec(_v2_spec())

        meta = registry.get_metadata("test_v2", "2.0")
        assert meta.spec_format == "v2"

    def test_v2_format_detection_from_json(self, tmp_path):
        """_detect_spec_format reads spec_format from JSON content."""
        spec = _v2_spec()
        path = tmp_path / "test.json"
        spec.save(path)

        assert _detect_spec_format(path) == "v2"

    def test_v1_format_detection_from_json(self, tmp_path):
        """_detect_spec_format defaults to v1 for v1 specs."""
        spec = _v1_spec()
        path = tmp_path / "test.json"
        spec.save(path)

        assert _detect_spec_format(path) == "v1"


class TestRegistryCompileV2:

    def test_registry_compile_v2(self, tmp_path):
        """registry.compile() works for v2 specs."""
        registry = StrategyRegistry(registry_dir=tmp_path)
        registry.save_spec(_v2_spec())

        strategy = registry.compile("test_v2", "2.0")
        assert isinstance(strategy, CompiledStrategyV2)

    def test_registry_compile_v1(self, tmp_path):
        """registry.compile() still works for v1 specs."""
        registry = StrategyRegistry(registry_dir=tmp_path)
        registry.save_spec(_v1_spec())

        strategy = registry.compile("test_v1", "1.0")
        assert isinstance(strategy, CompiledStrategy)

    def test_compile_strategy_dispatch_v2(self, tmp_path):
        """compile_strategy() correctly dispatches v2 specs."""
        spec = _v2_spec()
        strategy = compile_strategy(spec)
        assert isinstance(strategy, CompiledStrategyV2)


class TestRegistryExecutionGateV2:

    def test_load_spec_for_execution_v2(self, tmp_path):
        """load_spec_for_execution returns StrategySpecV2 for v2."""
        registry = StrategyRegistry(registry_dir=tmp_path)
        registry.save_spec(_v2_spec())

        # Promote to approved state
        registry.update_status("test_v2", "2.0", StrategyStatus.REVIEWED)
        meta = registry.get_metadata("test_v2", "2.0")
        meta.static_review_passed = True
        meta.save(tmp_path / "test_v2_v2.0.meta.json")
        registry.update_status("test_v2", "2.0", StrategyStatus.APPROVED)

        spec = registry.load_spec_for_execution("test_v2", "2.0")
        assert isinstance(spec, StrategySpecV2)

    def test_latest_approved_v2(self, tmp_path):
        """latest_approved returns StrategySpecV2 for v2 specs."""
        registry = StrategyRegistry(registry_dir=tmp_path)
        registry.save_spec(_v2_spec())

        registry.update_status("test_v2", "2.0", StrategyStatus.REVIEWED)
        meta = registry.get_metadata("test_v2", "2.0")
        meta.static_review_passed = True
        meta.save(tmp_path / "test_v2_v2.0.meta.json")
        registry.update_status("test_v2", "2.0", StrategyStatus.APPROVED)

        spec = registry.latest_approved("test_v2")
        assert isinstance(spec, StrategySpecV2)


class TestRegistryIterationV2:

    def test_iter_specs_includes_v2(self, tmp_path):
        """iter_specs yields both v1 and v2 specs."""
        registry = StrategyRegistry(registry_dir=tmp_path)
        registry.save_spec(_v1_spec())
        registry.save_spec(_v2_spec())

        specs = list(registry.iter_specs())
        types = {type(s).__name__ for s in specs}
        assert "StrategySpec" in types
        assert "StrategySpecV2" in types

    def test_list_strategies_includes_v2(self, tmp_path):
        """list_strategies includes v2 with correct fields."""
        registry = StrategyRegistry(registry_dir=tmp_path)
        registry.save_spec(_v1_spec())
        registry.save_spec(_v2_spec())

        entries = registry.list_strategies()
        formats = {e.get("spec_format") for e in entries}
        assert "v1" in formats
        assert "v2" in formats

    def test_list_specs_includes_v2(self, tmp_path):
        """list_specs (metadata-based) includes v2."""
        registry = StrategyRegistry(registry_dir=tmp_path)
        registry.save_spec(_v1_spec())
        registry.save_spec(_v2_spec())

        entries = registry.list_specs()
        formats = {e.get("spec_format") for e in entries}
        assert "v1" in formats
        assert "v2" in formats
