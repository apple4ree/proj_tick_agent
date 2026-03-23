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
    ImpactConfig,
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
        assert cfg.impact.type == "linear"

    def test_flat_fields_propagate_to_nested(self):
        cfg = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            fee_model="zero",
            impact_model="sqrt",
            slicing_algo="POV",
            placement_style="aggressive",
        )
        assert cfg.fee.type == "zero"
        assert cfg.impact.type == "sqrt"
        assert cfg.slicing.algo == "POV"
        assert cfg.placement.style == "aggressive"


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

    def test_nested_impact_config(self):
        cfg = BacktestConfig(
            symbol="005930",
            start_date="2026-03-13",
            end_date="2026-03-13",
            impact=ImpactConfig(type="sqrt", sigma=0.02, kappa=0.15),
        )
        assert cfg.impact.type == "sqrt"
        assert cfg.impact.sigma == 0.02
        assert cfg.impact.kappa == 0.15

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

    def test_invalid_impact_type_raises(self):
        with pytest.raises(ValueError, match="impact.type must be"):
            BacktestConfig(
                symbol="005930",
                start_date="2026-03-13",
                end_date="2026-03-13",
                impact=ImpactConfig(type="unknown"),
            )

    def test_invalid_slicing_algo_raises(self):
        with pytest.raises(ValueError, match="slicing.algo must be"):
            BacktestConfig(
                symbol="005930",
                start_date="2026-03-13",
                end_date="2026-03-13",
                slicing=SlicingConfig(algo="invalid"),
            )

    def test_invalid_initial_cash_raises(self):
        with pytest.raises(ValueError, match="initial_cash must be > 0"):
            BacktestConfig(
                symbol="005930",
                start_date="2026-03-13",
                end_date="2026-03-13",
                initial_cash=0,
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
        assert d["impact"]["type"] == "linear"

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
            impact=ImpactConfig(type="sqrt", sigma=0.03),
        )
        d = original.to_dict()
        restored = BacktestConfig.from_dict(d)
        assert restored.fee.commission_bps == 1.2
        assert restored.fee.market == "KOSDAQ"
        assert restored.impact.sigma == 0.03

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
