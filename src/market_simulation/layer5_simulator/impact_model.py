"""
impact_model.py
---------------
Market impact models for Layer 5.

Models the price impact of executing a child order, split into:
  - Temporary impact : immediate execution shortfall (reverts after trade)
  - Permanent impact : lasting price change (information leakage)

Additionally, SpreadCostModel captures the cost of crossing the bid-ask spread.

All impact values are returned in basis points (bps), and adjust_price()
converts them to an absolute price adjustment.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from execution_planning.layer3_order.order_types import ChildOrder, OrderSide
    from data.layer0_data.market_state import MarketState

from execution_planning.layer3_order.order_types import OrderSide, OrderType


class ImpactModel(ABC):
    """추상 기반 class for market impact models."""

    @abstractmethod
    def temporary_impact(self, qty: int, adv: float, mid: float) -> float:
        """
        Estimate temporary (instantaneous) market impact in bps.

        매개변수
        ----------
        qty : int
            Number of shares executed.
        adv : float
            Average daily volume (shares) — used to normalise order size.
        mid : float
            Current mid price.

        반환값
        -------
        float
            Impact in basis points.
        """
        ...

    @abstractmethod
    def permanent_impact(self, qty: int, adv: float, mid: float) -> float:
        """
        Estimate permanent (lasting) market impact in bps.
        """
        ...

    def adjust_price(
        self,
        base_price: float,
        qty: int,
        adv: float,
        mid: float,
        side: OrderSide,
    ) -> float:
        """
        Apply the temporary impact to `base_price`.

        For a BUY order the impact raises the effective price.
        For a SELL order the impact lowers the effective price.

        impact_bps → price_delta = mid * impact_bps / 10_000
        """
        impact_bps = self.temporary_impact(qty, adv, mid)
        price_delta = mid * impact_bps / 10_000.0
        if side == OrderSide.BUY:
            return base_price + price_delta
        else:
            return base_price - price_delta


# ---------------------------------------------------------------------------
# Linear impact (Almgren-Chriss linear component)
# ---------------------------------------------------------------------------

class LinearImpact(ImpactModel):
    """
    Linear market impact model.

    temporary_impact = eta  * (qty / adv)  [bps]
    permanent_impact = gamma * (qty / adv) [bps]

    매개변수
    ----------
    eta   : float  temporary impact coefficient
    gamma : float  permanent impact coefficient
    """

    def __init__(self, eta: float = 0.1, gamma: float = 0.01) -> None:
        self.eta = eta
        self.gamma = gamma

    def temporary_impact(self, qty: int, adv: float, mid: float) -> float:
        if adv <= 0.0:
            return 0.0
        return self.eta * (qty / adv) * 10_000.0  # convert fraction → bps

    def permanent_impact(self, qty: int, adv: float, mid: float) -> float:
        if adv <= 0.0:
            return 0.0
        return self.gamma * (qty / adv) * 10_000.0


# ---------------------------------------------------------------------------
# Square-root impact (Almgren-Chriss / empirical)
# ---------------------------------------------------------------------------

class SquareRootImpact(ImpactModel):
    """
    Square-root market impact model.

    Based on the empirical observation that temporary impact scales with the
    square root of participation rate (Almgren et al. 2005, Gatheral 2010).

    temporary_impact = sigma * kappa * sqrt(qty / adv)  [bps]
    permanent_impact = sigma * gamma * (qty / adv)      [bps]

    매개변수
    ----------
    sigma : float  변동성 (fraction, e.g. 0.01 = 1 %)
    kappa : float  temporary impact coefficient
    gamma : float  permanent impact coefficient
    """

    def __init__(
        self,
        sigma: float = 0.01,
        kappa: float = 0.1,
        gamma: float = 0.01,
    ) -> None:
        self.sigma = sigma
        self.kappa = kappa
        self.gamma = gamma

    def temporary_impact(self, qty: int, adv: float, mid: float) -> float:
        if adv <= 0.0 or qty <= 0:
            return 0.0
        participation = qty / adv
        return self.sigma * self.kappa * math.sqrt(participation) * 10_000.0

    def permanent_impact(self, qty: int, adv: float, mid: float) -> float:
        if adv <= 0.0:
            return 0.0
        return self.sigma * self.gamma * (qty / adv) * 10_000.0


# ---------------------------------------------------------------------------
# Zero impact (testing)
# ---------------------------------------------------------------------------

class ZeroImpact(ImpactModel):
    """No impact — useful for unit tests and baseline comparisons."""

    def temporary_impact(self, qty: int, adv: float, mid: float) -> float:
        return 0.0

    def permanent_impact(self, qty: int, adv: float, mid: float) -> float:
        return 0.0


# ---------------------------------------------------------------------------
# Spread cost model (separate from impact)
# ---------------------------------------------------------------------------

class SpreadCostModel:
    """
    Models the cost of crossing (or partially crossing) the bid-ask spread.

    This is distinct from market impact: spread cost arises purely from the
    bid-ask friction, independent of order size effects on price.

    매개변수
    ----------
    fill_half_spread : bool
        If True, assume passive orders pay 0 and aggressive orders pay the
        full spread.  If False, all orders pay half the spread (symmetric).
    """

    def __init__(self, fill_half_spread: bool = True) -> None:
        self.fill_half_spread = fill_half_spread

    def cost(
        self,
        child: ChildOrder,
        state: MarketState,
    ) -> float:
        """
        Compute spread-crossing cost in bps.

        반환값
        -------
        float
            Spread cost in bps.  0 if market data is unavailable.
        """
        spread_bps = state.lob.spread_bps
        if spread_bps is None:
            return 0.0

        if child.order_type == OrderType.MARKET:
            # 시장가 주문은 전체 스프레드를 부담한다
            return spread_bps

        # LIMIT 주문은 스프레드를 넘는지, 경계에 있는지, 내부에 있는지 점검한다
        mid = state.mid
        if mid is None or child.price is None:
            return 0.0

        lob = state.lob
        if child.side == OrderSide.BUY:
            best_ask = lob.best_ask
            if best_ask is None:
                return 0.0
            if child.price >= best_ask:
                # 스프레드를 넘기면 전체 스프레드를 부담한다
                return spread_bps
            best_bid = lob.best_bid
            if best_bid is None:
                return 0.0
            if child.price <= best_bid:
                # 최우선 매수호가 이하에 게시하면 스프레드 비용이 없다(메이커)
                return 0.0
            # 스프레드 내부면 보간한다
            fraction = (child.price - best_bid) / (best_ask - best_bid)
            return spread_bps * fraction
        else:
            best_bid = lob.best_bid
            if best_bid is None:
                return 0.0
            if child.price <= best_bid:
                # 스프레드를 넘기면 전체 스프레드를 부담한다
                return spread_bps
            best_ask = lob.best_ask
            if best_ask is None:
                return 0.0
            if child.price >= best_ask:
                # 최우선 매도호가 이상에 게시하면 스프레드 비용이 없다(메이커)
                return 0.0
            # 스프레드 내부면 보간한다
            fraction = (best_ask - child.price) / (best_ask - best_bid)
            return spread_bps * fraction
