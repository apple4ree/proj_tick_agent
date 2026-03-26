"""
KIS H0STASP0 데이터 수집 래퍼 스크립트.

open-trading-api의 tick_hoka_collector_krx.py를 호출하여
10호가 CSV 데이터를 수집합니다.

사용법:
    cd /home/dgu/tick/proj_rl_agent
    PYTHONPATH=. python scripts/internal/adhoc/collect_data.py 005930 000660
    PYTHONPATH=. python scripts/internal/adhoc/collect_data.py --symbols 005930 000660 138080 --out-dir data/raw
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


# 실제 수집기 스크립트 경로
COLLECTOR_SCRIPT = (
    Path("/home/dgu/tick/open-trading-api")
    / "examples_llm"
    / "domestic_stock"
    / "tick_hoka_collector_krx.py"
)

# 기본 출력 디렉터리(호환성을 위해 open-trading-api 내부 사용)
DEFAULT_OUT_DIR = "/home/dgu/tick/open-trading-api/data/realtime"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="KIS 실시간 10레벨 LOB(H0STASP0) 데이터를 수집합니다.",
        epilog=(
            "This is a convenience wrapper around the open-trading-api collector.\n"
            "Requires KIS API credentials configured in ~/KIS/config/kis_devlp.yaml\n"
            "KIS_CONFIG_PATH 환경 변수에 설정된 KIS API 자격 증명이 필요합니다."
        ),
    )
    p.add_argument(
        "symbols",
        nargs="*",
        help="KRX 종목 코드(예: 005930 000660), 최대 40개.",
    )
    p.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"출력 directory (default: {DEFAULT_OUT_DIR})",
    )
    p.add_argument(
        "--session",
        choices=["auto", "regular", "overtime"],
        default="auto",
        help="거래 세션(auto/regular/overtime), 기본값: auto",
    )
    p.add_argument(
        "--flush-rows",
        type=int,
        default=1000,
        help="N행마다 디스크에 기록(기본값: 1000)",
    )
    p.add_argument(
        "--flush-seconds",
        type=float,
        default=1.0,
        help="T초마다 디스크에 기록(기본값: 1.0)",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        help="로깅 레벨(기본값: INFO)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if not args.symbols:
        print("오류: 최소 한 개의 종목 코드는 필요합니다.")
        print("예시: python scripts/internal/adhoc/collect_data.py 005930 000660")
        sys.exit(1)

    if not COLLECTOR_SCRIPT.exists():
        print(f"오류: 수집기 스크립트를 찾을 수 없습니다: {COLLECTOR_SCRIPT}")
        print("open-trading-api가 /home/dgu/tick/open-trading-api 에 클론되어 있는지 확인하세요.")
        sys.exit(1)

    # 명령 구성
    cmd = [
        sys.executable,
        str(COLLECTOR_SCRIPT),
        *args.symbols,
        "--out-dir", args.out_dir,
        "--session", args.session,
        "--flush-rows", str(args.flush_rows),
        "--flush-seconds", str(args.flush_seconds),
        "--log-level", args.log_level,
    ]

    print("=" * 60)
    print("KIS LOB 데이터 수집기")
    print("=" * 60)
    print(f"  종목:    {', '.join(args.symbols)}")
    print(f"  출력:   {args.out_dir}")
    print(f"  세션:    {args.session}")
    print(f"  스크립트: {COLLECTOR_SCRIPT}")
    print("=" * 60)
    print()

    # 수집기 실행(블로킹, 중단은 Ctrl+C)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(COLLECTOR_SCRIPT.parent.parent.parent),
            check=True,
        )
    except KeyboardInterrupt:
        print("\n\n사용자가 수집을 중단했습니다.")
    except subprocess.CalledProcessError as e:
        print(f"\n수집기가 오류 코드와 함께 종료되었습니다: {e.returncode}")
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
