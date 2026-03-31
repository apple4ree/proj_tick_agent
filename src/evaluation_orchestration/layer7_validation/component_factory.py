"""
component_factory.py
--------------------
Factory for building simulation components from nested config objects.

Replaces the hardcoded component construction in PipelineRunner._setup_components()
with config-driven instantiation. Each static method builds a single component
from its corresponding sub-config dataclass.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from execution_planning.layer2_position import RiskCaps, TargetBuilder
    from execution_planning.layer4_execution import SlicingPolicy, PlacementPolicy
    from market_simulation.layer5_simulator import FeeModel, ImpactModel, LatencyModel, MatchingEngine
    from evaluation_orchestration.layer7_validation.backtest_config import (
        FeeConfig, ImpactConfig, LatencyConfig, ExchangeConfig,
        SlicingConfig, PlacementConfig, RiskConfig,
    )


class ComponentFactory:
    """
    Static factory methods for building simulation components from config objects.

    Each method takes a sub-config dataclass and returns a fully configured
    component instance. This centralizes component construction and ensures
    config parameters are properly threaded through.
    """

    # ------------------------------------------------------------------
    # 수수료 모델
    # ------------------------------------------------------------------

    @staticmethod
    def build_fee_model(cfg: "FeeConfig") -> "FeeModel":
        """
        Build a fee model from FeeConfig.

        매개변수
        ----------
        cfg : FeeConfig
            Fee configuration with type, commission_bps, market, include_tax.

        반환값
        -------
        FeeModel
            KRXFeeModel or ZeroFeeModel instance.
        """
        from market_simulation.layer5_simulator.fee_model import KRXFeeModel, ZeroFeeModel

        if cfg.type == "zero":
            return ZeroFeeModel()

        return KRXFeeModel(
            commission_bps=cfg.commission_bps,
            market=cfg.market,
            include_tax=cfg.include_tax,
        )

    # ------------------------------------------------------------------
    # Impact Model
    # ------------------------------------------------------------------

    @staticmethod
    def build_impact_model(cfg: "ImpactConfig") -> "ImpactModel":
        """
        Build a market impact model from ImpactConfig.

        매개변수
        ----------
        cfg : ImpactConfig
            Impact configuration with type, eta, gamma, sigma, kappa.

        반환값
        -------
        ImpactModel
            LinearImpact, SquareRootImpact, or ZeroImpact instance.
        """
        from market_simulation.layer5_simulator.impact_model import LinearImpact, SquareRootImpact, ZeroImpact

        if cfg.type == "zero":
            return ZeroImpact()

        if cfg.type == "sqrt":
            return SquareRootImpact(
                sigma=cfg.sigma,
                kappa=cfg.kappa,
                gamma=cfg.gamma,
            )

        # Default: linear
        return LinearImpact(
            eta=cfg.eta,
            gamma=cfg.gamma,
        )

    # ------------------------------------------------------------------
    # 지연 Model
    # ------------------------------------------------------------------

    @staticmethod
    def build_latency_model(cfg: "LatencyConfig", seed: int | None = None) -> "LatencyModel":
        """
        Build a latency model from LatencyConfig.

        매개변수
        ----------
        cfg : LatencyConfig
            지연 configuration with profile and optional per-field overrides.
        seed : int | None
            Random seed for jitter sampling.

        반환값
        -------
        LatencyModel
            Configured LatencyModel instance.
        """
        from market_simulation.layer5_simulator.latency_model import LatencyModel, LatencyProfile

        # Select base profile
        profile_builders = {
            "zero": LatencyProfile.zero,
            "colocation": LatencyProfile.colocation,
            "retail": LatencyProfile.retail,
        }

        if cfg.profile in profile_builders:
            profile = profile_builders[cfg.profile]()
        else:
            # "default" or unknown → use LatencyProfile default constructor
            profile = LatencyProfile()

        # Apply per-field overrides if specified
        if cfg.order_submit_ms is not None:
            profile.order_submit_ms = cfg.order_submit_ms
        if cfg.order_ack_ms is not None:
            profile.order_ack_ms = cfg.order_ack_ms
        if cfg.cancel_ms is not None:
            profile.cancel_ms = cfg.cancel_ms
        if cfg.market_data_delay_ms is not None:
            profile.market_data_delay_ms = cfg.market_data_delay_ms

        return LatencyModel(
            profile=profile,
            add_jitter=cfg.add_jitter,
            jitter_std_ms=cfg.jitter_std_ms,
            seed=seed,
        )

    # ------------------------------------------------------------------
    # Matching Engine
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_queue_model(queue_model: str | None) -> str:
        """Normalize queue model name for matching/fill components."""
        return "prob_queue"

    @staticmethod
    def build_matching_engine(cfg: "ExchangeConfig", seed: int | None = None) -> "MatchingEngine":
        """
        Build a matching engine from ExchangeConfig.

        매개변수
        ----------
        cfg : ExchangeConfig
            Exchange configuration with exchange_model, queue_model, queue_position.
        seed : int | None
            Random seed for stochastic queue models.

        반환값
        -------
        MatchingEngine
            Configured MatchingEngine instance.
        """
        from market_simulation.layer5_simulator.matching_engine import MatchingEngine, ExchangeModel, QueueModel

        # Parse exchange model
        if cfg.exchange_model == "no_partial_fill":
            exchange_model = ExchangeModel.NO_PARTIAL_FILL
        else:
            exchange_model = ExchangeModel.PARTIAL_FILL

        # Parse queue model
        queue_model_map = {
            "none": QueueModel.NONE,
            "price_time": QueueModel.PRICE_TIME,
            "risk_adverse": QueueModel.RISK_ADVERSE,
            "prob_queue": QueueModel.PROB_QUEUE,
            "pro_rata": QueueModel.PRO_RATA,
            "random": QueueModel.RANDOM,
        }
        queue_model_name = ComponentFactory.normalize_queue_model(cfg.queue_model)
        queue_model = queue_model_map.get(queue_model_name, QueueModel.PROB_QUEUE)

        return MatchingEngine(
            exchange_model=exchange_model,
            queue_model=queue_model,
            queue_position_assumption=cfg.queue_position_assumption,
            rng_seed=seed,
        )

    # ------------------------------------------------------------------
    # Slicing Policy
    # ------------------------------------------------------------------

    @staticmethod
    def build_slicer(cfg: "SlicingConfig") -> "SlicingPolicy":
        """
        Build a slicing policy from SlicingConfig.

        매개변수
        ----------
        cfg : SlicingConfig
            Slicing configuration with algo and algo-specific parameters.

        반환값
        -------
        SlicingPolicy
            TWAPSlicer, VWAPSlicer, POVSlicer, or AlmgrenChrissSlicer instance.
        """
        from execution_planning.layer4_execution.slicing_policy import (
            TWAPSlicer, VWAPSlicer, POVSlicer, AlmgrenChrissSlicer,
        )

        algo = cfg.algo.upper()

        if algo == "VWAP":
            return VWAPSlicer()

        if algo == "POV":
            return POVSlicer(participation_rate=cfg.participation_rate)

        if algo in {"AC", "ALMGREN_CHRISS", "ALMGRENCHRISS"}:
            return AlmgrenChrissSlicer(
                eta=cfg.ac_eta,
                gamma=cfg.ac_gamma,
                sigma=cfg.ac_sigma,
                T=cfg.ac_T,
            )

        # Default: TWAP
        return TWAPSlicer(interval_seconds=cfg.interval_seconds)

    # ------------------------------------------------------------------
    # 배치 Policy
    # ------------------------------------------------------------------

    @staticmethod
    def build_placement_policy(cfg: "PlacementConfig") -> "PlacementPolicy":
        """
        Build a placement policy from PlacementConfig.

        매개변수
        ----------
        cfg : PlacementConfig
            배치 configuration with style and style-specific parameters.

        반환값
        -------
        PlacementPolicy
            AggressivePlacement, PassivePlacement, or SpreadAdaptivePlacement.
        """
        from execution_planning.layer4_execution.placement_policy import (
            AggressivePlacement, PassivePlacement, SpreadAdaptivePlacement,
        )

        style = cfg.style.lower()

        if style == "aggressive":
            return AggressivePlacement(use_market_orders=cfg.use_market_orders)

        if style == "passive":
            return PassivePlacement(
                offset_ticks=cfg.offset_ticks,
                tick_size=cfg.tick_size,
            )

        # Default: spread_adaptive
        return SpreadAdaptivePlacement(
            aggression_spread_threshold_bps=cfg.aggression_spread_threshold_bps,
            imbalance_threshold=cfg.imbalance_threshold,
        )

    # ------------------------------------------------------------------
    # Risk Caps
    # ------------------------------------------------------------------

    @staticmethod
    def build_risk_caps(cfg: "RiskConfig", initial_cash: float) -> "RiskCaps":
        """
        Build risk caps from RiskConfig.

        매개변수
        ----------
        cfg : RiskConfig
            Risk configuration with max_gross_notional, etc.
        initial_cash : float
            Initial portfolio cash (used as default for max_gross_notional).

        반환값
        -------
        RiskCaps
            Configured RiskCaps instance.
        """
        from execution_planning.layer2_position.risk_caps import RiskCaps

        return RiskCaps(
            max_gross_notional=cfg.max_gross_notional or initial_cash,
        )

    # ------------------------------------------------------------------
    # Target Builder
    # ------------------------------------------------------------------

    @staticmethod
    def build_target_builder(cfg: "RiskConfig") -> "TargetBuilder":
        """
        Build a target builder from RiskConfig.

        매개변수
        ----------
        cfg : RiskConfig
            Risk configuration containing target_mode, max_position, default_size.

        반환값
        -------
        TargetBuilder
            Configured TargetBuilder instance.
        """
        from execution_planning.layer2_position.target_builder import TargetBuilder

        return TargetBuilder(
            mode=cfg.target_mode,
            max_position=cfg.max_position,
            default_size=cfg.default_size,
        )
