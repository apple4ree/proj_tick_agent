"""
전략 사양 검토 스크립트.

사용법:
    cd /home/dgu/tick/proj_rl_agent
    PYTHONPATH=src python scripts/review_strategy.py strategies/imbalance_momentum_v1.0.json
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from strategy_block.strategy_specs.schema import StrategySpec
from strategy_block.strategy_review import StrategyReviewer, ReviewResult


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review strategy specifications")
    parser.add_argument("spec_path", help="Path to strategy spec JSON")
    return parser.parse_args()


def print_review(spec: StrategySpec, result: ReviewResult) -> None:
    """Print review results for a single spec."""
    print(f"\n{'─' * 50}")
    print(f"Reviewing: {spec.name} (v{spec.version})")
    print(f"{'─' * 50}")

    status = "PASSED" if result.passed else "FAILED"
    print(f"  Review: {status}")

    if result.issues:
        for issue in result.issues:
            sev = issue.severity.upper()
            cat = issue.category
            print(f"    [{sev}] ({cat}) {issue.description}")
            if issue.suggestion:
                print(f"           -> {issue.suggestion}")
    else:
        print("  No issues found.")


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.WARNING)

    reviewer = StrategyReviewer()
    spec = StrategySpec.load(args.spec_path)
    result = reviewer.review(spec)
    print_review(spec, result)

    # Machine-friendly output for shell script parsing
    status = "PASSED" if result.passed else "FAILED"
    print(f"REVIEW_STATUS={status}")

    if not result.passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
