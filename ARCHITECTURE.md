# Architecture (v2-only)

## 1) System Blocks

```text
data -> strategy_block -> execution_planning -> market_simulation -> evaluation_orchestration
```

- `data`: 원시 호가/체결 데이터 적재, 정제, 동기화, `MarketState` 생성
- `strategy_block`: v2 spec 생성/검토/저장/컴파일
- `execution_planning`: signal을 target/parent-child order 계획으로 변환
- `market_simulation`: latency/impact/fee 포함 fill 시뮬레이션
- `evaluation_orchestration`: 백테스트 실행, 리포트/메트릭, worker orchestration

## 2) StrategySpec v2 Model

핵심 구성요소:
- `preconditions`
- `entry_policies`
- `exit_policies`
- `risk_policy`
- `execution_policy`
- `regimes`
- `state_policy`

AST 기반 조건식은 feature/state/position context를 평가해 entry/exit/risk/execution 판단에 사용된다.

## 3) Compiler / Reviewer / Registry 관계

- reviewer: `StrategySpecV2` 정적 점검
- registry: spec + metadata 상태 관리
- compiler: `StrategySpecV2` -> executable strategy
- backtest/worker: registry에서 로드한 v2 spec만 실행

## 4) Backtest Data Flow

```text
spec(v2)
  -> compile_strategy
  -> signal generation      (observed_state: delayed market data)
  -> target position         (observed_state)
  -> order planning          (observed_state)
  -> execution simulation    (true_state: current market data)
  -> fill/bookkeeping
  -> pnl/metrics/report
```

`PipelineRunner` maintains per-symbol state history and performs a
`bisect`-based lookup to derive `observed_state` from `true_state - market_data_delay_ms`.
When `market_data_delay_ms=0`, `observed_state == true_state` (zero-cost fast path).

Queue-position semantics are owned exclusively by `FillSimulator` via
explicit `QueueModel` interfaces (`queue_models/` package).
`MatchingEngine` (layer 5) is queue-free and handles pure price/qty matching.

## 5) Generation Path

현재 canonical path:
- v2 template selection
- lowering to `StrategySpecV2`
- static review gate
- registry save

## 6) Worker / Orchestration

- generation worker: generation job 처리 및 registry 저장
- backtest worker: 승인된 spec 버전 고정 로드 후 backtest 실행
- file-queue 기반 비동기 실행 경로 제공

## 7) Implementation Scope

Implemented:
- v2 generation/review/registry/compiler/backtest 기본 경로
- single-symbol backtest smoke (quick wiring check)
- worker 실행 경로

Partial / hint-level:
- execution policy 일부 필드만 downstream override에 반영
- reviewer는 정적 규칙 기반(heuristic 성격 일부 존재)

Not implemented as production claims:
- production-grade live OMS 전체
- unrestricted portfolio allocator
- full programmable strategy language
