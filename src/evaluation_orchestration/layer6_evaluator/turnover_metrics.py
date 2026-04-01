"""
turnover_metrics.py
-------------------
Layer 6: Turnover and Capacity Metrics

Computes portfolio turnover, average holding periods, regime-conditional
performance, and robust return statistics (IQM).

Definitions
-----------
  turnover = total_traded_notional / portfolio_value / n_periods * annualization
  holding_period = average number of steps between position open and close
  IQM = interquartile mean (mean of middle 50% of observations)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from market_simulation.layer5_simulator.bookkeeper import FillEvent
    from data.layer0_data.market_state import MarketState


# ---------------------------------------------------------------------------
# TurnoverReport
# ---------------------------------------------------------------------------

@dataclass
class TurnoverReport:
    """
    포트폴리오 turnover and capacity metrics.

    속성
    ----------
    period_days : float
        Length of the evaluation period in calendar days.
    total_traded_notional : float
        Sum of all fill notionals (buy + sell) in KRW.
    avg_daily_traded_notional : float
        total_traded_notional / period_days.
    annualized_turnover : float
        total_notional / portfolio_value / n_periods * annualization_factor.
        A value of 2.0 means the entire portfolio was traded twice in a year.
    avg_holding_period : float
        Average number of steps (ticks/minutes/days) between open and close.
    regime_breakdown : dict[str, dict]
        Performance broken down by regime label.
        Each inner dict has keys: 'n_fills', 'avg_is_bps', 'total_notional'.
    outlier_fraction : float
        Fraction of episodes in top or bottom 5% of performance distribution.
    iqm_return : float
        Interquartile mean of per-fill returns.
    """
    period_days: float
    total_traded_notional: float
    avg_daily_traded_notional: float
    annualized_turnover: float
    avg_holding_period: float
    regime_breakdown: dict[str, dict] = field(default_factory=dict)
    outlier_fraction: float = 0.0
    iqm_return: float = 0.0

    def to_dict(self) -> dict:
        return {
            "period_days": self.period_days,
            "total_traded_notional": self.total_traded_notional,
            "avg_daily_traded_notional": self.avg_daily_traded_notional,
            "annualized_turnover": self.annualized_turnover,
            "avg_holding_period": self.avg_holding_period,
            "regime_breakdown": self.regime_breakdown,
            "outlier_fraction": self.outlier_fraction,
            "iqm_return": self.iqm_return,
        }

    def __str__(self) -> str:
        rows = [
            ("Period Days", f"{self.period_days:.1f}"),
            ("Total Notional", f"{self.total_traded_notional:,.0f}"),
            ("Avg Daily Notional", f"{self.avg_daily_traded_notional:,.0f}"),
            ("Annualized Turnover", f"{self.annualized_turnover:.4f}x"),
            ("Avg Holding Period", f"{self.avg_holding_period:.1f} steps"),
            ("Outlier Fraction", f"{self.outlier_fraction:.4f}"),
            ("IQM Return", f"{self.iqm_return:.4f}"),
        ]
        width = max(len(k) for k, _ in rows) + 2
        lines = ["Turnover Report", "-" * (width + 20)]
        for key, val in rows:
            lines.append(f"  {key:<{width}}: {val}")
        if self.regime_breakdown:
            lines.append("  Regime Breakdown:")
            for regime, stats in self.regime_breakdown.items():
                lines.append(f"    {regime}: {stats}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TurnoverMetrics
# ---------------------------------------------------------------------------

class TurnoverMetrics:
    """
    Stateless utility for computing turnover and capacity metrics.
    """

    @classmethod
    def compute(
        cls,
        fills: list["FillEvent"],
        portfolio_values: pd.Series,
        positions_history: list[dict],
        annualization_factor: int = 252,
    ) -> TurnoverReport:
        """
        Compute a full TurnoverReport.

        매개변수
        ----------
        fills : list[FillEvent]
            All completed fills.
        portfolio_values : pd.Series
            포트폴리오 NAV indexed by timestamp (used for turnover denominator).
        positions_history : list[dict]
            Sequence of position snapshots: each dict maps symbol -> qty.
            Used to compute holding periods.
        annualization_factor : int
            Trading periods per year.
        """
        if not fills:
            return TurnoverReport(
                period_days=0.0,
                total_traded_notional=0.0,
                avg_daily_traded_notional=0.0,
                annualized_turnover=0.0,
                avg_holding_period=0.0,
            )

        total_notional = sum(f.notional for f in fills)

        # Period length in days
        timestamps = [f.timestamp for f in fills]
        t_start = min(timestamps)
        t_end = max(timestamps)
        period_days = max(1.0, (t_end - t_start).total_seconds() / 86_400.0)

        avg_daily_notional = total_notional / period_days

        # Turnover
        avg_portfolio_value = (
            float(portfolio_values.mean()) if len(portfolio_values) > 0 else 1.0
        )
        n_periods = len(fills)
        turnover = cls.compute_turnover(
            fills, avg_portfolio_value, n_periods, annualization_factor
        )

        # Holding periods
        holding_periods_by_symbol = cls.compute_holding_periods(positions_history)
        all_holding_periods = list(holding_periods_by_symbol.values())
        avg_holding = float(np.mean(all_holding_periods)) if all_holding_periods else 0.0

        # Fill-level returns for IQM / outlier analysis
        # Use slippage_bps as a per-fill return proxy
        fill_returns = np.array([f.slippage_bps for f in fills])
        iqm = cls.compute_iqm(fill_returns) if len(fill_returns) > 0 else 0.0

        # Outlier fraction: fills in top/bottom 5%
        if len(fill_returns) >= 20:
            p5 = np.percentile(fill_returns, 5)
            p95 = np.percentile(fill_returns, 95)
            n_outliers = int(np.sum((fill_returns < p5) | (fill_returns > p95)))
            outlier_frac = n_outliers / len(fill_returns)
        else:
            outlier_frac = 0.0

        return TurnoverReport(
            period_days=period_days,
            total_traded_notional=total_notional,
            avg_daily_traded_notional=avg_daily_notional,
            annualized_turnover=turnover,
            avg_holding_period=avg_holding,
            iqm_return=iqm,
            outlier_fraction=outlier_frac,
        )

    # ------------------------------------------------------------------
    # Turnover
    # ------------------------------------------------------------------

    @staticmethod
    def compute_turnover(
        fills: list["FillEvent"],
        portfolio_value: float,
        n_periods: int,
        annualization_factor: int = 252,
    ) -> float:
        """
        Annualized turnover ratio.

        turnover = (total_notional / portfolio_value) / n_periods * annualization_factor

        A value of 1.0 means the equivalent of the entire portfolio was
        traded once per year.

        매개변수
        ----------
        fills : list[FillEvent]
        portfolio_value : float
            문자열 표현esentative portfolio NAV in KRW.
        n_periods : int
            Number of trading periods (steps) in the evaluation window.
        annualization_factor : int
            Trading periods per year.
        """
        if portfolio_value <= 0.0 or n_periods == 0:
            return 0.0
        total_notional = sum(f.notional for f in fills)
        return (total_notional / portfolio_value) / n_periods * annualization_factor

    # ------------------------------------------------------------------
    # Holding periods
    # ------------------------------------------------------------------

    @staticmethod
    def compute_holding_periods(
        positions_history: list[dict],
    ) -> dict[str, float]:
        """
        Compute average holding period per symbol in number of steps.

        매개변수
        ----------
        positions_history : list[dict]
            Ordered list of position snapshots.  Each dict: {symbol: qty}.
            Steps are assumed equally spaced.

        반환값
        -------
        dict[str, float]
            symbol -> average holding period in steps.
        """
        if not positions_history:
            return {}

        # Collect all symbols
        all_symbols: set[str] = set()
        for snapshot in positions_history:
            all_symbols.update(snapshot.keys())

        holding_periods: dict[str, float] = {}
        n = len(positions_history)

        for symbol in all_symbols:
            qty_arr = np.fromiter(
                (snap.get(symbol, 0) for snap in positions_history),
                dtype=np.int64, count=n,
            )
            nonzero = qty_arr != 0
            # Find transition indices: flat→open and open→flat
            padded = np.empty(n + 2, dtype=bool)
            padded[0] = False
            padded[1:-1] = nonzero
            padded[-1] = False
            opens = np.where(~padded[:-1] & padded[1:])[0]   # step indices where position opened
            closes = np.where(padded[:-1] & ~padded[1:])[0]  # step indices after position closed
            if len(opens) > 0 and len(closes) >= len(opens):
                durations = closes[:len(opens)] - opens
                holding_periods[symbol] = float(np.mean(durations))
            elif len(opens) > 0:
                # Still open at end
                durations = np.append(closes, n) - opens[:len(closes) + 1]
                holding_periods[symbol] = float(np.mean(durations))
            else:
                holding_periods[symbol] = 0.0

        return holding_periods

    # ------------------------------------------------------------------
    # IQM
    # ------------------------------------------------------------------

    @staticmethod
    def compute_iqm(values: np.ndarray) -> float:
        """
        Interquartile Mean: mean of the middle 50% of values
        (excludes top and bottom 25%).

        매개변수
        ----------
        values : np.ndarray
            1-D array of values.

        반환값
        -------
        float
        """
        if len(values) == 0:
            return 0.0
        sorted_vals = np.sort(values)
        n = len(sorted_vals)
        q1_idx = int(np.floor(n * 0.25))
        q3_idx = int(np.ceil(n * 0.75))
        middle = sorted_vals[q1_idx:q3_idx]
        return float(np.mean(middle)) if len(middle) > 0 else float(np.mean(sorted_vals))

    # ------------------------------------------------------------------
    # Regime performance
    # ------------------------------------------------------------------

    @classmethod
    def regime_performance(
        cls,
        fills: list["FillEvent"],
        states: list["MarketState"],
    ) -> dict[str, dict]:
        """
        Group fills by market regime and compute aggregate IS per regime.

        Regime is read from state.features['regime'] if present, otherwise
        derived from imbalance sign (positive = bid-heavy, negative = ask-heavy).

        매개변수
        ----------
        fills : list[FillEvent]
        states : list[MarketState]

        반환값
        -------
        dict[str, dict]
            Regime label -> {'n_fills': int, 'avg_is_bps': float, 'total_notional': float}
        """
        if not fills or not states:
            return {}

        # Build timestamp -> regime map
        state_map: dict[pd.Timestamp, str] = {}
        ts_sorted: list[pd.Timestamp] = sorted(s.timestamp for s in states)
        ts_to_state = {s.timestamp: s for s in states}

        for ts in ts_sorted:
            s = ts_to_state[ts]
            regime = s.features.get("regime", None)
            if regime is None:
                imbalance = s.lob.order_imbalance
                if imbalance is None:
                    regime = "unknown"
                elif imbalance > 0.1:
                    regime = "bid_heavy"
                elif imbalance < -0.1:
                    regime = "ask_heavy"
                else:
                    regime = "balanced"
            state_map[ts] = str(regime)

        ts_arr = np.array(sorted(state_map.keys()), dtype="datetime64[ns]")

        # Assign each fill to a regime
        regime_fills: dict[str, list["FillEvent"]] = {}
        for f in fills:
            idx = np.searchsorted(ts_arr, np.datetime64(f.timestamp, "ns"), side="right") - 1
            if idx < 0:
                regime_label = "unknown"
            else:
                regime_label = state_map[ts_arr[idx]]
            regime_fills.setdefault(regime_label, []).append(f)

        result: dict[str, dict] = {}
        for regime_label, regime_fill_list in regime_fills.items():
            n = len(regime_fill_list)
            avg_is = float(np.mean([f.slippage_bps for f in regime_fill_list]))
            total_notional = sum(f.notional for f in regime_fill_list)
            result[regime_label] = {
                "n_fills": n,
                "avg_is_bps": avg_is,
                "total_notional": total_notional,
            }

        return result
