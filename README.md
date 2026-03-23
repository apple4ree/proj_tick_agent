# 틱 데이터 전략 생성 및 검증 플랫폼

LOB(호가창) 틱 데이터 기반 **전략 생성 → 검토 → 컴파일 → Universe 백테스트** 플랫폼.

템플릿/규칙 기반으로 전략 사양(Strategy Spec)을 생성하고, 정적 검토를 거친 뒤, 다종목 × 다 latency 조건에서 체계적으로 검증한다.

## 5-Block 아키텍처

본 프로젝트는 내부적으로 Layer 0~7 백테스트 파이프라인을 유지하지만,
상위 수준에서는 다음 5개 블록으로 이해할 수 있다.

```
Data ──▶ Strategy ──▶ Execution Planning ──▶ Market Simulation ──▶ Evaluation & Orchestration
```

| # | Block | 역할 | 대응 코드 |
|---|-------|------|----------|
| 1 | **Data** | 원시 틱 데이터 적재, 정제/동기화, MarketState 생성, feature 계산 | `src/data/layer0_data/` |
| 2 | **Strategy** | 전략 생성, 검토, Spec 저장, 컴파일 (Spec → Strategy 객체) | `src/strategy_block/` |
| 3 | **Execution Planning** | Signal → Target Position, 주문 수량 계산, slicing/placement/제약 적용 | `src/execution_planning/` |
| 4 | **Market Simulation** | 체결 시뮬레이션, latency 반영, 수수료/세금/충격 적용, bookkeeping | `src/market_simulation/layer5_simulator/` |
| 5 | **Evaluation & Orchestration** | PnL 계산, execution quality, 단일/Universe 백테스트, worker orchestration | `src/evaluation_orchestration/` |

> 내부 구현은 Layer 0~7로 세분화되어 있다. 상세는 `PIPELINE.md`를 참조.

## 프로젝트 구조

```
proj_rl_agent/
├── src/
│   ├── data/                    # ── Data Block ──
│   │   └── layer0_data/         #   데이터 수집·정제·동기화·피처
│   │
│   ├── strategy_block/          # ── Strategy Block ──
│   │   ├── strategy_generation/ #   전략 생성 (v1: template/OpenAI, v2: template+lowering)
│   │   ├── strategy_review/     #   정적 규칙 기반 전략 검토 (v1 + v2)
│   │   ├── strategy_specs/      #   전략 사양 스키마 (v1 + v2 AST)
│   │   ├── strategy_compiler/   #   Spec → Strategy 컴파일러 (v1/v2 디스패치)
│   │   ├── strategy_registry/   #   전략 저장·관리 + metadata/promotion
│   │   └── strategy/            #   Strategy ABC (base.py)
│   │
│   ├── execution_planning/      # ── Execution Planning Block ──
│   │   ├── layer1_signal/       #   시그널 데이터 타입
│   │   ├── layer2_position/     #   포지션 타겟·리스크 관리
│   │   ├── layer3_order/        #   주문 타입·델타 계산
│   │   └── layer4_execution/    #   슬라이싱·배치·타이밍
│   │
│   ├── market_simulation/       # ── Market Simulation Block ──
│   │   └── layer5_simulator/    #   체결·수수료·충격·latency
│   │
│   ├── evaluation_orchestration/ # ── Evaluation & Orchestration Block ──
│   │   ├── layer6_evaluator/    #   PnL·리스크·실행 품질
│   │   ├── layer7_validation/   #   백테스트 오케스트레이션
│   │   └── orchestration/       #   비동기 generation/execution orchestration
│   │
│   └── utils/config.py          # YAML config loader
│
├── conf/                      # YAML 설정 — load_config()가 자동 merge하는 config stack
│   ├── backtest_core.yaml    # (config stack 미포함) BacktestConfig.from_yaml() 전용
│   └── profiles/              # 환경별 프로필 — --profile로 지정 시 config stack 위에 merge
├── scripts/
│   ├── run_generation_worker.sh      # Shell 런처 (권장 진입점)
│   ├── run_backtest_worker.sh
│   ├── submit_generation_job.sh
│   ├── submit_backtest_job.sh
│   ├── run_local_stack.sh            # 로컬 스택 (두 Worker 동시)
│   ├── run_generation_worker.py      # Python 실행기 (--profile/--config override 지원)
│   ├── run_backtest_worker.py
│   ├── generate_strategy.py          # Generation (--direct or job queue)
│   ├── submit_backtest_job.py        # Backtest Job submitter
│   ├── run_generate_review_backtest.sh  # End-to-end launcher
│   ├── backtest.py                   # 단일 종목 백테스트 (직접 실행)
│   ├── backtest_strategy_universe.py # Universe 백테스트 (직접 실행)
│   └── ...
├── strategies/                # 전략 사양 저장소 (registry)
├── jobs/                      # File-based job queue
├── tests/                     # pytest 테스트
├── docs/                      # 문서
└── archive/                   # 비활성 코드·문서 보관
```

## 아키텍처 문서

- 비동기 전략 생성/실행 분리 설계: `ARCHITECTURE.md`
- 파이프라인 상세 (5-block → Layer 0~7): `PIPELINE.md`

