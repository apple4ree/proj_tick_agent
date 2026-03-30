from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")


def test_tier1_docs_reference_freeze_contracts() -> None:
    tier1_paths = [
        "PIPELINE.md",
        "scripts/README.md",
        "src/strategy_block/strategy_generation/README.md",
        "src/strategy_block/strategy_review/README.md",
        "src/evaluation_orchestration/layer7_validation/README.md",
    ]
    for rel_path in tier1_paths:
        text = _read(rel_path)
        assert "benchmark_freeze_protocol.md" in text


def test_scripts_readme_public_cli_surface_present() -> None:
    text = _read("scripts/README.md")
    expected = {
        "generate_strategy.py",
        "review_strategy.py",
        "backtest.py",
        "backtest_strategy_universe.py",
        "run_generate_review_backtest.sh",
    }
    for name in expected:
        assert name in text


def test_scripts_readme_plot_names_present() -> None:
    text = _read("scripts/README.md")
    expected_plots = {
        "overview.png",
        "signal_analysis.png",
        "execution_quality.png",
        "dashboard.png",
        "intraday_cumulative_profit.png",
        "trade_timeline.png",
        "equity_risk.png",
        "realism_dashboard.png",
    }
    for name in expected_plots:
        assert name in text


def test_review_readme_artifact_names_present() -> None:
    text = _read("src/strategy_block/strategy_review/README.md")
    expected_artifacts = {
        "static_review.json",
        "llm_review.json",
        "repair_plan.json",
        "repaired_spec.json",
        "final_static_review.json",
    }
    for name in expected_artifacts:
        assert name in text


def test_layer7_readme_core_artifact_contract_names_present() -> None:
    text = _read("src/evaluation_orchestration/layer7_validation/README.md")
    assert "summary.json" in text
    assert "realism_diagnostics.json" in text
    for name in {"overview.png", "trade_timeline.png", "equity_risk.png", "realism_dashboard.png"}:
        assert name in text


def test_docs_index_declares_authoritative_tiers() -> None:
    text = _read("docs/README.md")
    assert "Tier 1" in text
    assert "Tier 2" in text
    assert "Tier 3" in text
    assert "analysis/benchmark_freeze_protocol.md" in text
