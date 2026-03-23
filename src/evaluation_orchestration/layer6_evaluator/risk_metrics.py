"""
risk_metrics.py
---------------
Layer 6: Risk Metrics

Computes standard quantitative risk metrics from a PnL / returns series:
  - Volatility, Sharpe, Sortino, Calmar
  - Maximum Drawdown (value and duration)
  - Value at Risk (VaR) and Expected Shortfall (CVaR)
  - Skewness and Kurtosis
  - Rolling metrics window
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats


# ---------------------------------------------------------------------------
# RiskReport
# ---------------------------------------------------------------------------

@dataclass
class RiskReport:
    """
    Container for a full set of risk metrics over a given period.

    속성
    ----------
    period : str
        Label for the period, e.g. 'episode', 'daily', 'full'.
    annualized_vol : float
        Annualized 변동성 of period returns (fraction, not %).
    max_drawdown : float
        Maximum drawdown as a fraction (e.g. 0.10 = 10% drawdown).
    max_drawdown_duration : int
        Number of steps/periods the longest drawdown lasted.
    var_95 : float
        Historical 5% VaR (loss magnitude, positive number).
    var_99 : float
        Historical 1% VaR.
    expected_shortfall_95 : float
        CVaR / ES at 95% confidence (average of worst 5% losses).
    expected_shortfall_99 : float
        CVaR / ES at 99% confidence.
    sharpe_ratio : float
        Annualized Sharpe ratio (no risk-free rate deduction).
    sortino_ratio : float
        Sortino ratio using downside deviation only.
    calmar_ratio : float
        Annualized return divided by max drawdown magnitude.
    skewness : float
    kurtosis : float
        Excess kurtosis (normal = 0).
    """
    period: str
    annualized_vol: float
    max_drawdown: float
    max_drawdown_duration: int
    var_95: float
    var_99: float
    expected_shortfall_95: float
    expected_shortfall_99: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    skewness: float
    kurtosis: float

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "annualized_vol": self.annualized_vol,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_duration": self.max_drawdown_duration,
            "var_95": self.var_95,
            "var_99": self.var_99,
            "expected_shortfall_95": self.expected_shortfall_95,
            "expected_shortfall_99": self.expected_shortfall_99,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "calmar_ratio": self.calmar_ratio,
            "skewness": self.skewness,
            "kurtosis": self.kurtosis,
        }

    def __str__(self) -> str:
        rows = [
            ("Period", self.period),
            ("Annualized Vol", f"{self.annualized_vol:.4f}"),
            ("Max Drawdown", f"{self.max_drawdown:.4f}  ({self.max_drawdown * 100:.2f}%)"),
            ("MDD Duration", f"{self.max_drawdown_duration} steps"),
            ("VaR 95%", f"{self.var_95:.4f}"),
            ("VaR 99%", f"{self.var_99:.4f}"),
            ("ES 95%", f"{self.expected_shortfall_95:.4f}"),
            ("ES 99%", f"{self.expected_shortfall_99:.4f}"),
            ("Sharpe Ratio", f"{self.sharpe_ratio:.4f}"),
            ("Sortino Ratio", f"{self.sortino_ratio:.4f}"),
            ("Calmar Ratio", f"{self.calmar_ratio:.4f}"),
            ("Skewness", f"{self.skewness:.4f}"),
            ("Kurtosis (excess)", f"{self.kurtosis:.4f}"),
        ]
        width = max(len(k) for k, _ in rows) + 2
        lines = ["Risk Report", "-" * (width + 20)]
        for key, val in rows:
            lines.append(f"  {key:<{width}}: {val}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# RiskMetrics
# ---------------------------------------------------------------------------

class RiskMetrics:
    """
    Stateless utility class for computing risk metrics from a PnL series.

    All methods are class methods / static methods so no instantiation
    is required, but the class is used as a namespace.
    """

    @classmethod
    def compute(
        cls,
        pnl_series: pd.Series,
        freq: str = "tick",
        annualization_factor: int = 252,
        period: str = "full",
    ) -> RiskReport:
        """
        Compute the full RiskReport from a cumulative PnL series.

        매개변수
        ----------
        pnl_series : pd.Series
            Cumulative PnL indexed by timestamp.  Values should be in KRW
            (or any currency unit); metrics are scale-invariant ratios.
        freq : str
            'tick' | 'minute' | 'daily' — used to pick the right
            periods_per_year for the annualization_factor.
        annualization_factor : int
            Number of trading days per year (used when freq='daily').
            For tick/minute data the caller should pass the actual
            number of periods per year, or rely on the default 252
            which the code scales appropriately.
        period : str
            Label stored in RiskReport.period.

        반환값
        -------
        RiskReport
        """
        if pnl_series is None or len(pnl_series) < 2:
            return cls._empty_report(period)

        pnl_series = pnl_series.dropna().sort_index()
        if len(pnl_series) < 2:
            return cls._empty_report(period)

        # Period returns: differences of cumulative PnL
        returns = pnl_series.diff().dropna()

        if len(returns) == 0:
            return cls._empty_report(period)

        ret_arr = returns.values.astype(float)

        # Determine periods per year based on freq
        periods_per_year = cls._periods_per_year(freq, annualization_factor)

        # Volatility
        period_std = float(np.std(ret_arr, ddof=1)) if len(ret_arr) > 1 else 0.0
        ann_vol = cls._annualize_vol(period_std, periods_per_year)

        # Mean return (annualized)
        mean_ret = float(np.mean(ret_arr))
        ann_return = mean_ret * periods_per_year

        # Drawdown
        max_dd, max_dd_dur = cls._compute_drawdown(pnl_series)

        # VaR / ES
        var_95 = cls._compute_var(ret_arr, confidence=0.95)
        var_99 = cls._compute_var(ret_arr, confidence=0.99)
        es_95 = cls._compute_es(ret_arr, confidence=0.95)
        es_99 = cls._compute_es(ret_arr, confidence=0.99)

        # Sharpe (annualized mean / annualized vol)
        sharpe = ann_return / ann_vol if ann_vol > 0.0 else 0.0

        # Sortino: downside deviation
        downside = ret_arr[ret_arr < 0]
        if len(downside) > 1:
            downside_std = float(np.std(downside, ddof=1))
            ann_downside_std = cls._annualize_vol(downside_std, periods_per_year)
        else:
            ann_downside_std = ann_vol  # fallback
        sortino = ann_return / ann_downside_std if ann_downside_std > 0.0 else 0.0

        # Calmar: annualized return / MDD
        calmar = ann_return / max_dd if max_dd > 0.0 else 0.0

        # Higher moments
        skew = float(scipy_stats.skew(ret_arr)) if len(ret_arr) > 2 else 0.0
        kurt = float(scipy_stats.kurtosis(ret_arr)) if len(ret_arr) > 3 else 0.0  # excess

        return RiskReport(
            period=period,
            annualized_vol=ann_vol,
            max_drawdown=max_dd,
            max_drawdown_duration=max_dd_dur,
            var_95=var_95,
            var_99=var_99,
            expected_shortfall_95=es_95,
            expected_shortfall_99=es_99,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            skewness=skew,
            kurtosis=kurt,
        )

    # ------------------------------------------------------------------
    # Drawdown
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_drawdown(cum_returns: pd.Series) -> tuple[float, int]:
        """
        Compute maximum drawdown fraction and its duration in periods.

        MDD = max over all t of  (peak_t - trough_t) / peak_t
        Duration = longest consecutive period below a prior peak.

        매개변수
        ----------
        cum_returns : pd.Series
            Cumulative PnL / portfolio value series (levels, not returns).

        반환값
        -------
        (max_drawdown, max_drawdown_duration)
            max_drawdown is a positive fraction (0.10 = 10% drop).
            Duration is in number of periods.
        """
        vals = cum_returns.values.astype(float)
        if len(vals) == 0:
            return 0.0, 0

        # Shift so minimum value is > 0 to avoid division issues
        offset = abs(min(vals)) + 1.0
        vals_adj = vals + offset

        peak = np.maximum.accumulate(vals_adj)
        drawdown = (peak - vals_adj) / peak

        max_dd = float(np.max(drawdown))

        # Duration: count consecutive periods in drawdown
        in_dd = (drawdown > 0).astype(int)
        max_dur = 0
        cur_dur = 0
        for flag in in_dd:
            if flag:
                cur_dur += 1
                max_dur = max(max_dur, cur_dur)
            else:
                cur_dur = 0

        return max_dd, max_dur

    # ------------------------------------------------------------------
    # VaR / ES
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_var(returns: np.ndarray, confidence: float) -> float:
        """
        Historical simulation VaR.

        반환값 the loss magnitude at (1-confidence) quantile.
        Positive value = loss.

        매개변수
        ----------
        returns : np.ndarray
            Period returns (can be negative).
        confidence : float
            e.g. 0.95 for 95% VaR.
        """
        if len(returns) == 0:
            return 0.0
        alpha = 1.0 - confidence  # e.g. 0.05
        var = float(np.percentile(returns, alpha * 100))
        return -var  # return loss magnitude (positive)

    @staticmethod
    def _compute_es(returns: np.ndarray, confidence: float) -> float:
        """
        Expected Shortfall (CVaR): average of returns worse than VaR threshold.

        반환값 loss magnitude (positive number).

        매개변수
        ----------
        returns : np.ndarray
        confidence : float
            e.g. 0.95 for 95% ES.
        """
        if len(returns) == 0:
            return 0.0
        alpha = 1.0 - confidence
        threshold = float(np.percentile(returns, alpha * 100))
        tail = returns[returns <= threshold]
        if len(tail) == 0:
            return -threshold
        return -float(np.mean(tail))

    # ------------------------------------------------------------------
    # Annualization
    # ------------------------------------------------------------------

    @staticmethod
    def _annualize_vol(period_vol: float, periods_per_year: int) -> float:
        """Annualize period 변동성: sqrt(periods_per_year) * period_vol."""
        return float(np.sqrt(periods_per_year)) * period_vol

    @staticmethod
    def _periods_per_year(freq: str, annualization_factor: int) -> int:
        """
        Map frequency string to approximate periods per year.

        For 'daily' the caller's annualization_factor is used directly.
        For 'minute' we assume 390 trading minutes/day.
        For 'tick' we use the annualization_factor as-is (caller must set it).
        """
        freq_map = {
            "daily": annualization_factor,
            "minute": annualization_factor * 390,
            "tick": annualization_factor,  # caller must supply meaningful value
        }
        return freq_map.get(freq, annualization_factor)

    # ------------------------------------------------------------------
    # Rolling metrics
    # ------------------------------------------------------------------

    @classmethod
    def rolling_metrics(
        cls,
        pnl_series: pd.Series,
        window: int = 60,
        annualization_factor: int = 252,
    ) -> pd.DataFrame:
        """
        Compute rolling Sharpe ratio, annualized 변동성, and MDD.

        매개변수
        ----------
        pnl_series : pd.Series
            Cumulative PnL indexed by timestamp.
        window : int
            Look-back window in periods.
        annualization_factor : int
            Trading days per year.

        반환값
        -------
        pd.DataFrame
            Columns: 'rolling_sharpe', 'rolling_vol', 'rolling_mdd'.
            Index: same as pnl_series.
        """
        if pnl_series is None or len(pnl_series) < 2:
            return pd.DataFrame(
                columns=["rolling_sharpe", "rolling_vol", "rolling_mdd"],
                index=pnl_series.index if pnl_series is not None else pd.DatetimeIndex([]),
            )

        returns = pnl_series.diff().dropna()
        sqrt_ann = np.sqrt(annualization_factor)

        rolling_mean = returns.rolling(window).mean()
        rolling_std = returns.rolling(window).std()

        rolling_vol = rolling_std * sqrt_ann
        rolling_sharpe = (rolling_mean / rolling_std * sqrt_ann).where(rolling_std > 0, 0.0)

        # Rolling MDD: compute over each window
        rolling_mdd = pd.Series(np.nan, index=pnl_series.index, name="rolling_mdd")
        idx = pnl_series.index
        for i in range(window, len(pnl_series) + 1):
            sub = pnl_series.iloc[i - window: i]
            mdd, _ = cls._compute_drawdown(sub)
            rolling_mdd.iloc[i - 1] = mdd

        result = pd.DataFrame(
            {
                "rolling_sharpe": rolling_sharpe,
                "rolling_vol": rolling_vol,
                "rolling_mdd": rolling_mdd,
            }
        )
        return result

    # ------------------------------------------------------------------
    # 도우미
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_report(period: str = "full") -> RiskReport:
        return RiskReport(
            period=period,
            annualized_vol=0.0,
            max_drawdown=0.0,
            max_drawdown_duration=0,
            var_95=0.0,
            var_99=0.0,
            expected_shortfall_95=0.0,
            expected_shortfall_99=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
            skewness=0.0,
            kurtosis=0.0,
        )
