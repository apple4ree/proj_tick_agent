"""
Tests for hierarchical BacktestConfig and nested sub-configs.
"""

import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from evaluation_orchestration.layer7_validation.backtest_config import (
    BacktestConfig,
    FeeConfig,
    LatencyConfig,
    ExchangeConfig,
    SlicingConfig,
    PlacementConfig,
    RiskConfig,
)


class TestFlatConstruction:
    """backward-compatible flat construction을 테스트한다."""

    def test_minimal_config(self):
        cfg = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
        )
        assert cfg.symbol == "005930"
        assert cfg.initial_cash == 1e8
        assert cfg.seed == 42
        assert cfg.fee.type == "krx"

    def test_flat_fields_propagate_to_nested(self):
        cfg = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            fee_model="zero",
            slicing_algo="POV",
            placement_style="aggressive",
            queue_model="prob_queue",
            queue_position_assumption=0.3,
        )
        assert cfg.fee.type == "zero"
        assert cfg.slicing.algo == "POV"
        assert cfg.placement.style == "aggressive"
        assert cfg.exchange.queue_model == "prob_queue"
        assert cfg.exchange.queue_position_assumption == 0.3


class TestNestedConstruction:
    """explicit nested config construction을 테스트한다."""

    def test_nested_fee_config(self):
        cfg = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            fee=FeeConfig(type="krx", commission_bps=0.5, market="KOSDAQ"),
        )
        assert cfg.fee.commission_bps == 0.5
        assert cfg.fee.market == "KOSDAQ"

    def test_nested_risk_config(self):
        cfg = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            risk=RiskConfig(max_position=500, target_mode="Kelly"),
        )
        assert cfg.risk.max_position == 500
        assert cfg.risk.target_mode == "Kelly"
        assert cfg.risk.max_gross_notional == 1e8  # 기본값은 initial_cash


class TestLatencyAliasSemantics:
    """latency_ms compatibility alias -> nested latency mapping."""

    def test_alias_populates_nested_when_latency_missing(self):
        cfg = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            latency_ms=100.0,
        )
        assert cfg.latency.order_submit_ms == 30.0
        assert cfg.latency.order_ack_ms == 70.0
        assert cfg.latency.cancel_ms == 20.0
        assert cfg.to_dict()["latency_alias_applied"] is True

    def test_alias_does_not_override_explicit_nested_latency(self):
        cfg = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            latency_ms=100.0,
            latency=LatencyConfig(order_submit_ms=1.5, order_ack_ms=2.5, cancel_ms=3.5),
        )
        assert cfg.latency.order_submit_ms == 1.5
        assert cfg.latency.order_ack_ms == 2.5
        assert cfg.latency.cancel_ms == 3.5
        assert cfg.to_dict()["latency_alias_applied"] is False

    def test_profile_only_nested_latency_disables_alias(self):
        cfg = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            latency_ms=100.0,
            latency=LatencyConfig(profile="retail"),
        )
        # Profile-only nested config must not be backfilled by flat alias.
        assert cfg.latency.order_submit_ms is None
        assert cfg.latency.order_ack_ms is None
        assert cfg.latency.cancel_ms is None
        assert cfg.to_dict()["latency_alias_applied"] is False

    def test_partial_nested_latency_still_disables_alias(self):
        cfg = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            latency_ms=100.0,
            latency=LatencyConfig(profile="retail", order_submit_ms=1.5),
        )
        assert cfg.latency.order_submit_ms == 1.5
        assert cfg.latency.order_ack_ms is None
        assert cfg.latency.cancel_ms is None
        assert cfg.to_dict()["latency_alias_applied"] is False


