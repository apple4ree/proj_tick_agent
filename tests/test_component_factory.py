"""
Tests for ComponentFactory.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from evaluation_orchestration.layer7_validation.backtest_config import (
    FeeConfig,
    ImpactConfig,
    LatencyConfig,
    ExchangeConfig,
    SlicingConfig,
    PlacementConfig,
    RiskConfig,
)
from evaluation_orchestration.layer7_validation.component_factory import ComponentFactory


class TestBuildFeeModel:
    """ComponentFactory.build_fee_model()을 테스트한다."""

    def test_krx_default(self):
        cfg = FeeConfig()
        model = ComponentFactory.build_fee_model(cfg)
        assert model.commission_bps == 1.5
        assert model.market == "KOSPI"

    def test_krx_custom_bps(self):
        cfg = FeeConfig(commission_bps=0.5, market="KOSDAQ", include_tax=False)
        model = ComponentFactory.build_fee_model(cfg)
        assert model.commission_bps == 0.5
        assert model.market == "KOSDAQ"
        assert not model.include_tax

    def test_zero_fee(self):
        cfg = FeeConfig(type="zero")
        model = ComponentFactory.build_fee_model(cfg)
        from market_simulation.layer5_simulator.fee_model import ZeroFeeModel
        assert isinstance(model, ZeroFeeModel)


class TestBuildImpactModel:
    """ComponentFactory.build_impact_model()을 테스트한다."""

    def test_linear_default(self):
        cfg = ImpactConfig()
        model = ComponentFactory.build_impact_model(cfg)
        assert model.eta == 0.1
        assert model.gamma == 0.01

    def test_linear_custom(self):
        cfg = ImpactConfig(type="linear", eta=0.2, gamma=0.02)
        model = ComponentFactory.build_impact_model(cfg)
        assert model.eta == 0.2
        assert model.gamma == 0.02

    def test_sqrt(self):
        cfg = ImpactConfig(type="sqrt", sigma=0.02, kappa=0.15)
        model = ComponentFactory.build_impact_model(cfg)
        from market_simulation.layer5_simulator.impact_model import SquareRootImpact
        assert isinstance(model, SquareRootImpact)
        assert model.sigma == 0.02
        assert model.kappa == 0.15

    def test_zero(self):
        cfg = ImpactConfig(type="zero")
        model = ComponentFactory.build_impact_model(cfg)
        from market_simulation.layer5_simulator.impact_model import ZeroImpact
        assert isinstance(model, ZeroImpact)


class TestBuildLatencyModel:
    """ComponentFactory.build_latency_model()을 테스트한다."""

    def test_default_profile(self):
        cfg = LatencyConfig()
        model = ComponentFactory.build_latency_model(cfg, seed=42)
        assert model.profile.order_submit_ms == 0.5  # 기본 프로필 값
        assert model.add_jitter is True

    def test_colocation_profile(self):
        cfg = LatencyConfig(profile="colocation")
        model = ComponentFactory.build_latency_model(cfg)
        assert model.profile.order_submit_ms == 0.05  # 코로케이션 프로필

    def test_retail_profile(self):
        cfg = LatencyConfig(profile="retail")
        model = ComponentFactory.build_latency_model(cfg)
        assert model.profile.order_submit_ms == 5.0  # 리테일 프로필

    def test_zero_profile(self):
        cfg = LatencyConfig(profile="zero")
        model = ComponentFactory.build_latency_model(cfg)
        assert model.profile.order_submit_ms == 0.0

    def test_field_override(self):
        cfg = LatencyConfig(profile="default", order_submit_ms=2.0)
        model = ComponentFactory.build_latency_model(cfg)
        assert model.profile.order_submit_ms == 2.0


class TestBuildMatchingEngine:
    """ComponentFactory.build_matching_engine()을 테스트한다."""

    def test_default_config(self):
        cfg = ExchangeConfig()
        engine = ComponentFactory.build_matching_engine(cfg)
        from market_simulation.layer5_simulator.matching_engine import ExchangeModel, QueueModel
        assert engine.exchange_model == ExchangeModel.PARTIAL_FILL
        assert engine.queue_model == QueueModel.PROB_QUEUE

    def test_no_partial_fill(self):
        cfg = ExchangeConfig(exchange_model="no_partial_fill")
        engine = ComponentFactory.build_matching_engine(cfg)
        from market_simulation.layer5_simulator.matching_engine import ExchangeModel
        assert engine.exchange_model == ExchangeModel.NO_PARTIAL_FILL

    def test_price_time_queue(self):
        cfg = ExchangeConfig(queue_model="price_time")
        engine = ComponentFactory.build_matching_engine(cfg)
        from market_simulation.layer5_simulator.matching_engine import QueueModel
        assert engine.queue_model == QueueModel.PROB_QUEUE

    def test_queue_model_none(self):
        cfg = ExchangeConfig(queue_model="none")
        engine = ComponentFactory.build_matching_engine(cfg)
        from market_simulation.layer5_simulator.matching_engine import QueueModel
        assert engine.queue_model == QueueModel.PROB_QUEUE

    def test_normalize_queue_model_unknown_defaults_prob_queue(self):
        assert ComponentFactory.normalize_queue_model("invalid") == "prob_queue"

    def test_queue_position(self):
        cfg = ExchangeConfig(queue_position_assumption=0.8)
        engine = ComponentFactory.build_matching_engine(cfg)
        assert engine.queue_position_assumption == 0.8


class TestBuildSlicer:
    """ComponentFactory.build_slicer()을 테스트한다."""

    def test_twap_default(self):
        cfg = SlicingConfig()
        slicer = ComponentFactory.build_slicer(cfg)
        assert slicer.name == "TWAP"
        assert slicer.interval_seconds == 30.0

    def test_twap_custom_interval(self):
        cfg = SlicingConfig(algo="TWAP", interval_seconds=60.0)
        slicer = ComponentFactory.build_slicer(cfg)
        assert slicer.interval_seconds == 60.0

    def test_vwap(self):
        cfg = SlicingConfig(algo="VWAP")
        slicer = ComponentFactory.build_slicer(cfg)
        assert slicer.name == "VWAP"

    def test_pov(self):
        cfg = SlicingConfig(algo="POV", participation_rate=0.1)
        slicer = ComponentFactory.build_slicer(cfg)
        assert slicer.name == "POV"
        assert slicer.participation_rate == 0.1

    def test_almgren_chriss(self):
        cfg = SlicingConfig(algo="AC", ac_eta=0.2, ac_gamma=0.02)
        slicer = ComponentFactory.build_slicer(cfg)
        assert slicer.name == "AlmgrenChriss"
        assert slicer.eta == 0.2
        assert slicer.gamma == 0.02


class TestBuildPlacementPolicy:
    """ComponentFactory.build_placement_policy()을 테스트한다."""

    def test_spread_adaptive_default(self):
        cfg = PlacementConfig()
        policy = ComponentFactory.build_placement_policy(cfg)
        assert policy.name == "SpreadAdaptivePlacement"

    def test_aggressive(self):
        cfg = PlacementConfig(style="aggressive", use_market_orders=True)
        policy = ComponentFactory.build_placement_policy(cfg)
        assert policy.name == "AggressivePlacement"
        assert policy.use_market_orders is True

    def test_passive(self):
        cfg = PlacementConfig(style="passive", offset_ticks=2, tick_size=10.0)
        policy = ComponentFactory.build_placement_policy(cfg)
        assert policy.name == "PassivePlacement"
        assert policy.offset_ticks == 2
        assert policy.tick_size == 10.0


class TestBuildRiskCaps:
    """ComponentFactory.build_risk_caps()을 테스트한다."""

    def test_default(self):
        cfg = RiskConfig()
        caps = ComponentFactory.build_risk_caps(cfg, initial_cash=1e8)
        assert caps.max_gross_notional == 1e8

    def test_custom_max_gross(self):
        cfg = RiskConfig(max_gross_notional=5e7)
        caps = ComponentFactory.build_risk_caps(cfg, initial_cash=1e8)
        assert caps.max_gross_notional == 5e7


class TestBuildTargetBuilder:
    """ComponentFactory.build_target_builder()을 테스트한다."""

    def test_default(self):
        cfg = RiskConfig()
        builder = ComponentFactory.build_target_builder(cfg)
        assert builder._mode == "signal_proportional"
        assert builder._max_position == 1000
        assert builder._default_size == 100

    def test_kelly_mode(self):
        cfg = RiskConfig(target_mode="Kelly", max_position=500)
        builder = ComponentFactory.build_target_builder(cfg)
        assert builder._mode == "Kelly"
        assert builder._max_position == 500
