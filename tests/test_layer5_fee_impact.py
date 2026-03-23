"""
Tests for Layer 5 fee and impact models.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from execution_planning.layer3_order.order_types import OrderSide
from market_simulation.layer5_simulator.fee_model import KRXFeeModel, ZeroFeeModel
from market_simulation.layer5_simulator.impact_model import LinearImpact, SquareRootImpact, ZeroImpact


# ── 수수료 모델s ────────────────────────────────────────────────────

class TestKRXFeeModel:
    def test_buy_commission_only(self):
        fee = KRXFeeModel(commission_bps=1.5, market="KOSPI")
        result = fee.compute(qty=100, price=50000.0, side=OrderSide.BUY, is_maker=False)
        # notional = 100 * 50000 = 5_000_000
        # commission = 5_000_000 * 1.5 / 10_000 = 750
        assert result == pytest.approx(750.0)

    def test_sell_includes_tax(self):
        fee = KRXFeeModel(commission_bps=1.5, market="KOSPI", include_tax=True)
        result = fee.compute(qty=100, price=50000.0, side=OrderSide.SELL, is_maker=False)
        # commission = 750
        # tax = 5_000_000 * 18 / 10_000 = 9000
        assert result == pytest.approx(750.0 + 9000.0)

    def test_sell_no_tax(self):
        fee = KRXFeeModel(commission_bps=1.5, market="KOSPI", include_tax=False)
        result = fee.compute(qty=100, price=50000.0, side=OrderSide.SELL, is_maker=False)
        assert result == pytest.approx(750.0)

    def test_kosdaq_higher_tax(self):
        fee_kospi = KRXFeeModel(commission_bps=0, market="KOSPI")
        fee_kosdaq = KRXFeeModel(commission_bps=0, market="KOSDAQ")
        sell_kospi = fee_kospi.compute(100, 50000, OrderSide.SELL, False)
        sell_kosdaq = fee_kosdaq.compute(100, 50000, OrderSide.SELL, False)
        assert sell_kosdaq > sell_kospi

    def test_invalid_market_raises(self):
        with pytest.raises(ValueError):
            KRXFeeModel(market="NYSE")

    def test_zero_notional(self):
        fee = KRXFeeModel()
        result_bps = fee.compute_bps(qty=0, price=50000.0, side=OrderSide.BUY, is_maker=False)
        assert result_bps == 0.0

    def test_buy_bps_equals_commission(self):
        fee = KRXFeeModel(commission_bps=1.5, market="KOSPI")
        result = fee.compute_bps(100, 50000, OrderSide.BUY, False)
        assert result == pytest.approx(1.5)


class TestZeroFeeModel:
    def test_always_zero(self):
        fee = ZeroFeeModel()
        assert fee.compute(100, 50000, OrderSide.BUY, False) == 0.0
        assert fee.compute(100, 50000, OrderSide.SELL, True) == 0.0
        assert fee.compute_bps(100, 50000, OrderSide.BUY, False) == 0.0


# ── Impact Models ─────────────────────────────────────────────────

class TestLinearImpact:
    def test_temporary_increases_with_qty(self):
        model = LinearImpact(eta=0.1)
        impact_small = model.temporary_impact(qty=10, adv=10000, mid=50000)
        impact_large = model.temporary_impact(qty=1000, adv=10000, mid=50000)
        assert impact_large > impact_small

    def test_zero_adv_returns_zero(self):
        model = LinearImpact()
        assert model.temporary_impact(qty=100, adv=0, mid=50000) == 0.0

    def test_adjust_price_buy_increases(self):
        model = LinearImpact(eta=0.1)
        base = 50000.0
        adjusted = model.adjust_price(base, qty=100, adv=10000, mid=50000, side=OrderSide.BUY)
        assert adjusted > base

    def test_adjust_price_sell_decreases(self):
        model = LinearImpact(eta=0.1)
        base = 50000.0
        adjusted = model.adjust_price(base, qty=100, adv=10000, mid=50000, side=OrderSide.SELL)
        assert adjusted < base


class TestSquareRootImpact:
    def test_temporary_scales_sublinearly(self):
        model = SquareRootImpact(sigma=0.01, kappa=0.1)
        i1 = model.temporary_impact(qty=100, adv=10000, mid=50000)
        i4 = model.temporary_impact(qty=400, adv=10000, mid=50000)
        # sqrt scaling: 4x qty → 2x impact
        assert i4 == pytest.approx(2 * i1, rel=0.01)

    def test_zero_qty_returns_zero(self):
        model = SquareRootImpact()
        assert model.temporary_impact(qty=0, adv=10000, mid=50000) == 0.0


class TestZeroImpact:
    def test_all_zero(self):
        model = ZeroImpact()
        assert model.temporary_impact(100, 10000, 50000) == 0.0
        assert model.permanent_impact(100, 10000, 50000) == 0.0
        base = 50000.0
        assert model.adjust_price(base, 100, 10000, 50000, OrderSide.BUY) == base