class TestValidation:
    """config validation을 테스트한다."""

    def test_invalid_fee_type_raises(self):
        with pytest.raises(ValueError, match="fee.type must be"):
            BacktestConfig(
                symbol="005930",
                start_date="2026-03-13",
                end_date="2026-03-13",
                fee=FeeConfig(type="invalid"),
            )

    def test_negative_commission_raises(self):
        with pytest.raises(ValueError, match="commission_bps must be"):
            BacktestConfig(
                symbol="005930",
                start_date="2026-03-13",
                end_date="2026-03-13",
                fee=FeeConfig(commission_bps=-1.0),
            )

    def test_invalid_slicing_algo_raises(self):
        with pytest.raises(ValueError, match="slicing.algo must be"):
            BacktestConfig(
                symbol="005930",
                start_date="2026-03-13",
                end_date="2026-03-13",
                slicing=SlicingConfig(algo="invalid"),
            )

    def test_invalid_queue_model_raises(self):
        with pytest.raises(ValueError, match="exchange.queue_model must be"):
            BacktestConfig(
                symbol="005930",
                start_date="2026-03-13",
                end_date="2026-03-13",
                exchange=ExchangeConfig(queue_model="invalid"),
            )

    def test_invalid_queue_position_assumption_raises(self):
        with pytest.raises(ValueError, match="queue_position_assumption"):
            BacktestConfig(
                symbol="005930",
                start_date="2026-03-13",
                end_date="2026-03-13",
                exchange=ExchangeConfig(queue_position_assumption=1.1),
            )

    def test_invalid_initial_cash_raises(self):
        with pytest.raises(ValueError, match="initial_cash must be > 0"):
            BacktestConfig(
                symbol="005930",
                start_date="2026-03-13",
                end_date="2026-03-13",
                initial_cash=0,
            )

    def test_negative_market_data_delay_raises(self):
        with pytest.raises(ValueError, match="market_data_delay_ms must be >= 0"):
            BacktestConfig(
                symbol="005930",
                start_date="2026-03-13",
                end_date="2026-03-13",
                market_data_delay_ms=-1.0,
            )

    def test_negative_decision_compute_raises(self):
        with pytest.raises(ValueError, match="decision_compute_ms must be >= 0"):
            BacktestConfig(
                symbol="005930",
                start_date="2026-03-13",
                end_date="2026-03-13",
                decision_compute_ms=-5.0,
            )


class TestSerialization:
    """to_dict, from_dict, to_yaml, from_yaml을 테스트한다."""

    def test_to_dict_includes_nested(self):
        cfg = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            fee=FeeConfig(commission_bps=2.0),
        )
        d = cfg.to_dict()
        assert d["fee"]["commission_bps"] == 2.0

    def test_from_dict_flat(self):
        d = {
            "symbol": "005930",
            "start_date": "2026-03-13",
            "end_date": "2026-03-13",
            "fee_model": "zero",
        }
        cfg = BacktestConfig.from_dict(d)
        assert cfg.fee.type == "zero"

    def test_from_dict_nested(self):
        d = {
            "symbol": "005930",
            "start_date": "2026-03-13",
            "end_date": "2026-03-13",
            "fee": {"type": "krx", "commission_bps": 0.8},
            "slicing": {"algo": "VWAP"},
        }
        cfg = BacktestConfig.from_dict(d)
        assert cfg.fee.commission_bps == 0.8
        assert cfg.slicing.algo == "VWAP"

    def test_round_trip_dict(self):
        original = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            fee=FeeConfig(commission_bps=1.2, market="KOSDAQ"),
        )
        d = original.to_dict()
        restored = BacktestConfig.from_dict(d)
        assert restored.fee.commission_bps == 1.2
        assert restored.fee.market == "KOSDAQ"

    def test_yaml_round_trip(self):
        original = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            fee=FeeConfig(commission_bps=0.5),
            slicing=SlicingConfig(algo="POV", participation_rate=0.1),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "test_config.yaml"
            original.to_yaml(yaml_path)
            restored = BacktestConfig.from_yaml(yaml_path)

        assert restored.fee.commission_bps == 0.5
        assert restored.slicing.algo == "POV"
        assert restored.slicing.participation_rate == 0.1


class TestMerge:
    """config merge functionality을 테스트한다."""

    def test_merge_flat_override(self):
        base = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
        )
        merged = base.merge({"fee_model": "zero"})
        assert merged.fee_model == "zero"

    def test_merge_nested_override(self):
        base = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            fee=FeeConfig(commission_bps=1.5),
        )
        merged = base.merge({"fee": {"commission_bps": 0.8}})
        assert merged.fee.commission_bps == 0.8
        assert merged.fee.type == "krx"  # preserved

    def test_merge_preserves_unaffected(self):
        base = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            initial_cash=5e7,
            seed=123,
        )
        merged = base.merge({"fee_model": "zero"})
        assert merged.initial_cash == 5e7
        assert merged.seed == 123


# ---------------------------------------------------------------------------
# tick_size enforcement (single source-of-truth policy)
# ---------------------------------------------------------------------------

