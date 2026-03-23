"""
order_constraints.py
--------------------
Exchange and regulatory order constraints for Layer 3.

클래스
-----
OrderConstraints  - Validates and adjusts orders for tick size, lot size, price bands
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState

from .order_types import OrderStatus, ParentOrder


class OrderConstraints:
    """
    Apply exchange-level constraints to orders.

    Handles tick-size rounding, lot-size rounding, price-band enforcement
    (e.g. KRX ±30%), minimum quantity checks, and tradability filtering.

    매개변수
    ----------
    tick_size : dict[str, float] | float
        Tick size per symbol, or a scalar applied to all symbols.
    lot_size : dict[str, int] | int
        Lot (board lot) size per symbol, or a scalar.
    min_qty : int
        Minimum allowed order quantity (default 1).
    price_band_pct : float
        Maximum allowed price deviation from reference price as a fraction.
        Default 0.30 (±30%, matching KRX daily price limits).
    """

    def __init__(
        self,
        tick_size: dict[str, float] | float = 1.0,
        lot_size: dict[str, int] | int = 1,
        min_qty: int = 1,
        price_band_pct: float = 0.30,
    ) -> None:
        self._tick_size = tick_size
        self._lot_size = lot_size
        self._min_qty = min_qty
        self._price_band_pct = price_band_pct

    # ------------------------------------------------------------------
    # 가격 도우미
    # ------------------------------------------------------------------

    def round_price(self, symbol: str, price: float) -> float:
        """
        Round price to the nearest valid tick for the given symbol.

        매개변수
        ----------
        symbol : str
        price : float

        반환값
        -------
        float
        """
        tick = self._get_tick(symbol)
        if tick <= 0:
            return price
        rounded = round(price / tick) * tick
        # 부동소수점 오차를 방지한다
        decimals = max(0, -int(math.floor(math.log10(tick)))) if tick < 1 else 0
        return round(rounded, decimals)

    def round_qty(self, symbol: str, qty: int) -> int:
        """
        Round quantity down to the nearest valid lot size.

        매개변수
        ----------
        symbol : str
        qty : int

        반환값
        -------
        int
        """
        lot = self._get_lot(symbol)
        if lot <= 0:
            return qty
        return (qty // lot) * lot

    def apply_price_band(
        self,
        symbol: str,
        price: float,
        ref_price: float,
    ) -> float:
        """
        Clip price to within ±price_band_pct of the reference price.

        매개변수
        ----------
        symbol : str
            Instrument (unused; reserved for per-symbol overrides).
        price : float
            Proposed order price.
        ref_price : float
            Reference price (e.g. previous day close or circuit-breaker base).

        반환값
        -------
        float
            Price clipped to the allowed band, then tick-rounded.
        """
        _ = symbol  # reserved
        if ref_price <= 0:
            return price
        lower = ref_price * (1.0 - self._price_band_pct)
        upper = ref_price * (1.0 + self._price_band_pct)
        clipped = max(lower, min(upper, price))
        return self.round_price(symbol, clipped)

    # ------------------------------------------------------------------
    # 수량 도우미
    # ------------------------------------------------------------------

    def validate_qty(self, qty: int) -> bool:
        """
        Return True when quantity meets the minimum order size requirement.

        매개변수
        ----------
        qty : int
            Absolute order quantity.

        반환값
        -------
        bool
        """
        return qty >= self._min_qty

    # ------------------------------------------------------------------
    # 주문 필터링
    # ------------------------------------------------------------------

    def apply_tradability_mask(
        self,
        orders: list[ParentOrder],
        tradable: dict[str, bool],
    ) -> list[ParentOrder]:
        """
        Reject / cancel orders for symbols that are currently non-tradable.

        매개변수
        ----------
        orders : list[ParentOrder]
        tradable : dict[str, bool]
            Symbol → whether trading is currently permitted.

        반환값
        -------
        list[ParentOrder]
            Orders where tradable[symbol] is True (or symbol not in dict).
            Non-tradable orders have their status set to REJECTED.
        """
        result: list[ParentOrder] = []
        for order in orders:
            if tradable.get(order.symbol, True):
                result.append(order)
            else:
                order.status = OrderStatus.REJECTED
                order.meta["reject_reason"] = "symbol_not_tradable"
        return result

    def apply_all(
        self,
        order: ParentOrder,
        state: MarketState,
    ) -> ParentOrder:
        """
        Apply all constraints to a ParentOrder in-place and return it.

        Checks applied
        --------------
        1. Tradability
        2. Lot-size rounding on total_qty
        3. Minimum quantity validation
        4. Price-band check on limit_price (if set)

        매개변수
        ----------
        order : ParentOrder
        state : MarketState

        반환값
        -------
        ParentOrder
            The (potentially modified) order.  Status set to REJECTED if
            any hard constraint is violated.
        """
        # 1. Tradability
        if not state.tradable:
            order.status = OrderStatus.REJECTED
            order.meta["reject_reason"] = "not_tradable"
            return order

        # 2. Halted session
        if state.session in ("halted", "closed"):
            order.status = OrderStatus.REJECTED
            order.meta["reject_reason"] = f"session_{state.session}"
            return order

        # 3. Lot rounding
        rounded_qty = self.round_qty(order.symbol, order.total_qty)
        if rounded_qty != order.total_qty:
            order.meta["qty_before_lot_round"] = order.total_qty
            order.total_qty = rounded_qty

        # 4. Minimum quantity
        if not self.validate_qty(order.total_qty):
            order.status = OrderStatus.REJECTED
            order.meta["reject_reason"] = f"qty_below_min ({order.total_qty} < {self._min_qty})"
            return order

        # 5. Price band on limit_price
        if order.limit_price is not None:
            ref_price = state.lob.mid_price or order.limit_price
            bounded_price = self.apply_price_band(order.symbol, order.limit_price, ref_price)
            if bounded_price != order.limit_price:
                order.meta["price_before_band"] = order.limit_price
                order.limit_price = bounded_price

        return order

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------

    def _get_tick(self, symbol: str) -> float:
        if isinstance(self._tick_size, dict):
            return self._tick_size.get(symbol, 1.0)
        return float(self._tick_size)

    def _get_lot(self, symbol: str) -> int:
        if isinstance(self._lot_size, dict):
            return self._lot_size.get(symbol, 1)
        return int(self._lot_size)
