"""
bookkeeper.py
-------------
Account bookkeeping for Layer 5.

Tracks fills, positions, cash, P&L, and fees across the simulation.

클래스
-------
FillEvent    : immutable record of a single fill
AccountState : mutable snapshot of the account at a point in time
Bookkeeper   : accumulates FillEvents and maintains AccountState
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import pandas as pd

if TYPE_CHECKING:
    pass

from execution_planning.layer3_order.order_types import OrderSide


# ---------------------------------------------------------------------------
# FillEvent
# ---------------------------------------------------------------------------

@dataclass
class FillEvent:
    """
    Immutable record of a single child-order fill.

    속성
    ----------
    timestamp : pd.Timestamp
    order_id : str
        Child order ID.
    parent_id : str
    symbol : str
    side : OrderSide
    filled_qty : int
    fill_price : float
    fee : float
        Total transaction fee paid in KRW.
    slippage_bps : float
        Fill price vs arrival mid in basis points.
        Positive means we paid more than mid (adverse), negative means we
        received better than mid.
    market_impact_bps : float
        Estimated temporary market impact in bps.
    latency_ms : float
        Order-to-fill latency in milliseconds.
    """
    timestamp: pd.Timestamp
    order_id: str
    parent_id: str
    symbol: str
    side: OrderSide
    filled_qty: int
    fill_price: float
    fee: float
    slippage_bps: float
    market_impact_bps: float
    latency_ms: float
    is_maker: bool = False

    @property
    def notional(self) -> float:
        """Gross notional value of the fill (qty * price)."""
        return float(self.filled_qty) * self.fill_price

    @property
    def total_cost(self) -> float:
        """
        Net cost to the account.

        For BUY:  notional + fee  (cash outflow)
        For SELL: notional - fee  (cash inflow net of fees)
        """
        if self.side == OrderSide.BUY:
            return self.notional + self.fee
        else:
            return self.notional - self.fee


# ---------------------------------------------------------------------------
# AccountState
# ---------------------------------------------------------------------------

@dataclass
class AccountState:
    """
    Snapshot of the account at a specific point in time.

    속성
    ----------
    timestamp : pd.Timestamp
    cash : float
        Available cash in KRW.
    positions : dict[str, int]
        Net share positions keyed by symbol.
    realized_pnl : float
        Cumulative realised P&L in KRW (FIFO basis).
    total_fees : float
        Cumulative fees paid.
    total_slippage_cost : float
        Cumulative slippage cost in KRW (approx: slippage_bps * notional / 10_000).
    """
    timestamp: pd.Timestamp
    cash: float
    positions: dict[str, int] = field(default_factory=dict)
    realized_pnl: float = 0.0
    total_fees: float = 0.0
    total_slippage_cost: float = 0.0

    def nav(self, prices: dict[str, float]) -> float:
        """
        Net Asset Value: cash + mark-to-market value of all open positions.

        매개변수
        ----------
        prices : dict[str, float]
            Current mark prices keyed by symbol.

        반환값
        -------
        float
            NAV in KRW.
        """
        position_value = sum(
            qty * prices.get(symbol, 0.0)
            for symbol, qty in self.positions.items()
        )
        return self.cash + position_value


# ---------------------------------------------------------------------------
# Bookkeeper
# ---------------------------------------------------------------------------

class Bookkeeper:
    """
    Maintains the full fill history and live account state.

    매개변수
    ----------
    initial_cash : float
        Starting cash balance in KRW.
    """

    def __init__(self, initial_cash: float = 1e8) -> None:
        self._initial_cash = initial_cash
        self.fills: list[FillEvent] = []
        self.state = AccountState(
            timestamp=pd.Timestamp.now(),
            cash=initial_cash,
        )
        # FIFO 원가 큐: symbol → (price, qty) 튜플 deque
        self._cost_queues: dict[str, deque[tuple[float, int]]] = defaultdict(deque)
        # Short FIFO 원가 큐: symbol → (sell_price, qty) 튜플 deque
        self._short_cost_queues: dict[str, deque[tuple[float, int]]] = defaultdict(deque)

    # ------------------------------------------------------------------
    # 체결 기록
    # ------------------------------------------------------------------

    def record_fill(self, fill: FillEvent) -> None:
        """
        Update positions, cash, and P&L based on `fill`.

        Handles four accounting paths:
        - BUY when flat/long:  open/add long — push to long FIFO
        - BUY when short:      cover short — pop short FIFO, realise P&L
        - SELL when flat/short: open/add short — push to short FIFO
        - SELL when long:       close long — pop long FIFO, realise P&L
        """
        self.fills.append(fill)
        self.state.timestamp = fill.timestamp
        self.state.total_fees += fill.fee

        # KRW 기준 슬리피지 비용
        slippage_cost_krw = abs(fill.slippage_bps) * fill.notional / 10_000.0
        self.state.total_slippage_cost += slippage_cost_krw

        symbol = fill.symbol
        current_pos = self.state.positions.get(symbol, 0)

        if fill.side == OrderSide.BUY:
            self.state.cash -= fill.total_cost
            self.state.positions[symbol] = current_pos + fill.filled_qty

            if current_pos < 0:
                # Covering short position
                cover_qty = min(fill.filled_qty, abs(current_pos))
                realised = self._realise_pnl_fifo_short(
                    symbol, cover_qty, fill.fill_price,
                )
                self.state.realized_pnl += realised
                # If we flipped to long, remaining goes to long FIFO
                remaining = fill.filled_qty - cover_qty
                if remaining > 0:
                    self._cost_queues[symbol].append((fill.fill_price, remaining))
            else:
                # Opening/adding to long
                self._cost_queues[symbol].append((fill.fill_price, fill.filled_qty))

        else:  # SELL
            self.state.cash += fill.total_cost
            self.state.positions[symbol] = current_pos - fill.filled_qty

            if current_pos > 0:
                # Closing long position
                close_qty = min(fill.filled_qty, current_pos)
                realised = self._realise_pnl_fifo(
                    symbol, close_qty, fill.fill_price,
                )
                self.state.realized_pnl += realised
                # If we flipped to short, remaining goes to short FIFO
                remaining = fill.filled_qty - close_qty
                if remaining > 0:
                    self._short_cost_queues[symbol].append((fill.fill_price, remaining))
            else:
                # Opening/adding to short
                self._short_cost_queues[symbol].append((fill.fill_price, fill.filled_qty))

    # ------------------------------------------------------------------
    # 손익 도우미
    # ------------------------------------------------------------------

    def compute_realized_pnl(self, symbol: str) -> float:
        """
        Compute cumulative realised P&L for `symbol` using FIFO matching.
        This re-computes from the fill history rather than using cached state.
        Handles both long and short positions.
        """
        long_queue: deque[tuple[float, int]] = deque()
        short_queue: deque[tuple[float, int]] = deque()
        realised = 0.0
        pos = 0

        for fill in self.fills:
            if fill.symbol != symbol:
                continue

            if fill.side == OrderSide.BUY:
                if pos < 0:
                    # Covering short
                    cover_qty = min(fill.filled_qty, abs(pos))
                    rem = cover_qty
                    while rem > 0 and short_queue:
                        sp, sq = short_queue[0]
                        m = min(rem, sq)
                        realised += m * (sp - fill.fill_price)
                        rem -= m
                        if m == sq:
                            short_queue.popleft()
                        else:
                            short_queue[0] = (sp, sq - m)
                    remaining = fill.filled_qty - cover_qty
                    if remaining > 0:
                        long_queue.append((fill.fill_price, remaining))
                else:
                    long_queue.append((fill.fill_price, fill.filled_qty))
                pos += fill.filled_qty

            else:  # SELL
                if pos > 0:
                    # Closing long
                    close_qty = min(fill.filled_qty, pos)
                    rem = close_qty
                    while rem > 0 and long_queue:
                        cp, cq = long_queue[0]
                        m = min(rem, cq)
                        realised += m * (fill.fill_price - cp)
                        rem -= m
                        if m == cq:
                            long_queue.popleft()
                        else:
                            long_queue[0] = (cp, cq - m)
                    remaining = fill.filled_qty - close_qty
                    if remaining > 0:
                        short_queue.append((fill.fill_price, remaining))
                else:
                    short_queue.append((fill.fill_price, fill.filled_qty))
                pos -= fill.filled_qty

        return realised

    def mark_to_market(self, prices: dict[str, float]) -> float:
        """
        Compute total unrealised P&L across all open positions.

        unrealised_pnl[symbol] = (current_price - avg_cost) * qty

        반환값
        -------
        float
            Total unrealised P&L in KRW.
        """
        total_unrealised = 0.0
        for symbol, qty in self.state.positions.items():
            if qty == 0:
                continue
            current_price = prices.get(symbol)
            if current_price is None:
                continue
            avg_cost = self.get_average_cost(symbol)
            unrealised = (current_price - avg_cost) * qty
            total_unrealised += unrealised
        return total_unrealised

    def get_position(self, symbol: str) -> int:
        """Return current net position in shares for `symbol`."""
        return self.state.positions.get(symbol, 0)

    def get_average_cost(self, symbol: str) -> float:
        """
        Return the FIFO average cost basis per share for `symbol`.

        For long positions, uses the long FIFO queue.
        For short positions, uses the short FIFO queue (avg sell price).
        If no open position exists, returns 0.0.
        """
        pos = self.state.positions.get(symbol, 0)
        if pos > 0:
            queue = self._cost_queues.get(symbol)
        elif pos < 0:
            queue = self._short_cost_queues.get(symbol)
        else:
            return 0.0

        if not queue:
            return 0.0
        total_cost = sum(price * qty for price, qty in queue)
        total_qty = sum(qty for _, qty in queue)
        if total_qty == 0:
            return 0.0
        return total_cost / total_qty

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self, initial_cash: Optional[float] = None) -> None:
        """
        Reset the bookkeeper to its initial state.

        매개변수
        ----------
        initial_cash : float | None
            Override the initial cash balance.  If None, uses the value
            provided at construction.
        """
        cash = initial_cash if initial_cash is not None else self._initial_cash
        if initial_cash is not None:
            self._initial_cash = initial_cash
        self.fills.clear()
        self._cost_queues.clear()
        self._short_cost_queues.clear()
        self.state = AccountState(
            timestamp=pd.Timestamp.now(),
            cash=cash,
        )

    def to_dataframe(self) -> pd.DataFrame:
        """
        Return the complete fill history as a tidy DataFrame.

        Columns
        -------
        timestamp, order_id, parent_id, symbol, side,
        filled_qty, fill_price, fee, slippage_bps,
        market_impact_bps, latency_ms, notional, total_cost
        """
        if not self.fills:
            return pd.DataFrame()

        records = []
        for f in self.fills:
            records.append(
                {
                    "timestamp": f.timestamp,
                    "order_id": f.order_id,
                    "parent_id": f.parent_id,
                    "symbol": f.symbol,
                    "side": f.side.value,
                    "filled_qty": f.filled_qty,
                    "fill_price": f.fill_price,
                    "fee": f.fee,
                    "slippage_bps": f.slippage_bps,
                    "market_impact_bps": f.market_impact_bps,
                    "latency_ms": f.latency_ms,
                    "notional": f.notional,
                    "total_cost": f.total_cost,
                }
            )
        df = pd.DataFrame(records)
        df.set_index("timestamp", inplace=True)
        return df

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------

    def _realise_pnl_fifo(
        self,
        symbol: str,
        sell_qty: int,
        sell_price: float,
    ) -> float:
        """
        Match `sell_qty` against the long FIFO cost queue and return realised P&L.
        Modifies self._cost_queues[symbol] in place.
        """
        queue = self._cost_queues[symbol]
        realised = 0.0
        remaining = sell_qty

        while remaining > 0 and queue:
            cost_price, cost_qty = queue[0]
            matched = min(remaining, cost_qty)
            realised += matched * (sell_price - cost_price)
            remaining -= matched
            if matched == cost_qty:
                queue.popleft()
            else:
                queue[0] = (cost_price, cost_qty - matched)

        return realised

    def _realise_pnl_fifo_short(
        self,
        symbol: str,
        cover_qty: int,
        buy_price: float,
    ) -> float:
        """
        Match `cover_qty` against the short FIFO queue and return realised P&L.
        Short profit = sell_price - buy_price for each matched lot.
        Modifies self._short_cost_queues[symbol] in place.
        """
        queue = self._short_cost_queues[symbol]
        realised = 0.0
        remaining = cover_qty

        while remaining > 0 and queue:
            sell_price, sell_qty = queue[0]
            matched = min(remaining, sell_qty)
            realised += matched * (sell_price - buy_price)
            remaining -= matched
            if matched == sell_qty:
                queue.popleft()
            else:
                queue[0] = (sell_price, sell_qty - matched)

        return realised
