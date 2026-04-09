"""
backtest_config.py
------------------
Configuration and result data classes for backtest runs.

Supports both flat (backward-compatible) and nested (qlib-style) configuration.
Nested configs allow fine-grained parameter control via YAML files.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Nested sub-config dataclasses (qlib-style)
# ---------------------------------------------------------------------------

@dataclass
class FeeConfig:
    """Transaction fee model configuration."""
    type: str = "krx"            # krx | zero
    commission_bps: float = 1.5  # bps (both buy and sell)
    market: str = "KOSPI"        # KOSPI | KOSDAQ
    include_tax: bool = True     # Include securities transaction tax on sells

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> FeeConfig:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class LatencyConfig:
    """지연 model configuration."""
    profile: str = "default"                    # default | zero | colocation | retail
    order_submit_ms: float | None = None        # Override profile value
    order_ack_ms: float | None = None
    cancel_ms: float | None = None
    market_data_delay_ms: float | None = None
    add_jitter: bool = True
    jitter_std_ms: float = 0.1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> LatencyConfig:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ExchangeConfig:
    """Exchange simulation configuration.

    Queue model (handled exclusively by FillSimulator):
      - prob_queue : trade + partial depth-drop credit (KRX FIFO with cancel credit)
    """
    exchange_model: str = "partial_fill"        # partial_fill | no_partial_fill
    queue_model: str = "prob_queue"             # fixed: prob_queue only
    queue_position_assumption: float = 0.5       # Assumed queue position (0.0 = front, 1.0 = back)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ExchangeConfig:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class SlicingConfig:
    """Order slicing algorithm configuration."""
    algo: str = "TWAP"                  # TWAP | VWAP | POV | AC (Almgren-Chriss)
    interval_seconds: float = 30.0      # TWAP slice interval
    participation_rate: float = 0.05   # POV participation rate
    # Almgren-Chriss parameters
    ac_eta: float = 0.1
    ac_gamma: float = 0.01
    ac_sigma: float = 0.01
    ac_T: int = 100

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SlicingConfig:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PlacementConfig:
    """Order placement policy configuration."""
    style: str = "spread_adaptive"              # spread_adaptive | aggressive | passive | midpoint
    aggression_spread_threshold_bps: float = 5.0
    imbalance_threshold: float = 0.3
    use_market_orders: bool = False             # AggressivePlacement
    offset_ticks: int = 0                       # PassivePlacement
    tick_size: float = 1.0                      # PassivePlacement

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> PlacementConfig:
        coerce: dict[str, type] = {
            "aggression_spread_threshold_bps": float,
            "imbalance_threshold": float,
            "offset_ticks": int,
            "tick_size": float,
        }
        filtered = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        for field_name, type_fn in coerce.items():
            if field_name in filtered:
                filtered[field_name] = type_fn(filtered[field_name])
        return cls(**filtered)


@dataclass
class RiskConfig:
    """Risk limits and target sizing configuration."""
    max_gross_notional: float | None = None     # Defaults to initial_cash
    max_position: int = 1000                    # Maximum position size (shares)
    default_size: int = 100                     # Default order size
    target_mode: str = "signal_proportional"   # signal_proportional | fixed_size | Kelly

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> RiskConfig:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# Module-level constant: matches PlacementConfig.tick_size default.
# Defined here (not inside BacktestConfig) so it never appears as a dataclass field.
_PLACEMENT_TICK_SIZE_DEFAULT: float = 1.0


# ---------------------------------------------------------------------------
# Main BacktestConfig
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    """
    Full configuration for one backtest run.

    Supports both flat construction (backward compatible) and nested config
    construction (qlib-style). When nested configs are None, they are
    automatically synthesized from flat fields in __post_init__.

    속성
    ----------
    symbol : str
        Primary instrument symbol (e.g. '005930').
    start_date : str
        Inclusive start date, e.g. '2023-01-02'.
    end_date : str
        Inclusive end date, e.g. '2023-12-29'.
    initial_cash : float
        Starting cash in KRW.
    seed : int
        Global random seed for reproducibility.
    slicing_algo : str
        'TWAP' | 'VWAP' | 'POV' | 'AC' (flat, deprecated in favor of slicing.algo)
    placement_style : str
        'spread_adaptive' | 'aggressive' | 'passive' | 'midpoint' (flat)
    latency_ms : float
        Compatibility alias for venue lifecycle latency components (flat).
        Used only when nested `latency` config is absent (`latency is None`).
        In that legacy shorthand path, it populates:
        - `latency.order_submit_ms`
        - `latency.order_ack_ms`
        - `latency.cancel_ms`
        Once nested `latency` is present (profile-only / partial / full),
        this alias is fully disabled.
    fee_model : str
        'krx' | 'zero' | 'flat_bps' (flat)
    exchange_model : str
        'partial_fill' | 'no_partial_fill' (flat)
    queue_model : str
        'none' | 'prob_queue' | 'risk_adverse' | 'price_time' | 'pro_rata' | 'random' (flat)
    queue_position_assumption : float
        Queue ahead percentile for probabilistic queue advancement (flat).
    compute_attribution : bool
        Whether to run the full attribution analysis (slower).
    annualization_factor : int
        Trading days per year for risk metric annualization.

    Nested Configs (optional, override flat fields when provided)
    -------------------------------------------------------------
    fee : FeeConfig
    latency : LatencyConfig
    exchange : ExchangeConfig
    slicing : SlicingConfig
    placement : PlacementConfig
    risk : RiskConfig
    """
    # --- Required ---
    symbol: str
    start_date: str
    end_date: str

    # --- Top-level scalars ---
    initial_cash: float = 1e8
    seed: int = 42

    # --- Flat fields (backward compat) ---
    slicing_algo: str = "TWAP"
    placement_style: str = "spread_adaptive"
    latency_ms: float = 1.0
    fee_model: str = "krx"
    exchange_model: str = "partial_fill"
    queue_model: str = "prob_queue"
    queue_position_assumption: float = 0.5
    # Observation lag: strategy sees market data delayed by this amount (ms).
    # 0.0 = no delay (current behavior).  When > 0, PipelineRunner performs
    # actual past-state lookup (not a timestamp shift).
    # Meaningful only when the state stream resolution is fine enough:
    #   - "1s"    : small delays (< 1000ms) often collapse to same state
    #   - "500ms" : moderate delays (>= 200ms) yield distinct observed_state
    market_data_delay_ms: float = 0.0
    # Decision latency: how long the strategy takes to compute an action
    # after observing market data (ms).  0.0 = instant decision.
    # Separate from observation lag (which state is seen) and order submission
    # latency (how long the venue takes to receive the order).
    # Effective state lookup delay = market_data_delay_ms + decision_compute_ms.
    decision_compute_ms: float = 0.0

    compute_attribution: bool = True
    annualization_factor: int = 252
    tick_size: float = 1.0

    # --- Nested configs (qlib-style) ---
    fee: FeeConfig | None = field(default=None)
    latency: LatencyConfig | None = field(default=None)
    exchange: ExchangeConfig | None = field(default=None)
    slicing: SlicingConfig | None = field(default=None)
    placement: PlacementConfig | None = field(default=None)
    risk: RiskConfig | None = field(default=None)
    
    
    @staticmethod
    def latency_alias_components(latency_ms: float) -> tuple[float, float, float]:
        """Map flat latency_ms (compatibility alias) to nested venue latencies.

        This mapping only applies to venue lifecycle latency fields:
        - order_submit_ms
        - order_ack_ms
        - cancel_ms

        Observation-lag semantics (`market_data_delay_ms`) are intentionally
        not derived from this alias.
        """
        base = max(0.0, float(latency_ms))
        return (base * 0.3, base * 0.7, base * 0.2)

    def __post_init__(self) -> None:
        self._resolve_configs()
        self._validate()

    def _unify_tick_size(self) -> None:
        """Enforce the canonical invariant: self.tick_size == self.placement.tick_size.

        Priority rules (placement must already exist when this is called):
          1. Both provided, different, both non-default  → ValueError (split contract)
          2. placement.tick_size is non-default, top-level is default → placement wins
          3. Otherwise (top-level is non-default, or both are default)  → top-level wins

        After resolution both self.tick_size and self.placement.tick_size are
        normalised to float canonical form.
        """
        t_ts = float(self.tick_size)
        p_ts = float(self.placement.tick_size)
        default = _PLACEMENT_TICK_SIZE_DEFAULT

        if t_ts != p_ts:
            if t_ts != default and p_ts != default:
                raise ValueError(
                    f"tick_size conflict: top-level tick_size={t_ts} and "
                    f"placement.tick_size={p_ts} must match. "
                    "Set one of them or provide the same value for both."
                )
            if p_ts != default:
                # placement carries an explicit non-default value; adopt it as canonical
                t_ts = p_ts
            else:
                # top-level is canonical (or both are default); sync placement
                p_ts = t_ts

        # Always write back float canonical form to both sides
        self.tick_size = t_ts
        self.placement.tick_size = p_ts

    def _resolve_configs(self) -> None:
        """Synthesize nested configs from flat fields if not provided."""
        if self.fee is None:
            self.fee = FeeConfig(type=self.fee_model)

        alias_submit_ms, alias_ack_ms, alias_cancel_ms = self.latency_alias_components(self.latency_ms)
        self._latency_alias_applied = False
        if self.latency is None:
            # Legacy shorthand path: apply flat latency_ms alias only when
            # nested latency config is completely absent.
            self.latency = LatencyConfig(
                order_submit_ms=alias_submit_ms,
                order_ack_ms=alias_ack_ms,
                cancel_ms=alias_cancel_ms,
            )
            self._latency_alias_applied = True
        else:
            # Canonical precedence: once nested latency is present, flat
            # latency_ms alias is fully disabled (even for None fields).
            self._latency_alias_applied = False
        if self.exchange is None:
            self.exchange = ExchangeConfig(
                exchange_model=self.exchange_model,
                queue_model=self.queue_model,
                queue_position_assumption=self.queue_position_assumption,
            )
        if self.slicing is None:
            self.slicing = SlicingConfig(algo=self.slicing_algo)
        if self.placement is None:
            self.tick_size = float(self.tick_size)
            self.placement = PlacementConfig(style=self.placement_style, tick_size=self.tick_size)
        else:
            self._unify_tick_size()
        if self.risk is None:
            self.risk = RiskConfig()

        # Sync max_gross_notional default
        if self.risk.max_gross_notional is None:
            self.risk.max_gross_notional = self.initial_cash

    def _validate(self) -> None:
        """Validate configuration values."""
        errors: list[str] = []

        # Fee config
        if self.fee.type not in {"krx", "zero"}:
            errors.append(f"fee.type must be 'krx' or 'zero', got '{self.fee.type}'")
        if self.fee.market not in {"KOSPI", "KOSDAQ"}:
            errors.append(f"fee.market must be 'KOSPI' or 'KOSDAQ', got '{self.fee.market}'")
        if self.fee.commission_bps < 0:
            errors.append(f"fee.commission_bps must be >= 0, got {self.fee.commission_bps}")

        # 지연 config
        if self.latency.profile not in {"default", "zero", "colocation", "retail"}:
            errors.append(f"latency.profile must be 'default', 'zero', 'colocation', or 'retail', got '{self.latency.profile}'")
        if self.latency.order_submit_ms is not None and self.latency.order_submit_ms < 0:
            errors.append(f"latency.order_submit_ms must be >= 0, got {self.latency.order_submit_ms}")
        if self.latency.order_ack_ms is not None and self.latency.order_ack_ms < 0:
            errors.append(f"latency.order_ack_ms must be >= 0, got {self.latency.order_ack_ms}")
        if self.latency.cancel_ms is not None and self.latency.cancel_ms < 0:
            errors.append(f"latency.cancel_ms must be >= 0, got {self.latency.cancel_ms}")
        if self.latency.jitter_std_ms < 0:
            errors.append(f"latency.jitter_std_ms must be >= 0, got {self.latency.jitter_std_ms}")

        # Exchange config
        if self.exchange.exchange_model not in {"partial_fill", "no_partial_fill"}:
            errors.append(f"exchange.exchange_model must be 'partial_fill' or 'no_partial_fill', got '{self.exchange.exchange_model}'")
        valid_queue = {"prob_queue"}
        if self.exchange.queue_model not in valid_queue:
            errors.append(f"exchange.queue_model must be 'prob_queue', got '{self.exchange.queue_model}'")
        if not 0.0 <= self.exchange.queue_position_assumption <= 1.0:
            errors.append(
                "exchange.queue_position_assumption must be in [0, 1], "
                f"got {self.exchange.queue_position_assumption}"
            )

        # Slicing config
        if self.slicing.algo.upper() not in {"TWAP", "VWAP", "POV", "AC"}:
            errors.append(f"slicing.algo must be 'TWAP', 'VWAP', 'POV', or 'AC', got '{self.slicing.algo}'")

        # 배치 config
        if self.placement.style not in {"spread_adaptive", "aggressive", "passive", "midpoint"}:
            errors.append(f"placement.style must be 'spread_adaptive', 'aggressive', 'passive', or 'midpoint', got '{self.placement.style}'")

        # Risk config
        if self.risk.target_mode not in {"signal_proportional", "fixed_size", "Kelly"}:
            errors.append(f"risk.target_mode must be 'signal_proportional', 'fixed_size', or 'Kelly', got '{self.risk.target_mode}'")

        # Top-level
        if self.initial_cash <= 0:
            errors.append(f"initial_cash must be > 0, got {self.initial_cash}")
        if self.seed < 0:
            errors.append(f"seed must be >= 0, got {self.seed}")
        if self.latency_ms < 0:
            errors.append(f"latency_ms must be >= 0, got {self.latency_ms}")
        if self.market_data_delay_ms < 0:
            errors.append(f"market_data_delay_ms must be >= 0, got {self.market_data_delay_ms}")
        if self.decision_compute_ms < 0:
            errors.append(f"decision_compute_ms must be >= 0, got {self.decision_compute_ms}")

        if errors:
            raise ValueError("BacktestConfig validation failed:\n  - " + "\n  - ".join(errors))

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize to dict including nested configs."""
        return {
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "initial_cash": self.initial_cash,
            "seed": self.seed,
            "slicing_algo": self.slicing_algo,
            "placement_style": self.placement_style,
            "latency_ms": self.latency_ms,
            "fee_model": self.fee_model,
            "exchange_model": self.exchange_model,
            "queue_model": self.queue_model,
            "queue_position_assumption": self.queue_position_assumption,
            "market_data_delay_ms": self.market_data_delay_ms,
            "decision_compute_ms": self.decision_compute_ms,
            "latency_alias_applied": bool(getattr(self, "_latency_alias_applied", False)),
            "compute_attribution": self.compute_attribution,
            "annualization_factor": self.annualization_factor,
            "tick_size": self.tick_size,
            # Nested configs
            "fee": self.fee.to_dict() if self.fee else None,
            "latency": self.latency.to_dict() if self.latency else None,
            "exchange": self.exchange.to_dict() if self.exchange else None,
            "slicing": self.slicing.to_dict() if self.slicing else None,
            "placement": self.placement.to_dict() if self.placement else None,
            "risk": self.risk.to_dict() if self.risk else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> BacktestConfig:
        """
        Create BacktestConfig from dict.

        Handles both flat-only dicts and dicts with nested config sections.
        """
        d = copy.deepcopy(d)

        # Type coercion for numeric fields that may come as strings from YAML
        numeric_fields = {
            "initial_cash": float,
            "seed": int,
            "latency_ms": float,
            "annualization_factor": int,
            "queue_position_assumption": float,
            "market_data_delay_ms": float,
            "decision_compute_ms": float,
            "tick_size": float,
        }
        for field_name, type_fn in numeric_fields.items():
            if field_name in d and isinstance(d[field_name], str):
                d[field_name] = type_fn(d[field_name])

        # Extract nested configs if present
        nested_keys = ["fee", "latency", "exchange", "slicing", "placement", "risk"]
        nested: dict[str, Any] = {}
        for key in nested_keys:
            if key in d and isinstance(d[key], dict):
                nested[key] = d.pop(key)

        # Build nested config objects
        if "fee" in nested:
            d["fee"] = FeeConfig.from_dict(nested["fee"])
        if "latency" in nested:
            d["latency"] = LatencyConfig.from_dict(nested["latency"])
        if "exchange" in nested:
            d["exchange"] = ExchangeConfig.from_dict(nested["exchange"])
        if "slicing" in nested:
            d["slicing"] = SlicingConfig.from_dict(nested["slicing"])
        if "placement" in nested:
            d["placement"] = PlacementConfig.from_dict(nested["placement"])
        if "risk" in nested:
            d["risk"] = RiskConfig.from_dict(nested["risk"])

        # Filter to valid fields only
        valid_fields = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in d.items() if k in valid_fields}

        return cls(**filtered)

    @classmethod
    def from_yaml(cls, path: str | Path) -> BacktestConfig:
        """Load BacktestConfig from a YAML file."""
        path = Path(path)
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    def to_yaml(self, path: str | Path) -> None:
        """Save BacktestConfig to a YAML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def merge(self, overrides: dict) -> BacktestConfig:
        """
        Create a new BacktestConfig with overrides applied.

        Supports both flat overrides (e.g., {"fee_model": "zero"})
        and nested overrides (e.g., {"fee": {"commission_bps": 0.5}}).

        tick_size invariant: merged.tick_size == merged.placement.tick_size always.
        Rules:
          - Only top-level overridden  → placement.tick_size set to same value.
          - Only placement.tick_size overridden → top-level set to same value.
          - Both overridden, same value → fine.
          - Both overridden, different values → ValueError.
        """
        # Detect tick_size in overrides before merging
        top_ts_override = overrides.get("tick_size")
        placement_override_dict = overrides.get("placement")
        nested_ts_override = (
            placement_override_dict.get("tick_size")
            if isinstance(placement_override_dict, dict)
            else None
        )

        if top_ts_override is not None and nested_ts_override is not None:
            if float(top_ts_override) != float(nested_ts_override):
                raise ValueError(
                    f"merge(): tick_size conflict: top-level={top_ts_override}, "
                    f"placement.tick_size={nested_ts_override}. "
                    "Provide the same value or omit one."
                )

        base = self.to_dict()

        for key, value in overrides.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                # Deep merge nested configs
                base[key].update(value)
            else:
                base[key] = value

        # Propagate canonical tick_size to the other side
        if top_ts_override is not None and nested_ts_override is None:
            if isinstance(base.get("placement"), dict):
                base["placement"]["tick_size"] = float(top_ts_override)
        elif nested_ts_override is not None and top_ts_override is None:
            base["tick_size"] = float(nested_ts_override)

        return BacktestConfig.from_dict(base)


# ---------------------------------------------------------------------------
# BacktestResult (unchanged)
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    """Aggregated output of a completed backtest run."""
    config: BacktestConfig
    run_id: str
    pnl_report: "PnLReport"
    risk_report: "RiskReport"
    execution_report: "ExecutionReport"
    turnover_report: "TurnoverReport"
    attribution_report: "AttributionReport | None"
    n_fills: int
    n_states: int
    metadata: dict = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        """Flat dict of key metrics suitable for logging or comparison tables."""
        result: dict[str, Any] = {
            "n_fills": float(self.n_fills),
            "n_states": float(self.n_states),
        }

        result.update({
            "total_realized_pnl": self.pnl_report.total_realized,
            "total_unrealized_pnl": self.pnl_report.total_unrealized,
            "net_pnl": self.pnl_report.net_pnl,
            "total_commission": self.pnl_report.total_commission,
            "total_slippage": self.pnl_report.total_slippage,
            "total_impact": self.pnl_report.total_impact,
        })

        result.update({
            "sharpe_ratio": self.risk_report.sharpe_ratio,
            "sortino_ratio": self.risk_report.sortino_ratio,
            "calmar_ratio": self.risk_report.calmar_ratio,
            "max_drawdown": self.risk_report.max_drawdown,
            "max_drawdown_duration": float(self.risk_report.max_drawdown_duration),
            "annualized_vol": self.risk_report.annualized_vol,
            "var_95": self.risk_report.var_95,
            "expected_shortfall_95": self.risk_report.expected_shortfall_95,
        })

        result.update({
            "fill_rate": self.execution_report.fill_rate,
            "cancel_rate": self.execution_report.cancel_rate,
            "is_bps": self.execution_report.implementation_shortfall_bps,
            "vwap_diff_bps": self.execution_report.vwap_diff_bps,
            "avg_slippage_bps": self.execution_report.avg_slippage_bps,
            "avg_market_impact_bps": self.execution_report.avg_market_impact_bps,
            "timing_score": self.execution_report.timing_score,
            "partial_fill_rate": self.execution_report.partial_fill_rate,
            "maker_fill_ratio": self.execution_report.maker_fill_ratio,
            "avg_latency_ms": self.execution_report.avg_latency_ms,
        })

        result.update({
            "annualized_turnover": self.turnover_report.annualized_turnover,
            "avg_holding_period": self.turnover_report.avg_holding_period,
            "iqm_return": self.turnover_report.iqm_return,
        })

        if self.attribution_report is not None:
            result.update({
                "alpha_contribution": self.attribution_report.alpha_contribution,
                "execution_contribution": self.attribution_report.execution_contribution,
                "cost_contribution": self.attribution_report.cost_contribution,
                "timing_contribution": self.attribution_report.timing_contribution,
                "alpha_fraction": self.attribution_report.alpha_fraction,
            })

        diagnostics = self.metadata.get("realism_diagnostics", {})
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        observation_lag = diagnostics.get("observation_lag", self.metadata.get("observation_lag", {}))
        decision_latency = diagnostics.get("decision_latency", self.metadata.get("decision_latency", {}))
        tick_time = diagnostics.get("tick_time", self.metadata.get("tick_time", {}))
        lifecycle = diagnostics.get("lifecycle", self.metadata.get("lifecycle", {}))
        queue = diagnostics.get("queue", self.metadata.get("queue", {}))
        latency = diagnostics.get("latency", self.metadata.get("latency", {}))

        if isinstance(observation_lag, dict):
            result["resample_interval"] = observation_lag.get("resample_interval")
            result["canonical_tick_interval_ms"] = observation_lag.get("canonical_tick_interval_ms")
            result["configured_market_data_delay_ms"] = observation_lag.get("configured_market_data_delay_ms")
            result["avg_observation_staleness_ms"] = observation_lag.get("avg_observation_staleness_ms")
            result["effective_delay_ms"] = observation_lag.get("effective_delay_ms")
            result["state_history_max_len"] = observation_lag.get("state_history_max_len")
            result["strategy_runtime_lookback_ticks"] = observation_lag.get("strategy_runtime_lookback_ticks")

        if isinstance(decision_latency, dict):
            result["configured_decision_compute_ms"] = decision_latency.get("configured_decision_compute_ms")
            result["decision_latency_enabled"] = decision_latency.get("decision_latency_enabled")
        elif isinstance(observation_lag, dict):
            result["configured_decision_compute_ms"] = observation_lag.get("configured_decision_compute_ms")
            result["decision_latency_enabled"] = observation_lag.get("decision_latency_enabled")

        queue_model_default = self.config.exchange.queue_model if self.config.exchange is not None else self.config.queue_model
        queue_position_default = (
            self.config.exchange.queue_position_assumption
            if self.config.exchange is not None
            else self.config.queue_position_assumption
        )
        if isinstance(queue, dict):
            result["queue_model"] = queue.get("queue_model", queue_model_default)
            result["queue_position_assumption"] = queue.get("queue_position_assumption", queue_position_default)
        else:
            result["queue_model"] = queue_model_default
            result["queue_position_assumption"] = queue_position_default

        if isinstance(latency, dict):
            result["configured_order_submit_ms"] = latency.get("configured_order_submit_ms")
            result["configured_order_ack_ms"] = latency.get("configured_order_ack_ms")
            result["configured_cancel_ms"] = latency.get("configured_cancel_ms")
            result["sampled_avg_submit_latency_ms"] = latency.get("sampled_avg_submit_latency_ms")
            result["sampled_avg_cancel_latency_ms"] = latency.get("sampled_avg_cancel_latency_ms")
            result["sampled_avg_fill_latency_ms"] = latency.get("sampled_avg_fill_latency_ms")
            result["latency_alias_applied"] = latency.get("latency_alias_applied")

        if isinstance(lifecycle, dict):
            result["avg_child_lifetime_seconds"] = lifecycle.get("avg_child_lifetime_seconds", 0.0)
            if "cancel_rate" in lifecycle:
                result["cancel_rate"] = lifecycle.get("cancel_rate", result.get("cancel_rate"))
            if "child_order_count" in lifecycle:
                result["child_order_count"] = lifecycle.get("child_order_count")
            elif "n_child_orders" in result:
                result["child_order_count"] = result["n_child_orders"]
            if "parent_order_count" in lifecycle:
                result["parent_order_count"] = lifecycle.get("parent_order_count")
            elif "n_parent_orders" in result:
                result["parent_order_count"] = result["n_parent_orders"]
            if "signal_count" in lifecycle:
                result["signal_count"] = lifecycle.get("signal_count")

        if result.get("resample_interval") is None and isinstance(tick_time, dict):
            result["resample_interval"] = tick_time.get("resample_interval")
        if result.get("canonical_tick_interval_ms") is None and isinstance(tick_time, dict):
            result["canonical_tick_interval_ms"] = tick_time.get("canonical_tick_interval_ms")

        return result