## 빠른 시작

### End-to-end (가장 쉬운 방법)

생성 → 검토 → 백테스트를 한 번에 실행:

```bash
cd /home/dgu/tick/proj_rl_agent

# Single-symbol
./scripts/run_generate_review_backtest.sh \
    --goal "order imbalance alpha" \
    --symbol 005930 --start-date 20260313

# Universe mode (모든 종목)
./scripts/run_generate_review_backtest.sh \
    --goal "order imbalance alpha" \
    --universe --start-date 20260313

# OpenAI backend 사용
./scripts/run_generate_review_backtest.sh \
    --goal "spread mean reversion" \
    --symbol 005930 --start-date 20260313 \
    --backend openai --mode mock
```

`OPENAI_API_KEY` 환경 변수는 `--backend openai --mode live`일 때만 필요하다.

### Worker 기반 실행

```bash
# 0. 로컬 스택 시작 (generation + backtest worker)
./scripts/run_local_stack.sh dev

# 1. 전략 생성 Job 제출
./scripts/submit_generation_job.sh "Order imbalance alpha"

# 2. 백테스트 Job 제출
./scripts/submit_backtest_job.sh \
    --strategy imbalance_momentum --version 1.0 \
    --symbol 005930 --start-date 2026-03-13

# 3. 결과 요약
PYTHONPATH=src python scripts/summarize_universe_results.py \
    --results outputs/backtests/universe_results.csv
```

### 개별 스크립트 직접 실행

```bash
# 전략 직접 생성
PYTHONPATH=src python scripts/generate_strategy.py \
    --goal "Order imbalance alpha" --direct

# 단일 종목 백테스트
PYTHONPATH=src python scripts/backtest.py \
    --spec strategies/imbalance_momentum_v1.0.json \
    --symbol 005930 --start-date 20260313

# Universe 백테스트
PYTHONPATH=src python scripts/backtest_strategy_universe.py \
    --spec strategies/imbalance_momentum_v1.0.json \
    --start-date 20260313
```

## Strategy Spec 형식

두 가지 사양 형식을 지원하며, `compile_strategy(spec)`가 자동 분기한다.

### v1 (StrategySpec) — flat rule 리스트

```json
{
  "name": "imbalance_momentum",
  "version": "1.0",
  "signal_rules": [
    {"feature": "order_imbalance", "operator": ">", "threshold": 0.3, "score_contribution": 0.5}
  ],
  "filters": [
    {"feature": "spread_bps", "operator": ">", "threshold": 30.0, "action": "block"}
  ],
  "position_rule": {"max_position": 500, "sizing_mode": "signal_proportional"},
  "exit_rules": [
    {"exit_type": "stop_loss", "threshold_bps": 15.0},
    {"exit_type": "take_profit", "threshold_bps": 25.0},
    {"exit_type": "time_exit", "timeout_ticks": 300}
  ]
}
```

### v2 (StrategySpecV2) — 계층적 IR + Expression AST

```json
{
  "spec_format": "v2",
  "name": "imbalance_persist_momentum",
  "entry_policies": [
    {
      "name": "long_entry", "side": "long",
      "trigger": {"type": "persist", "expr": {"type": "comparison", "feature": "order_imbalance", "op": ">", "threshold": 0.25}, "window": 5, "min_true": 3},
      "strength": {"type": "const", "value": 0.5}
    }
  ],
  "exit_policies": [{"name": "exits", "rules": [
    {"name": "stop", "priority": 1,
     "condition": {"type": "comparison", "feature": "order_imbalance", "op": "<", "threshold": -0.2},
     "action": {"type": "close_all"}}
  ]}],
  "risk_policy": {"max_position": 500, "inventory_cap": 1000},
  "regimes": [
    {"name": "trending", "priority": 1, "when": {"type": "comparison", "feature": "spread_bps", "op": "<", "threshold": 15.0}, "entry_policy_refs": ["long_entry"], "exit_policy_ref": "exits"}
  ],
  "execution_policy": {"placement_mode": "adaptive", "cancel_after_ticks": 20, "max_reprices": 3}
}
```

**AST 노드**: `const`, `feature`, `comparison`, `all`, `any`, `not`, `cross`, `lag`, `rolling`, `persist`

## Universe 평가 프로토콜

- 한 종목 결과만으로 결론 내리지 않는다
- 모든 적용 가능 종목에 적용 후 기본 요약:
  - **Mean** net_pnl, sharpe
  - **Median** net_pnl, sharpe
  - **Std** (종목 간 분산)
  - **Win rate** (수익 종목 비율)
- Latency는 기본 실험 축: 0ms, 50ms, 100ms, 500ms, 1000ms

## Latency를 기본 변수로 포함하는 이유

실제 트레이딩에서 latency는 전략 성과에 결정적 영향을 미친다:
- 0ms (이론적 최적): 전략의 순수 알파 측정
- 50ms (co-location): 기관 투자자 환경
- 100ms (일반 기관): 현실적 기관 환경
- 500ms~1s (리테일): 개인 투자자 환경

latency에 따라 성과가 급격히 변하는 전략은 실전 배포에 부적합하다.
