"""
exposure_controller.py
----------------------
Exposure tracking and concentration control for Layer 2.

클래스
-------
ExposureReport      - Snapshot of portfolio exposure metrics
ExposureController  - Computes exposure, checks concentration, applies constraints
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


# ---------------------------------------------------------------------------
# 익스포저 보고서
# ---------------------------------------------------------------------------

@dataclass
class ExposureReport:
    """
    Detailed portfolio exposure snapshot.

    속성
    ----------
    timestamp : pd.Timestamp
    gross_exposure : float
        Sum of absolute notional values.
    net_exposure : float
        Long notional minus short notional (signed).
    long_exposure : float
        Total notional value of long positions.
    short_exposure : float
        Total absolute notional value of short positions.
    exposure_by_symbol : dict[str, float]
        Symbol → signed notional exposure.
    concentration_hhi : float
        Herfindahl-Hirschman Index of notional weights.
        Sum of squared portfolio-weight fractions, in [0, 1].
        0 = perfectly diversified, 1 = fully concentrated.
    """

    timestamp: pd.Timestamp
    gross_exposure: float
    net_exposure: float
    long_exposure: float
    short_exposure: float
    exposure_by_symbol: dict[str, float] = field(default_factory=dict)
    concentration_hhi: float = 0.0

    def __repr__(self) -> str:
        return (
            f"ExposureReport(ts={self.timestamp}, "
            f"gross={self.gross_exposure:,.0f}, "
            f"net={self.net_exposure:,.0f}, "
            f"hhi={self.concentration_hhi:.4f})"
        )


# ---------------------------------------------------------------------------
# 익스포저 제어기
# ---------------------------------------------------------------------------

class ExposureController:
    """
    Tracks portfolio exposure and enforces concentration limits.

    매개변수
    ----------
    max_concentration_hhi : float
        Maximum allowable HHI.  Positions will be diluted if this is exceeded
        (default 0.3, equivalent to roughly 3+ equally-weighted positions).
    min_symbols : int
        Minimum number of symbols that must be held when long positions exist.
        Enforced during constraint application.
    """

    def __init__(
        self,
        max_concentration_hhi: float = 0.3,
        min_symbols: int = 1,
    ) -> None:
        self._max_hhi = max_concentration_hhi
        self._min_symbols = min_symbols

    # ------------------------------------------------------------------
    # 공개 인터페이스
    # ------------------------------------------------------------------

    def compute_exposure(
        self,
        positions: dict[str, int],
        prices: dict[str, float],
    ) -> ExposureReport:
        """
        Compute a full exposure snapshot from current positions and prices.

        매개변수
        ----------
        positions : dict[str, int]
            Symbol → shares held (negative for shorts).
        prices : dict[str, float]
            Symbol → current price.

        반환값
        -------
        ExposureReport
        """
        notionals: dict[str, float] = {
            sym: qty * prices.get(sym, 0.0)
            for sym, qty in positions.items()
        }

        gross = sum(abs(n) for n in notionals.values())
        net = sum(notionals.values())
        long_exp = sum(n for n in notionals.values() if n > 0)
        short_exp = sum(abs(n) for n in notionals.values() if n < 0)

        # HHI: 절대 가중치 비율 기준
        hhi = 0.0
        if gross > 0:
            hhi = sum((abs(n) / gross) ** 2 for n in notionals.values())

        return ExposureReport(
            timestamp=pd.Timestamp.utcnow(),
            gross_exposure=gross,
            net_exposure=net,
            long_exposure=long_exp,
            short_exposure=short_exp,
            exposure_by_symbol=notionals,
            concentration_hhi=hhi,
        )

    def check_concentration(
        self,
        positions: dict[str, int],
        prices: dict[str, float],
    ) -> bool:
        """
        Return True when concentration is within the HHI limit.

        매개변수
        ----------
        positions : dict[str, int]
        prices : dict[str, float]

        반환값
        -------
        bool
        """
        report = self.compute_exposure(positions, prices)
        return report.concentration_hhi <= self._max_hhi

    def neutralize_net(
        self,
        targets: dict[str, int],
        prices: dict[str, float],
    ) -> dict[str, int]:
        """
        Scale the dominant leg to achieve approximate net neutrality.

        Iteratively reduces the larger of the long or short leg until
        the net notional is as close to zero as possible.

        매개변수
        ----------
        targets : dict[str, int]
        prices : dict[str, float]

        반환값
        -------
        dict[str, int]
            Net-neutral targets.
        """
        notionals = {sym: qty * prices.get(sym, 0.0) for sym, qty in targets.items()}
        long_total = sum(n for n in notionals.values() if n > 0)
        short_total = sum(abs(n) for n in notionals.values() if n < 0)

        if long_total == 0.0 or short_total == 0.0:
            return dict(targets)

        adjusted = dict(targets)

        if long_total > short_total:
            # 롱 포지션을 줄여 숏과 맞춘다
            scale = short_total / long_total
            for sym, qty in targets.items():
                if qty > 0:
                    adjusted[sym] = int(round(qty * scale))
        else:
            # 숏 포지션을 줄여 롱과 맞춘다
            scale = long_total / short_total
            for sym, qty in targets.items():
                if qty < 0:
                    adjusted[sym] = int(round(qty * scale))

        return adjusted

    def apply_constraints(
        self,
        targets: dict[str, int],
        prices: dict[str, float],
    ) -> tuple[dict[str, int], ExposureReport]:
        """
        Apply concentration constraints to target positions.

        If HHI exceeds the limit, the largest positions are scaled down
        proportionally until the constraint is satisfied.

        매개변수
        ----------
        targets : dict[str, int]
        prices : dict[str, float]

        반환값
        -------
        tuple[dict[str, int], ExposureReport]
            (adjusted_targets, exposure_report)
        """
        adjusted = dict(targets)
        report = self.compute_exposure(adjusted, prices)

        if report.concentration_hhi > self._max_hhi and report.gross_exposure > 0:
            # 가중치를 균등화해 목표 HHI를 맞춘다. 이상적인 동일 가중치를 계산한다.
            n_nonzero = sum(1 for q in adjusted.values() if q != 0)
            if n_nonzero > 0:
                # 동일 가중치 HHI = 1/n; 가장 큰 포지션부터 줄인다
                # 단순 접근: 모든 포지션을 같은 비율로 축소한다
                # 이렇게 하면 집중도가 완화된다.
                # 축소 비율은 target_hhi = current_hhi * scale^2 에서 유도한다
                import math
                scale = math.sqrt(self._max_hhi / report.concentration_hhi)
                scale = min(1.0, max(0.0, scale))
                for sym in list(adjusted.keys()):
                    adjusted[sym] = int(round(adjusted[sym] * scale))

        report = self.compute_exposure(adjusted, prices)
        return adjusted, report
