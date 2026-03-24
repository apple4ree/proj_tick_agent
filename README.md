# proj_rl_agent

틱 데이터 기반 전략 생성/검토/컴파일/백테스트를 수행하는 **StrategySpec v2-only** 연구/검증 워크스페이스.

## 현재 상태 (요약)
- 전략 사양: **v2-only**
- 기본 흐름: `generate -> review -> compile -> backtest`
- 단일 종목 실데이터 백테스트 스모크 실행 가능
- smoke는 **quick wiring check** 용도이며 품질 보증 테스트가 아님

## Quick Start
```bash
cd /home/dgu/tick/proj_rl_agent

# 1) 전략 생성 (직접 생성)
PYTHONPATH=src python scripts/generate_strategy.py \
  --goal "order imbalance alpha" --direct --backend template --mode mock

# 2) 전략 검토
PYTHONPATH=src python scripts/review_strategy.py \
  strategies/examples/stateful_cooldown_momentum_v2.0.json

# 3) 단일 종목 백테스트
PYTHONPATH=src python scripts/backtest.py \
  --spec strategies/examples/stateful_cooldown_momentum_v2.0.json \
  --symbol 005930 --start-date 20260313 --profile smoke
```

## 핵심 디렉토리
- `src/data/`: 데이터 적재/정제/상태 빌드
- `src/strategy_block/`: v2 generation/review/spec/registry/compiler
- `src/execution_planning/`: signal -> target -> order -> execution planning
- `src/market_simulation/`: fill/latency/impact/fee 시뮬레이션
- `src/evaluation_orchestration/`: backtest pipeline/metrics/job worker
- `scripts/`: 운영 CLI/런처
- `strategies/`: runtime registry 저장소
- `strategies/examples/`: reference v2 샘플

## Validation Tiers
- smoke: CLI/help/짧은 경로의 배선 확인
- stronger: fill/latency/impact가 실제 발생하는 통합/회귀 검증
- 기본 plot 출력: 5종 (overview, signal_analysis, execution_quality, dashboard, intraday_cumulative_profit)

## examples vs registry
- `strategies/examples/`: 참고용 정적 샘플
- `strategies/`: generate 또는 worker가 저장/읽는 실제 registry 경로

## 현재 한계
- execution policy는 일부 필드만 downstream에서 hint-level 반영
- reviewer는 static/heuristic 규칙 기반 점검
- production OMS/live trading 엔진 완성 상태는 아님
