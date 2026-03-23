"""
risk_caps.py
------------
포트폴리오-level risk constraint checker and enforcer for Layer 2.

클래스
-------
RiskReport  - Snapshot of risk metrics and any constraint violations
RiskCaps    - Checks and enforces gross/net/leverage/concentration limits
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


# ---------------------------------------------------------------------------
# 리스크 보고서
# ---------------------------------------------------------------------------

@dataclass
class RiskReport:
    """
    Summary of risk metrics and constraint violations at a point in time.

    속성
    ----------
    timestamp : pd.Timestamp
    gross_exposure : float
        Sum of |position notional| across all symbols.
    net_exposure : float
        Algebraic sum of position notionals (long - short).
    position_count : int
        Number of symbols with non-zero target position.
    max_single_position : float
        Largest single-symbol notional exposure.
    violations : list[str]
        Human-readable descriptions of constraint breaches.
    is_compliant : bool  (property)
        True when there are no violations.
    """

    timestamp: pd.Timestamp
    gross_exposure: float
    net_exposure: float
    position_count: int
    max_single_position: float
    violations: list[str] = field(default_factory=list)

    @property
    def is_compliant(self) -> bool:
        """True when no constraints are violated."""
        return len(self.violations) == 0

    def __repr__(self) -> str:
        status = "COMPLIANT" if self.is_compliant else f"VIOLATIONS={len(self.violations)}"
        return (
            f"RiskReport(ts={self.timestamp}, "
            f"gross={self.gross_exposure:,.0f}, "
            f"net={self.net_exposure:,.0f}, "
            f"n={self.position_count}, "
            f"status={status})"
        )


# ---------------------------------------------------------------------------
# 리스크 한도
# ---------------------------------------------------------------------------

class RiskCaps:
    """
    포트폴리오 risk constraint manager.

    Checks target positions against configured limits and scales them down
    to achieve compliance when violations are detected.

    매개변수
    ----------
    max_gross_notional : float
        Maximum total gross exposure in currency units (default 1e8).
    max_net_notional : float
        Maximum net long or short exposure (default 5e7).
    max_single_position_pct : float
        Maximum single-symbol weight as fraction of portfolio_value (default 0.10).
    max_leverage : float
        Maximum gross_exposure / portfolio_value (default 2.0).
    max_daily_turnover : float
        Maximum daily turnover as fraction of portfolio_value (default 0.5).
        Not enforced here - see TurnoverBudget; stored for reference.
    vol_target : float | None
        Optional annualised 변동성 target.  Not enforced directly; reserved
        for downstream 변동성-scaling logic.
    """

    def __init__(
        self,
        max_gross_notional: float = 1e8,
        max_net_notional: float = 5e7,
        max_single_position_pct: float = 0.1,
        max_leverage: float = 2.0,
        max_daily_turnover: float = 0.5,
        vol_target: float | None = None,
    ) -> None:
        self.max_gross_notional = max_gross_notional
        self.max_net_notional = max_net_notional
        self.max_single_position_pct = max_single_position_pct
        self.max_leverage = max_leverage
        self.max_daily_turnover = max_daily_turnover
        self.vol_target = vol_target

    # ------------------------------------------------------------------
    # 공개 인터페이스
    # ------------------------------------------------------------------

    def check(
        self,
        targets: dict[str, int],
        prices: dict[str, float],
        portfolio_value: float,
    ) -> RiskReport:
        """
        Compute risk metrics and identify violations without modifying targets.

        매개변수
        ----------
        targets : dict[str, int]
            Proposed symbol → quantity mapping.
        prices : dict[str, float]
            Symbol → current price.
        portfolio_value : float
            포트폴리오 NAV.

        반환값
        -------
        RiskReport
        """
        notionals = self._compute_notionals(targets, prices)
        gross = sum(abs(n) for n in notionals.values())
        net = sum(notionals.values())
        max_single = max((abs(n) for n in notionals.values()), default=0.0)
        n_pos = sum(1 for q in targets.values() if q != 0)

        violations: list[str] = []

        if gross > self.max_gross_notional:
            violations.append(
                f"Gross exposure {gross:,.0f} exceeds limit {self.max_gross_notional:,.0f}"
            )
        if abs(net) > self.max_net_notional:
            violations.append(
                f"Net exposure {net:,.0f} exceeds limit ±{self.max_net_notional:,.0f}"
            )
        if portfolio_value > 0:
            leverage = gross / portfolio_value
            if leverage > self.max_leverage:
                violations.append(
                    f"Leverage {leverage:.2f}x exceeds limit {self.max_leverage:.2f}x"
                )
            if portfolio_value > 0 and max_single / portfolio_value > self.max_single_position_pct:
                violations.append(
                    f"Single position concentration "
                    f"{max_single / portfolio_value:.1%} exceeds limit "
                    f"{self.max_single_position_pct:.1%}"
                )

        return RiskReport(
            timestamp=pd.Timestamp.utcnow(),
            gross_exposure=gross,
            net_exposure=net,
            position_count=n_pos,
            max_single_position=max_single,
            violations=violations,
        )

    def apply(
        self,
        targets: dict[str, int],
        prices: dict[str, float],
        portfolio_value: float,
    ) -> tuple[dict[str, int], RiskReport]:
        """
        Scale down positions iteratively until all constraints are met.

        매개변수
        ----------
        targets : dict[str, int]
        prices : dict[str, float]
        portfolio_value : float

        반환값
        -------
        tuple[dict[str, int], RiskReport]
            (adjusted_targets, final_risk_report)
        """
        adjusted = dict(targets)

        # 1. 필요하면 총익스포저를 축소
        notionals = self._compute_notionals(adjusted, prices)
        gross = sum(abs(n) for n in notionals.values())
        if gross > self.max_gross_notional:
            adjusted = self.scale_to_gross_limit(adjusted, prices, self.max_gross_notional)

        # 2. 필요하면 순익스포저를 축소
        notionals = self._compute_notionals(adjusted, prices)
        net = sum(notionals.values())
        if abs(net) > self.max_net_notional:
            adjusted = self.scale_to_net_limit(adjusted, prices, self.max_net_notional)

        # 3. 단일 포지션 비중 한도를 적용
        if portfolio_value > 0:
            max_notional_per_sym = self.max_single_position_pct * portfolio_value
            for sym in list(adjusted.keys()):
                price = prices.get(sym, 0.0)
                if price > 0:
                    max_qty = int(max_notional_per_sym / price)
                    if abs(adjusted[sym]) > max_qty:
                        adjusted[sym] = int(
                            abs(max_qty) * (1 if adjusted[sym] > 0 else -1)
                        )

            # 4. 레버리지 한도
            notionals = self._compute_notionals(adjusted, prices)
            gross = sum(abs(n) for n in notionals.values())
            leverage = gross / portfolio_value if portfolio_value > 0 else 0.0
            if leverage > self.max_leverage:
                limit = self.max_leverage * portfolio_value
                adjusted = self.scale_to_gross_limit(adjusted, prices, limit)

        report = self.check(adjusted, prices, portfolio_value)
        return adjusted, report

    def scale_to_gross_limit(
        self,
        targets: dict[str, int],
        prices: dict[str, float],
        limit: float,
    ) -> dict[str, int]:
        """
        Proportionally scale all positions so gross notional <= limit.

        매개변수
        ----------
        targets : dict[str, int]
        prices : dict[str, float]
        limit : float

        반환값
        -------
        dict[str, int]
            Scaled targets.
        """
        notionals = self._compute_notionals(targets, prices)
        gross = sum(abs(n) for n in notionals.values())
        if gross <= limit or gross == 0.0:
            return dict(targets)
        scale = limit / gross
        return {
            sym: int(round(qty * scale))
            for sym, qty in targets.items()
        }

    def scale_to_net_limit(
        self,
        targets: dict[str, int],
        prices: dict[str, float],
        limit: float,
    ) -> dict[str, int]:
        """
        Proportionally scale the dominant leg until |net notional| <= limit.

        The long and short legs are each scaled independently.

        매개변수
        ----------
        targets : dict[str, int]
        prices : dict[str, float]
        limit : float  (absolute)

        반환값
        -------
        dict[str, int]
        """
        notionals = self._compute_notionals(targets, prices)
        net = sum(notionals.values())
        if abs(net) <= limit:
            return dict(targets)

        # Separate longs and shorts
        long_notional = sum(n for n in notionals.values() if n > 0)
        short_notional = sum(n for n in notionals.values() if n < 0)  # negative number

        # Reduce the dominant side
        adjusted = dict(targets)
        if net > 0:
            # Long-heavy: scale down longs
            excess = net - limit
            if long_notional > 0:
                scale = max(0.0, 1.0 - excess / long_notional)
                for sym, qty in targets.items():
                    if qty > 0:
                        adjusted[sym] = int(round(qty * scale))
        else:
            # Short-heavy: scale down shorts
            excess = abs(net) - limit
            if short_notional < 0:
                scale = max(0.0, 1.0 - excess / abs(short_notional))
                for sym, qty in targets.items():
                    if qty < 0:
                        adjusted[sym] = int(round(qty * scale))

        return adjusted

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_notionals(
        targets: dict[str, int],
        prices: dict[str, float],
    ) -> dict[str, float]:
        """Return symbol → signed notional value mapping."""
        return {
            sym: qty * prices.get(sym, 0.0)
            for sym, qty in targets.items()
        }
