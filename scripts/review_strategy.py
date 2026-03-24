"""전략 사양 검토 스크립트 (StrategySpec v2 only)."""
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

from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2
from strategy_block.strategy_review.review_common import ReviewResult


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review StrategySpec v2 JSON")
    parser.add_argument("spec_path", help="Path to strategy spec JSON (v2)")
    return parser.parse_args()


def print_review(spec: StrategySpecV2, result: ReviewResult) -> None:
    print(f"\n{'─' * 50}")
    print(f"Reviewing: {spec.name} (v{spec.version}, format=v2)")
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

    spec_path = Path(args.spec_path)
    spec = StrategySpecV2.load(spec_path)
    reviewer = StrategyReviewerV2()

    result = reviewer.review(spec)
    print_review(spec, result)

    status = "PASSED" if result.passed else "FAILED"
    print(f"REVIEW_STATUS={status}")

    if not result.passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