class TestTickSizeEnforcement:
    """tick_size == placement.tick_size invariant이 모든 경로에서 강제되는지 검증한다."""

    _BASE = {"symbol": "005930", "start_date": "2026-03-13", "end_date": "2026-03-13"}

    # ── A. 생성 경로 ──────────────────────────────────────────────────────

    def test_top_level_only_syncs_placement(self):
        """tick_size=5.0만 주면 placement.tick_size도 5.0이 된다."""
        cfg = BacktestConfig(**self._BASE, tick_size=5.0)
        assert cfg.tick_size == 5.0
        assert cfg.placement.tick_size == 5.0

    def test_placement_only_syncs_top_level(self):
        """placement.tick_size=5.0만 주면 top-level tick_size도 5.0이 된다."""
        cfg = BacktestConfig(
            **self._BASE,
            placement=PlacementConfig(style="passive", tick_size=5.0),
        )
        assert cfg.tick_size == 5.0
        assert cfg.placement.tick_size == 5.0

    def test_both_same_value_passes(self):
        """둘 다 5.0이면 통과하고 invariant가 유지된다."""
        cfg = BacktestConfig(
            **self._BASE,
            tick_size=5.0,
            placement=PlacementConfig(style="passive", tick_size=5.0),
        )
        assert cfg.tick_size == 5.0
        assert cfg.placement.tick_size == 5.0

    def test_both_different_raises_value_error(self):
        """tick_size=10.0, placement.tick_size=3.0이면 ValueError."""
        with pytest.raises(ValueError, match="tick_size"):
            BacktestConfig(
                **self._BASE,
                tick_size=10.0,
                placement=PlacementConfig(style="passive", tick_size=3.0),
            )

    def test_default_both_remain_1(self):
        """기본 생성 시 tick_size와 placement.tick_size 모두 1.0이다."""
        cfg = BacktestConfig(**self._BASE)
        assert cfg.tick_size == 1.0
        assert cfg.placement.tick_size == 1.0

    def test_top_level_syncs_placement_with_default(self):
        """placement에 기본값(1.0)만 있으면 top-level이 canonical이 된다."""
        cfg = BacktestConfig(
            **self._BASE,
            tick_size=10.0,
            placement=PlacementConfig(style="passive"),   # tick_size=1.0 (default)
        )
        assert cfg.tick_size == 10.0
        assert cfg.placement.tick_size == 10.0

    def test_invariant_always_holds_after_construction(self):
        """생성 후 항상 tick_size == placement.tick_size."""
        for ts in (1.0, 5.0, 100.0):
            cfg = BacktestConfig(**self._BASE, tick_size=ts)
            assert cfg.tick_size == cfg.placement.tick_size, (
                f"Invariant violated for tick_size={ts}"
            )

    # ── B. merge 경로 ─────────────────────────────────────────────────────

    def test_merge_top_level_propagates_to_placement(self):
        """top-level만 override하면 placement.tick_size도 따라간다."""
        base = BacktestConfig(**self._BASE, tick_size=5.0)
        merged = base.merge({"tick_size": 100.0})
        assert merged.tick_size == 100.0
        assert merged.placement.tick_size == 100.0

    def test_merge_placement_tick_size_propagates_to_top_level(self):
        """placement.tick_size만 override하면 top-level도 따라간다."""
        base = BacktestConfig(**self._BASE, tick_size=5.0)
        merged = base.merge({"placement": {"tick_size": 100.0}})
        assert merged.tick_size == 100.0
        assert merged.placement.tick_size == 100.0

    def test_merge_both_same_passes(self):
        """둘 다 같은 값으로 override하면 통과."""
        base = BacktestConfig(**self._BASE, tick_size=5.0)
        merged = base.merge({"tick_size": 10.0, "placement": {"tick_size": 10.0}})
        assert merged.tick_size == 10.0
        assert merged.placement.tick_size == 10.0

    def test_merge_both_different_raises_value_error(self):
        """둘 다 다르게 override하면 ValueError."""
        base = BacktestConfig(**self._BASE, tick_size=5.0)
        with pytest.raises(ValueError, match="tick_size"):
            base.merge({"tick_size": 10.0, "placement": {"tick_size": 3.0}})

    def test_merge_unrelated_preserves_tick_size(self):
        """tick_size와 무관한 override 후에도 invariant 유지."""
        base = BacktestConfig(**self._BASE, tick_size=5.0)
        merged = base.merge({"fee_model": "zero"})
        assert merged.tick_size == 5.0
        assert merged.placement.tick_size == 5.0

    # ── C. round-trip / from_dict 경로 ───────────────────────────────────

    def test_round_trip_preserves_tick_size(self):
        """to_dict → from_dict 후에도 tick_size == placement.tick_size."""
        original = BacktestConfig(**self._BASE, tick_size=7.0)
        restored = BacktestConfig.from_dict(original.to_dict())
        assert restored.tick_size == 7.0
        assert restored.placement.tick_size == 7.0

    def test_from_dict_split_config_raises_value_error(self):
        """from_dict에 top-level과 placement.tick_size가 다르게 주어지면 ValueError."""
        d = {
            **self._BASE,
            "tick_size": 10.0,
            "placement": {"style": "passive", "tick_size": 3.0},
        }
        with pytest.raises(ValueError, match="tick_size"):
            BacktestConfig.from_dict(d)

    def test_from_dict_top_level_only_syncs(self):
        """from_dict에 top-level만 있으면 placement도 동기화된다."""
        d = {**self._BASE, "tick_size": 5.0}
        cfg = BacktestConfig.from_dict(d)
        assert cfg.tick_size == 5.0
        assert cfg.placement.tick_size == 5.0

    def test_from_dict_placement_only_syncs_top_level(self):
        """from_dict에 placement.tick_size만 있으면 top-level도 동기화된다."""
        d = {
            **self._BASE,
            "placement": {"style": "passive", "tick_size": 5.0},
        }
        cfg = BacktestConfig.from_dict(d)
        assert cfg.tick_size == 5.0
        assert cfg.placement.tick_size == 5.0

    def test_from_dict_both_same_passes(self):
        """from_dict에 둘 다 같은 값이면 통과."""
        d = {
            **self._BASE,
            "tick_size": 5.0,
            "placement": {"style": "passive", "tick_size": 5.0},
        }
        cfg = BacktestConfig.from_dict(d)
        assert cfg.tick_size == 5.0
        assert cfg.placement.tick_size == 5.0

    def test_from_dict_string_coercion(self):
        """from_dict({"tick_size": "5.0", ...})가 float 5.0으로 복원된다."""
        d = {**self._BASE, "tick_size": "5.0"}
        cfg = BacktestConfig.from_dict(d)
        assert cfg.tick_size == 5.0
        assert isinstance(cfg.tick_size, float)

    # ── D. to_dict 포함 확인 ──────────────────────────────────────────────

    def test_to_dict_includes_tick_size(self):
        """to_dict() 결과에 tick_size가 포함된다."""
        cfg = BacktestConfig(**self._BASE, tick_size=5.0)
        d = cfg.to_dict()
        assert "tick_size" in d
        assert d["tick_size"] == 5.0

    def test_to_dict_placement_tick_size_consistent(self):
        """to_dict()의 top-level tick_size와 placement.tick_size가 같다."""
        cfg = BacktestConfig(**self._BASE, tick_size=5.0)
        d = cfg.to_dict()
        assert d["tick_size"] == d["placement"]["tick_size"]


