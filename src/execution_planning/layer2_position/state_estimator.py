"""
state_estimator.py
------------------
포트폴리오 state tracking for Layer 2.

클래스
-------
PortfolioState          - Immutable snapshot of portfolio at a point in time
PortfolioStateEstimator - Stateful bookkeeper updated with fills and mark-to-market
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

import pandas as pd


# ---------------------------------------------------------------------------
# 포트폴리오 상태 스냅샷
# ---------------------------------------------------------------------------

@dataclass
class PortfolioState:
    """
    Snapshot of the portfolio at a specific point in time.

    속성
    ----------
    timestamp : pd.Timestamp
    positions : dict[str, int]
        Symbol → shares held.  Negative values denote net short positions.
    cash : float
        Available cash balance (after all fills and fees).
    realized_pnl : float
        Cumulative realized PnL since inception.
    unrealized_pnl : float
        Unrealized PnL at the last mark-to-market price.
    """

    timestamp: pd.Timestamp
    positions: dict[str, int] = field(default_factory=dict)
    cash: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    def total_value(self, prices: dict[str, float]) -> float:
        """
        Total portfolio value: cash + market value of all positions.

        매개변수
        ----------
        prices : dict[str, float]
            Symbol → current price.

        반환값
        -------
        float
        """
        market_value = sum(
            qty * prices.get(sym, 0.0) for sym, qty in self.positions.items()
        )
        return self.cash + market_value

    def nav(self, prices: dict[str, float]) -> float:
        """Alias for total_value; returns Net Asset Value."""
        return self.total_value(prices)

    def __repr__(self) -> str:
        n_pos = sum(1 for q in self.positions.values() if q != 0)
        return (
            f"PortfolioState(ts={self.timestamp}, "
            f"cash={self.cash:,.2f}, "
            f"realized_pnl={self.realized_pnl:,.2f}, "
            f"unrealized_pnl={self.unrealized_pnl:,.2f}, "
            f"n_positions={n_pos})"
        )


# ---------------------------------------------------------------------------
# 포트폴리오 상태 추정기
# ---------------------------------------------------------------------------

class PortfolioStateEstimator:
    """
    Stateful bookkeeper that maintains PortfolioState across fills.

    Tracks average cost basis per symbol to compute realized and unrealized PnL.

    매개변수
    ----------
    initial_cash : float
        Starting cash balance (default 1e8).
    """

    def __init__(self, initial_cash: float = 1e8) -> None:
        self._initial_cash = initial_cash
        self._state = PortfolioState(
            timestamp=pd.Timestamp.utcnow(),
            positions={},
            cash=initial_cash,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )
        # 손익 추적을 위한 심볼별 평균 단가
        self._avg_cost: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> PortfolioState:
        """Current portfolio state."""
        return self._state

    # ------------------------------------------------------------------
    # Fill processing
    # ------------------------------------------------------------------

    def apply_fill(
        self,
        symbol: str,
        qty: int,
        price: float,
        fee: float,
        side: str,
    ) -> None:
        """
        Update portfolio state with an execution fill.

        매개변수
        ----------
        symbol : str
            Instrument identifier.
        qty : int
            Filled quantity (always positive; direction determined by side).
        price : float
            Fill price per share.
        fee : float
            Total transaction fee in currency units.
        side : str
            'buy'  → add +qty to position, debit cash.
            'sell' → add -qty to position, credit cash.
        """
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")

        side_lower = side.lower()
        if side_lower not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

        signed_qty = qty if side_lower == "buy" else -qty
        trade_value = qty * price  # always positive
        current_qty = self._state.positions.get(symbol, 0)
        new_qty = current_qty + signed_qty

        # 실현 손익 계산
        realized = 0.0
        if side_lower == "sell" and current_qty > 0:
            # 롱 포지션 청산(또는 축소)
            closed_qty = min(qty, current_qty)
            avg = self._avg_cost.get(symbol, price)
            realized = closed_qty * (price - avg)
        elif side_lower == "buy" and current_qty < 0:
            # 숏 포지션 청산(또는 축소)
            closed_qty = min(qty, abs(current_qty))
            avg = self._avg_cost.get(symbol, price)
            realized = closed_qty * (avg - price)

        # 평균 단가 갱신
        if new_qty == 0:
            self._avg_cost.pop(symbol, None)
        elif (signed_qty > 0 and current_qty >= 0) or (signed_qty < 0 and current_qty <= 0):
            # 신규 진입 또는 확대: 가중 평균 단가 갱신
            prev_cost = self._avg_cost.get(symbol, 0.0)
            prev_abs = abs(current_qty)
            new_abs = prev_abs + qty
            self._avg_cost[symbol] = (
                (prev_abs * prev_cost + qty * price) / new_abs
                if new_abs > 0
                else price
            )
        # 그 외는 부분 청산이므로 남은 수량의 평균 단가는 유지

        # 포지션 갱신
        if new_qty == 0:
            self._state.positions.pop(symbol, None)
        else:
            self._state.positions[symbol] = new_qty

        # 현금 갱신
        if side_lower == "buy":
            self._state.cash -= trade_value + fee
        else:
            self._state.cash += trade_value - fee

        # 실현 손익 누적(수수료 차감 후)
        self._state.realized_pnl += realized - fee
        self._state.timestamp = pd.Timestamp.utcnow()

    # ------------------------------------------------------------------
    # Mark-to-market
    # ------------------------------------------------------------------

    def mark_to_market(self, prices: dict[str, float]) -> float:
        """
        Recompute unrealized PnL at current market prices.

        매개변수
        ----------
        prices : dict[str, float]
            Symbol → current price.

        반환값
        -------
        float
            Total unrealized PnL.
        """
        unrealized = 0.0
        for sym, qty in self._state.positions.items():
            avg = self._avg_cost.get(sym, 0.0)
            current_price = prices.get(sym, avg)
            unrealized += qty * (current_price - avg)
        self._state.unrealized_pnl = unrealized
        self._state.timestamp = pd.Timestamp.utcnow()
        return unrealized

    # ------------------------------------------------------------------
    # Reset / snapshot
    # ------------------------------------------------------------------

    def reset(self, initial_cash: float | None = None) -> None:
        """
        Reset the estimator to a clean slate.

        매개변수
        ----------
        initial_cash : float | None
            New starting cash.  Uses the original initial_cash if None.
        """
        cash = initial_cash if initial_cash is not None else self._initial_cash
        self._initial_cash = cash
        self._state = PortfolioState(
            timestamp=pd.Timestamp.utcnow(),
            positions={},
            cash=cash,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )
        self._avg_cost.clear()

    def snapshot(self) -> PortfolioState:
        """
        Return a deep copy of the current portfolio state.

        반환값
        -------
        PortfolioState
        """
        return PortfolioState(
            timestamp=self._state.timestamp,
            positions=copy.copy(self._state.positions),
            cash=self._state.cash,
            realized_pnl=self._state.realized_pnl,
            unrealized_pnl=self._state.unrealized_pnl,
        )
