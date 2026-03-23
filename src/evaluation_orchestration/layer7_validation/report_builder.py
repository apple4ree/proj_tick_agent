"""
report_builder.py
-----------------
Report generation and persistence extracted from PipelineRunner.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from market_simulation.layer5_simulator.bookkeeper import FillEvent
    from execution_planning.layer3_order.order_types import ParentOrder
    from data.layer0_data.market_state import MarketState

from evaluation_orchestration.layer7_validation.backtest_config import BacktestConfig, BacktestResult

logger = logging.getLogger(__name__)


class ReportBuilder:
    """Generates evaluation reports from simulation data."""

    def __init__(self, config: BacktestConfig, pnl_ledger) -> None:
        self._config = config
        self._pnl_ledger = pnl_ledger

    def generate_reports(
        self,
        fills: list["FillEvent"],
        parent_orders: list["ParentOrder"],
        states: list["MarketState"],
        signals: list,
        portfolio_values: list[tuple],
        positions_history: list[dict],
        arrival_prices: dict[str, float],
        twap_prices: dict[str, float],
        run_id: str,
    ) -> BacktestResult:
        """Build all Layer 6 evaluation reports from accumulated simulation data."""
        from evaluation_orchestration.layer6_evaluator.risk_metrics import RiskMetrics
        from evaluation_orchestration.layer6_evaluator.execution_metrics import ExecutionMetrics
        from evaluation_orchestration.layer6_evaluator.turnover_metrics import TurnoverMetrics
        from evaluation_orchestration.layer6_evaluator.attribution import AttributionAnalyzer

        pnl_report = self._pnl_ledger.generate_report()

        cum_pnl = self._pnl_ledger.cumulative_pnl_series()
        risk_report = RiskMetrics.compute(
            pnl_series=cum_pnl,
            freq="tick",
            annualization_factor=self._config.annualization_factor,
        )

        exec_report = ExecutionMetrics.compute(fills, parent_orders, states)

        if portfolio_values:
            ts, vals = zip(*portfolio_values)
            pv_series = pd.Series(vals, index=pd.DatetimeIndex(ts))
        else:
            pv_series = pd.Series(dtype=float)

        turnover_report = TurnoverMetrics.compute(
            fills=fills,
            portfolio_values=pv_series,
            positions_history=positions_history,
            annualization_factor=self._config.annualization_factor,
        )

        attribution_report = None
        if self._config.compute_attribution and fills:
            attribution_report = AttributionAnalyzer.compute(
                fills=fills,
                signals=signals,
                parent_orders=parent_orders,
                states=states,
                arrival_prices=arrival_prices,
                twap_prices=twap_prices,
            )

        return BacktestResult(
            config=self._config,
            run_id=run_id,
            pnl_report=pnl_report,
            risk_report=risk_report,
            execution_report=exec_report,
            turnover_report=turnover_report,
            attribution_report=attribution_report,
            n_fills=len(fills),
            n_states=len(states),
        )

    def save_results(
        self,
        result: BacktestResult,
        output_dir: Path,
        signals: list | None = None,
        parent_orders: list | None = None,
        fills: list | None = None,
        states: list["MarketState"] | None = None,
    ) -> None:
        """Persist backtest results to disk."""
        output_dir = Path(output_dir)
        run_dir = output_dir / result.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        plots_dir = run_dir / "plots"
        plots_dir.mkdir(exist_ok=True)

        summary = result.summary()
        with open(run_dir / "summary.json", "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, default=str)

        with open(run_dir / "config.json", "w", encoding="utf-8") as fh:
            json.dump(result.config.to_dict(), fh, indent=2)

        pnl_series = result.pnl_report.pnl_series
        if pnl_series is not None and len(pnl_series) > 0:
            pnl_series.to_csv(run_dir / "pnl_series.csv", header=True)

        df = self._pnl_ledger.to_dataframe()
        if not df.empty:
            df.to_csv(run_dir / "pnl_entries.csv")

        if signals:
            self._save_signals(signals, run_dir)
        if parent_orders:
            self._save_orders(parent_orders, run_dir)
        if fills:
            self._save_fills(fills, run_dir)
        if states:
            self._save_market_quotes(states, run_dir)

        self._generate_plots(run_dir)
        logger.info("Results saved to %s", run_dir)

    # ------------------------------------------------------------------
    # Strategy artifact persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _save_signals(signals: list, run_dir: Path) -> None:
        rows = []
        for s in signals:
            row = {
                "timestamp": s.timestamp,
                "symbol": s.symbol,
                "score": s.score,
                "expected_return": s.expected_return,
                "confidence": s.confidence,
                "horizon_steps": s.horizon_steps,
                "is_valid": s.is_valid,
            }
            if s.tags:
                for k, v in s.tags.items():
                    row[f"tag_{k}"] = v
            rows.append(row)
        pd.DataFrame(rows).to_csv(run_dir / "signals.csv", index=False)
        logger.info("Saved %d signals", len(rows))

    @staticmethod
    def _save_orders(parent_orders: list, run_dir: Path) -> None:
        rows = []
        for p in parent_orders:
            rows.append({
                "order_id": p.order_id,
                "symbol": p.symbol,
                "side": p.side.name,
                "total_qty": p.total_qty,
                "filled_qty": p.filled_qty,
                "remaining_qty": p.remaining_qty,
                "urgency": p.urgency,
                "status": p.status.name,
                "arrival_mid": getattr(p, "arrival_mid", None),
                "avg_fill_price": p.avg_fill_price,
                "n_children": len(p.child_orders),
                "fill_rate": p.fill_rate,
            })
        pd.DataFrame(rows).to_csv(run_dir / "orders.csv", index=False)
        logger.info("Saved %d parent orders", len(rows))

    @staticmethod
    def _save_fills(fills: list, run_dir: Path) -> None:
        rows = []
        for f in fills:
            rows.append({
                "timestamp": f.timestamp,
                "symbol": f.symbol,
                "side": f.side.name if hasattr(f.side, "name") else f.side,
                "filled_qty": f.filled_qty,
                "fill_price": f.fill_price,
                "fee": f.fee,
                "slippage_bps": f.slippage_bps,
                "market_impact_bps": f.market_impact_bps,
                "latency_ms": f.latency_ms,
                "parent_id": f.parent_id,
                "order_id": f.order_id,
            })
        pd.DataFrame(rows).to_csv(run_dir / "fills.csv", index=False)
        logger.info("Saved %d fills", len(rows))

    @staticmethod
    def _save_market_quotes(states: list, run_dir: Path) -> None:
        """Save market quote snapshots for visualization."""
        rows = []
        for s in states:
            lob = s.lob
            rows.append({
                "timestamp": s.timestamp,
                "symbol": s.symbol,
                "best_bid": lob.best_bid,
                "best_ask": lob.best_ask,
                "mid_price": lob.mid_price,
            })
        if rows:
            pd.DataFrame(rows).to_csv(run_dir / "market_quotes.csv", index=False)
            logger.info("Saved %d market quote snapshots", len(rows))

    @staticmethod
    def _generate_plots(run_dir: Path) -> None:
        """Generate visualization plots from saved CSVs."""
        try:
            from scripts.visualize import generate_all_plots

            paths = generate_all_plots(run_dir, show=False)
            logger.info("Generated %d plots in %s", len(paths), run_dir / "plots")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Plot generation skipped: %s", exc)
