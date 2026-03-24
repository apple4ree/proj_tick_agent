"""Integration tests for v2-only registry/compiler paths."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_specs.v2.schema_v2 import (
    StrategySpecV2, EntryPolicyV2, ExitPolicyV2, ExitRuleV2,
    ExitActionV2, RiskPolicyV2,
)
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ConstExpr
from strategy_block.strategy_registry.registry import StrategyRegistry, _detect_spec_format
from strategy_block.strategy_registry.models import StrategyStatus
from strategy_block.strategy_compiler import compile_strategy
from strategy_block.strategy_compiler.v2.compiler_v2 import CompiledStrategyV2


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


def test_v2_spec_save_load_preserves_type(tmp_path):
    registry = StrategyRegistry(registry_dir=tmp_path)
    spec = _v2_spec()
    registry.save_spec(spec)

    loaded = registry.load_spec("test_v2", "2.0")
    assert isinstance(loaded, StrategySpecV2)
    assert loaded.spec_format == "v2"


def test_v2_detect_format(tmp_path):
    path = tmp_path / "spec.json"
    _v2_spec().save(path)
    assert _detect_spec_format(path) == "v2"


def test_v1_spec_rejected_by_detector(tmp_path):
    path = tmp_path / "v1_like.json"
    path.write_text('{"name":"old","version":"1.0"}', encoding="utf-8")
    try:
        _detect_spec_format(path)
    except ValueError as exc:
        assert "Unsupported spec format" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-v2 spec")


def test_registry_compile_v2(tmp_path):
    registry = StrategyRegistry(registry_dir=tmp_path)
    registry.save_spec(_v2_spec())
    strategy = registry.compile("test_v2", "2.0")
    assert isinstance(strategy, CompiledStrategyV2)


def test_compile_strategy_dispatch_v2():
    strategy = compile_strategy(_v2_spec())
    assert isinstance(strategy, CompiledStrategyV2)


def test_load_spec_for_execution_v2(tmp_path):
    registry = StrategyRegistry(registry_dir=tmp_path)
    registry.save_spec(_v2_spec())

    registry.update_status("test_v2", "2.0", StrategyStatus.REVIEWED)
    meta = registry.get_metadata("test_v2", "2.0")
    meta.static_review_passed = True
    meta.save(tmp_path / "test_v2_v2.0.meta.json")
    registry.update_status("test_v2", "2.0", StrategyStatus.APPROVED)

    spec = registry.load_spec_for_execution("test_v2", "2.0")
    assert isinstance(spec, StrategySpecV2)