# ---------------------------------------------------------------------------
# Residual fix tests: nested placement string coercion + ClassVar hygiene
# ---------------------------------------------------------------------------

class TestTickSizeResidualFixes:
    """잔여 버그 수정 검증."""

    _BASE = {"symbol": "005930", "start_date": "2026-03-13", "end_date": "2026-03-13"}

    # ── 1. nested placement string coercion ───────────────────────────────

    def test_nested_placement_string_tick_size_coerced_to_float(self):
        """from_dict에서 placement.tick_size가 문자열로 들어와도 float로 정규화된다."""
        d = {
            **self._BASE,
            "placement": {"style": "passive", "tick_size": "5.0"},
        }
        cfg = BacktestConfig.from_dict(d)
        assert cfg.tick_size == 5.0
        assert cfg.placement.tick_size == 5.0
        assert isinstance(cfg.tick_size, float)
        assert isinstance(cfg.placement.tick_size, float)

    def test_both_string_same_value_coerced_and_passes(self):
        """top-level과 placement 모두 같은 문자열로 들어와도 float로 정규화되고 통과한다."""
        d = {
            **self._BASE,
            "tick_size": "5.0",
            "placement": {"style": "passive", "tick_size": "5.0"},
        }
        cfg = BacktestConfig.from_dict(d)
        assert cfg.tick_size == 5.0
        assert cfg.placement.tick_size == 5.0
        assert isinstance(cfg.tick_size, float)
        assert isinstance(cfg.placement.tick_size, float)

    def test_placement_config_from_dict_string_tick_size(self):
        """PlacementConfig.from_dict에서 tick_size 문자열이 float로 변환된다."""
        p = PlacementConfig.from_dict({"style": "passive", "tick_size": "5.0"})
        assert p.tick_size == 5.0
        assert isinstance(p.tick_size, float)

    def test_tick_size_float_invariant_after_construction(self):
        """생성 후 tick_size와 placement.tick_size 모두 반드시 float 타입이다."""
        cfg = BacktestConfig(**self._BASE, tick_size=5.0)
        assert isinstance(cfg.tick_size, float)
        assert isinstance(cfg.placement.tick_size, float)

    # ── 2. ClassVar hygiene ────────────────────────────────────────────────

    def test_tick_size_default_not_in_dataclass_fields(self):
        """`_TICK_SIZE_DEFAULT`는 __dataclass_fields__에 포함되지 않아야 한다."""
        assert "_TICK_SIZE_DEFAULT" not in BacktestConfig.__dataclass_fields__

    def test_tick_size_default_not_in_constructor_signature(self):
        """`_TICK_SIZE_DEFAULT`는 BacktestConfig 생성자 파라미터에 없어야 한다."""
        import inspect
        sig = inspect.signature(BacktestConfig)
        assert "_TICK_SIZE_DEFAULT" not in sig.parameters
