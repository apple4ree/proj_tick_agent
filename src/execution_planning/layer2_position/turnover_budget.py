"""
turnover_budget.py
------------------
Transaction cost and turnover budget management for Layer 2.

클래스
-----
TurnoverBudget  - Estimates and enforces turnover / cost budgets
"""
from __future__ import annotations


class TurnoverBudget:
    """
    Manages trade execution budget defined by daily turnover and cost limits.

    Turnover is defined as the sum of absolute trade values divided by
    portfolio NAV.  Transaction cost is estimated from spread and volume.

    매개변수
    ----------
    daily_turnover_limit : float
        Maximum allowed daily turnover as fraction of portfolio value (default 0.5).
        E.g. 0.5 means trades up to 50% of the portfolio NAV per day.
    cost_per_bps : float
        Notional cost per basis point of spread in currency units per 1bps.
        Used as a rough per-trade cost multiplier (default 5.0).
    max_cost_budget : float
        Maximum allowed total estimated transaction cost per cycle (default 50.0).
    min_holding_steps : int
        Minimum number of steps a position must be held before being changed
        (default 5).  Enforced externally via check_min_holding().
    """

    def __init__(
        self,
        daily_turnover_limit: float = 0.5,
        cost_per_bps: float = 5.0,
        max_cost_budget: float = 50.0,
        min_holding_steps: int = 5,
    ) -> None:
        self._turnover_limit = daily_turnover_limit
        self._cost_per_bps = cost_per_bps
        self._max_cost_budget = max_cost_budget
        self._min_holding_steps = min_holding_steps

    # ------------------------------------------------------------------
    # 회전율 계산
    # ------------------------------------------------------------------

    def compute_turnover(
        self,
        current: dict[str, int],
        targets: dict[str, int],
        prices: dict[str, float],
        portfolio_value: float,
    ) -> float:
        """
        Compute the one-way turnover implied by moving from current to target.

        Turnover = sum(|delta_qty| * price) / portfolio_value

        매개변수
        ----------
        current : dict[str, int]
            Current positions.
        targets : dict[str, int]
            Target positions.
        prices : dict[str, float]
            Symbol → current price.
        portfolio_value : float
            포트폴리오 NAV.

        반환값
        -------
        float
            Turnover as a fraction of portfolio value.
        """
        if portfolio_value <= 0:
            return 0.0

        all_syms = set(current.keys()) | set(targets.keys())
        total_trade_value = 0.0
        for sym in all_syms:
            delta = targets.get(sym, 0) - current.get(sym, 0)
            price = prices.get(sym, 0.0)
            total_trade_value += abs(delta) * price

        return total_trade_value / portfolio_value

    # ------------------------------------------------------------------
    # 비용 추정
    # ------------------------------------------------------------------

    def estimate_cost(
        self,
        current: dict[str, int],
        targets: dict[str, int],
        prices: dict[str, float],
        spread_bps: dict[str, float],
    ) -> float:
        """
        Estimate total transaction cost in bps for the proposed trades.

        Cost per trade = 0.5 * spread_bps[sym] (half-spread, one-way)
        Total cost = sum(|delta_qty| * price * cost_bps / 10000)

        매개변수
        ----------
        current : dict[str, int]
        targets : dict[str, int]
        prices : dict[str, float]
        spread_bps : dict[str, float]
            Symbol → current spread in basis points.

        반환값
        -------
        float
            Total estimated cost in currency units.
        """
        all_syms = set(current.keys()) | set(targets.keys())
        total_cost = 0.0
        for sym in all_syms:
            delta = abs(targets.get(sym, 0) - current.get(sym, 0))
            if delta == 0:
                continue
            price = prices.get(sym, 0.0)
            half_spread = spread_bps.get(sym, 10.0) / 2.0  # default 10bps half-spread
            cost_fraction = half_spread / 10_000.0
            total_cost += delta * price * cost_fraction * self._cost_per_bps
        return total_cost

    # ------------------------------------------------------------------
    # 예산 점검
    # ------------------------------------------------------------------

    def is_within_budget(
        self,
        current: dict[str, int],
        targets: dict[str, int],
        prices: dict[str, float],
        portfolio_value: float,
    ) -> bool:
        """
        Return True when the proposed trades fit within the turnover budget.

        매개변수
        ----------
        current : dict[str, int]
        targets : dict[str, int]
        prices : dict[str, float]
        portfolio_value : float

        반환값
        -------
        bool
        """
        turnover = self.compute_turnover(current, targets, prices, portfolio_value)
        return turnover <= self._turnover_limit

    def throttle(
        self,
        current: dict[str, int],
        targets: dict[str, int],
        prices: dict[str, float],
        portfolio_value: float,
    ) -> dict[str, int]:
        """
        Scale down proposed trades proportionally to fit the turnover budget.

        Symbols with the largest planned trade values are scaled down first
        (proportional scaling applied uniformly).

        매개변수
        ----------
        current : dict[str, int]
        targets : dict[str, int]
        prices : dict[str, float]
        portfolio_value : float

        반환값
        -------
        dict[str, int]
            Adjusted targets that respect the turnover budget.
        """
        turnover = self.compute_turnover(current, targets, prices, portfolio_value)
        if turnover <= self._turnover_limit or turnover == 0.0:
            return dict(targets)

        # Scale factor: reduce all trades by the same factor
        scale = self._turnover_limit / turnover
        adjusted: dict[str, int] = {}
        for sym, target_qty in targets.items():
            current_qty = current.get(sym, 0)
            delta = target_qty - current_qty
            scaled_delta = int(round(delta * scale))
            adjusted[sym] = current_qty + scaled_delta

        return adjusted

    # ------------------------------------------------------------------
    # 보유 기간 점검
    # ------------------------------------------------------------------

    def check_min_holding(self, symbol: str, steps_held: int) -> bool:
        """
        Return True when a position has been held long enough to be changed.

        매개변수
        ----------
        symbol : str
            Symbol identifier (unused here, reserved for per-symbol overrides).
        steps_held : int
            Number of steps the current position has been open.

        반환값
        -------
        bool
        """
        _ = symbol  # reserved for per-symbol overrides in future
        return steps_held >= self._min_holding_steps
