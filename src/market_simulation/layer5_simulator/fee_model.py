"""
fee_model.py
------------
Transaction fee models for Layer 5.

Models brokerage commissions and exchange taxes for Korean equity markets (KRX).

KRX fee structure (as of 2024):
  - Securities transaction tax (증권거래세): 0.18% on KOSPI sells, 0.20% on KOSDAQ
  - Brokerage commission: typically 0.015% retail, ~0.005% institutional/HFT
  - No stamp duty or clearing fee at the strategy level (included in above)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from execution_planning.layer3_order.order_types import OrderSide

from execution_planning.layer3_order.order_types import OrderSide


class FeeModel(ABC):
    """추상 기반 class for transaction fee models."""

    @abstractmethod
    def compute(
        self,
        qty: int,
        price: float,
        side: OrderSide,
        is_maker: bool,
    ) -> float:
        """
        Compute the total fee in KRW (absolute amount).

        매개변수
        ----------
        qty : int
        price : float
        side : OrderSide
        is_maker : bool
            True if the order rested on the book and was filled passively.
            Some venues offer maker rebates (negative fees).

        반환값
        -------
        float
            Fee in KRW.
        """
        ...

    @abstractmethod
    def compute_bps(
        self,
        qty: int,
        price: float,
        side: OrderSide,
        is_maker: bool,
    ) -> float:
        """
        Compute the total fee as basis points relative to notional value.

        반환값
        -------
        float
            Fee in bps.
        """
        ...

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _notional(qty: int, price: float) -> float:
        return float(qty) * float(price)


# ---------------------------------------------------------------------------
# KRX fee model
# ---------------------------------------------------------------------------

class KRXFeeModel(FeeModel):
    """
    Korean Stock Exchange (KRX) fee model.

    Components
    ----------
    1. Brokerage commission — charged on both buys and sells.
    2. Securities transaction tax — charged on sells only.
       KOSPI: 0.18% (as of 2024, phased reduction from 0.25%)
       KOSDAQ: 0.20%

    Maker/taker distinction: KRX does not currently differentiate commissions
    by maker/taker for equity markets; the `is_maker` parameter is kept for
    API consistency but does not affect the calculation.

    매개변수
    ----------
    commission_bps : float
        Brokerage commission in bps (both sides).  Default 1.5 bps = 0.015%.
    market : str
        'KOSPI' or 'KOSDAQ'.
    include_tax : bool
        Include securities transaction tax on sell orders.
    """

    TRANSACTION_TAX: dict[str, float] = {
        "KOSPI": 18.0,    # bps (0.18%)
        "KOSDAQ": 20.0,   # bps (0.20%)
    }

    def __init__(
        self,
        commission_bps: float = 1.5,
        market: str = "KOSPI",
        include_tax: bool = True,
    ) -> None:
        if market not in self.TRANSACTION_TAX:
            raise ValueError(
                f"Unknown market '{market}'. Expected one of {list(self.TRANSACTION_TAX)}"
            )
        self.commission_bps = commission_bps
        self.market = market
        self.include_tax = include_tax

    def compute(
        self,
        qty: int,
        price: float,
        side: OrderSide,
        is_maker: bool,
    ) -> float:
        """Return total fee in KRW."""
        notional = self._notional(qty, price)
        commission = notional * self.commission_bps / 10_000.0
        tax = 0.0
        if self.include_tax and side == OrderSide.SELL:
            tax = notional * self.TRANSACTION_TAX[self.market] / 10_000.0
        return commission + tax

    def compute_bps(
        self,
        qty: int,
        price: float,
        side: OrderSide,
        is_maker: bool,
    ) -> float:
        """Return total fee in bps."""
        total_fee = self.compute(qty, price, side, is_maker)
        notional = self._notional(qty, price)
        if notional == 0.0:
            return 0.0
        return (total_fee / notional) * 10_000.0

    @property
    def total_sell_bps(self) -> float:
        """Convenience: total cost for a sell (commission + tax) in bps."""
        tax = self.TRANSACTION_TAX[self.market] if self.include_tax else 0.0
        return self.commission_bps + tax

    @property
    def total_buy_bps(self) -> float:
        """Convenience: total cost for a buy (commission only) in bps."""
        return self.commission_bps


# ---------------------------------------------------------------------------
# Zero fee model (testing)
# ---------------------------------------------------------------------------

class ZeroFeeModel(FeeModel):
    """No fees — useful for unit tests and impact-isolation studies."""

    def compute(
        self,
        qty: int,
        price: float,
        side: OrderSide,
        is_maker: bool,
    ) -> float:
        return 0.0

    def compute_bps(
        self,
        qty: int,
        price: float,
        side: OrderSide,
        is_maker: bool,
    ) -> float:
        return 0.0
