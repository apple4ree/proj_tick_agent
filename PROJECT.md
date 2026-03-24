# Project Map (v2-only)

## Root Layout

- `src/`: 실행 로직
- `scripts/`: 운영/검증 CLI
- `conf/`: YAML 설정 및 프로필
- `strategies/`: runtime registry
- `strategies/examples/`: reference v2 examples
- `tests/`: pytest suites
- `outputs/`: 실행 산출물(코드 본체 아님)
- `experiments/`, `checkpoints/`: 실험/학습 산출물(코드 본체 아님)

## `src/` Subtrees

- `src/data/`
  - layer0 데이터 적재/정제/동기화/상태 생성
- `src/strategy_block/`
  - `strategy_generation/` v2 전략 생성
  - `strategy_review/` v2 정적 검토
  - `strategy_specs/` v2 spec/AST
  - `strategy_registry/` spec+metadata 저장소
  - `strategy_compiler/` v2 spec 컴파일
- `src/execution_planning/`
  - signal -> target -> order -> execution planning
- `src/market_simulation/`
  - fill/impact/fee/latency
- `src/evaluation_orchestration/`
  - backtest pipeline + metrics + job worker

## Scripts

- generation: `scripts/generate_strategy.py`, `scripts/run_generation_worker.py`
- review: `scripts/review_strategy.py`
- backtest: `scripts/backtest.py`, `scripts/backtest_strategy_universe.py`
- workers/launcher: `scripts/run_backtest_worker.py`, `scripts/run_local_stack.sh`, `scripts/run_generate_review_backtest.sh`

## Current Operational Status

- v2-only 경로: generation/review/registry/compiler/backtest
- single-symbol backtest: operational
- 기본 시각화 산출물: 5개 plot (intraday cumulative profit 포함)
- worker path: available

## Maturity Snapshot

Implemented:
- v2 spec lifecycle (generate/review/save/load/compile/backtest)
- registry metadata gate + worker orchestration

Partial:
- execution policy 일부는 hint-level/partial override
- reviewer는 static/heuristic 점검

Cleanup candidates:
- outputs/experiments/checkpoints 산출물 주기적 정리
- 문서/설정의 mixed change 분리 커밋

## Examples Directory Role

`strategies/examples/`는 참조용 샘플 모음이다.
실행/저장/승인 기준은 registry(`strategies/`) 기준으로 관리한다.
