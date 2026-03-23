from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from data.layer0_data import MarketStateBuilder


def _make_lob_df(n_steps: int = 6) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    start_ts = pd.Timestamp("2026-03-12 09:00:00")

    for step in range(n_steps):
        timestamp = start_ts + pd.Timedelta(milliseconds=200 * step)
        row: dict[str, object] = {"timestamp": timestamp}
        for level in range(1, 11):
            row[f"BIDP{level}"] = 100.0 - 0.01 * (level - 1)
            row[f"ASKP{level}"] = 100.01 + 0.01 * (level - 1)
            row[f"BIDP_RSQN{level}"] = 1_000 + 25 * level + step
            row[f"ASKP_RSQN{level}"] = 900 + 20 * level + step
        rows.append(row)

    return pd.DataFrame(rows)


def _make_trades_df() -> pd.DataFrame:
    start_ts = pd.Timestamp("2026-03-12 09:00:00")
    return pd.DataFrame(
        {
            "timestamp": [
                start_ts + pd.Timedelta(milliseconds=150),
                start_ts + pd.Timedelta(milliseconds=450),
                start_ts + pd.Timedelta(milliseconds=850),
            ],
            "price": [100.01, 100.02, 100.00],
            "volume": [30, 45, 25],
            "side": ["buy", "sell", "buy"],
        }
    )


def test_market_state_builder_builds_states_from_dataframes():
    builder = MarketStateBuilder()

    result = builder.build_from_dataframes(
        lob_df=_make_lob_df(),
        symbol="TEST",
        trades_df=_make_trades_df(),
    )

    assert result.n_input_rows == 6
    assert result.n_clean_rows == 6
    assert result.n_states == 6
    assert len(result.states) == 6

    first_state = result.states[0]
    assert first_state.symbol == "TEST"
    assert first_state.session == "regular"
    assert first_state.tradable is True
    assert first_state.trades is None
    assert "spread_bps" in first_state.features
    assert "order_imbalance" in first_state.features

    later_state = result.states[3]
    assert later_state.trades is not None
    assert len(later_state.trades) == 2
    assert list(later_state.trades.columns) == ["timestamp", "price", "volume", "side"]
    assert later_state.features["trade_flow"] != 0.0
