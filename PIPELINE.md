# 파이프라인 — 5-Block 흐름 및 내부 Layer 상세

## 5-Block 파이프라인 흐름

본 프로젝트는 내부적으로 Layer 0~7 백테스트 파이프라인을 유지하지만,
상위 수준에서는 다음 5개 블록으로 이해할 수 있다.

```
┌──────────────┐   ┌──────────────┐   ┌────────────────────┐   ┌────────────────────┐   ┌──────────────────────────┐
│  1. Data      │──▶│  2. Strategy  │──▶│  3. Exec Planning   │──▶│  4. Market Simulation│──▶│  5. Evaluation &          │
│  layer0_data  │   │  generation/  │   │  layer1~4           │   │  layer5_simulator    │   │     Orchestration         │
│               │   │  review/      │   │                     │   │                      │   │  layer6~7, orchestration  │
│               │   │  compiler     │   │                     │   │                      │   │                           │
└──────────────┘   └──────────────┘   └────────────────────┘   └────────────────────┘   └──────────────────────────┘
```

---

## Block 1: Data

원시 틱 데이터 적재, 정제/동기화, MarketState 생성, feature 계산.

**모듈**: `src/data/layer0_data/`

```
KIS H0STASP0 CSV
  ↓  DataIngester → DataCleaner → FeaturePipeline → MarketStateBuilder
MarketState[] (LOB 스냅샷 + 피처 + 메타데이터)
```

---

## Block 2: Strategy

전략 생성, 검토, Spec 저장, 컴파일 (Spec → Strategy 객체).

### 2-1. 전략 생성

**Shell 런처**: `./scripts/internal/ops/submit_generation_job.sh "<goal>"`
**Python**: `scripts/generate_strategy.py --goal "<goal>"`
**Config**: `conf/generation.yaml`
**모듈**: `src/strategy_block/strategy_generation/`

설정은 `conf/generation.yaml`에서 로드하며, `--profile`로 환경별 override 가능.
template backend와 OpenAI multi-agent backend를 병행 지원한다.
백테스트 엔진(Execution Planning → Market Simulation → Evaluation)은 backend에 무관하게 동일하며, static reviewer는 양쪽 모두 필수 hard gate이다.

#### Backend A: Template (기본)

```
--goal "Order imbalance alpha"
  ↓  키워드 매칭 (select_ideas_for_goal)
IDEA_TEMPLATES에서 관련 템플릿 선택
  ↓  StrategyGenerator._build_spec()
  ↓  latency 보정 (holding_period, time_exit)
StrategySpec 생성
  ↓  StrategyRegistry.save()
strategies/{name}_v{version}.json 저장
```

내장 템플릿 (5개):
| # | 이름 | 핵심 feature |
|---|------|-------------|
| 0 | imbalance_momentum | order_imbalance, depth_imbalance |
| 1 | spread_mean_reversion | spread_bps, order_imbalance (contrarian) |
| 2 | trade_flow_pressure | trade_flow_imbalance |
| 3 | depth_divergence | depth_imbalance + trade_flow (contrarian) |
| 4 | micro_price_alpha | order_imbalance, bid/ask_depth_5 |

#### Backend B: OpenAI Multi-Agent (`--backend openai`)

```
--goal "Order imbalance alpha" --backend openai
  ↓  ResearcherAgent: goal → IdeaBriefList (n개 아이디어)
  ↓  FactorDesignerAgent: idea → SignalDraft (signal rules + filters)
  ↓  RiskDesignerAgent: idea + signal → RiskDraft (position + exit rules)
  ↓  Assembler: agent 출력 → StrategySpec (결정론적 변환)
  ↓  LLMReviewerAgent: soft critique (재설계 루프)
  ↓  Static Reviewer: hard gate (필수)
StrategySpec 생성
```

4-Agent 구조:
| Agent | 입력 | 출력 | 역할 |
|-------|------|------|------|
| ResearcherAgent | goal (str) | IdeaBriefList | 리서치 아이디어 생성 |
| FactorDesignerAgent | IdeaBrief | SignalDraft | 시그널/필터 규칙 설계 |
| RiskDesignerAgent | IdeaBrief + SignalDraft | RiskDraft | 포지션/exit 규칙 설계 |
| LLMReviewerAgent | StrategySpec dict | ReviewDecision | 소프트 리뷰 |

Fallback 정책: OpenAI API 실패 시 자동으로 template backend로 전환.
`--mode mock`으로 API 키 없이 agent fallback 로직 테스트 가능.

### 2-2. 전략 검토

**스크립트**: `scripts/review_strategy.py`
**모듈**: `src/strategy_block/strategy_review/`

```
strategies/{name}_v{version}.json
  ↓  StrategySpec.load()
StrategySpec
  ↓  StrategyReviewer.review()
ReviewResult (passed/failed + issues)
```

검토 규칙 (7개 카테고리):
- **schema**: 필수 필드, 유효 연산자/액션/exit 타입
- **signal**: 규칙 존재 여부, 과다(>10), 단방향만 존재
- **filter**: 과다(>5), 비현실적 threshold
- **risk**: stop_loss/time_exit 부재, 비현실적 설정
- **position**: max_position/inventory_cap 유효성
- **redundancy**: 동일 규칙 중복
- **feature**: 미지원 피처 사용

### 2-3. 컴파일

**모듈**: `src/strategy_block/strategy_compiler/`

```
StrategySpec
  ↓  StrategyCompiler.compile()
CompiledStrategy (Strategy ABC 구현체)
```

---

## Block 3: Execution Planning

Signal → Target Position, 주문 수량 계산, slicing/placement/제약 적용.

**모듈**: `src/execution_planning/layer1_signal/` ~ `src/execution_planning/layer4_execution/`

```
MarketState → Strategy.generate_signal() → Signal
  ↓  TargetBuilder → DeltaComputer → ParentOrder
  ↓  SlicingPolicy → PlacementPolicy → ChildOrder
```

---

## Block 4: Market Simulation

체결 시뮬레이션, latency 반영, 수수료/세금/충격 적용, bookkeeping.

**모듈**: `src/market_simulation/layer5_simulator/`

```
ChildOrder + LOB
  ↓  MatchingEngine → FillEvent
  ↓  LatencyModel, ImpactModel, FeeModel 적용
  ↓  Bookkeeper → 계좌 상태 갱신
```

---

## Block 5: Evaluation & Orchestration

PnL 계산, execution quality, 단일/Universe 백테스트, worker orchestration.

**모듈**: `src/evaluation_orchestration/layer6_evaluator/`, `layer7_validation/`, `orchestration/`, `scripts/`

### 5-1. 단일 종목 백테스트

**Shell**: `./scripts/internal/ops/submit_backtest_job.sh --strategy <name> --version <ver> --symbol <sym> --start-date <date>`
**직접 실행**: `scripts/backtest.py --spec <path> --symbol <sym> --start-date <date>`
**Config**: `conf/backtest_base.yaml` + `conf/backtest_worker.yaml`

```
CompiledStrategy + MarketState[]
  ↓  PipelineRunner.run()
  ↓  observed_state = lookup(market_data_delay_ms + decision_compute_ms)  ← strategy decisions
  ↓  true_state = current state                      ← fill/matching
  ↓  FillSimulator (queue gate → MatchingEngine → impact/fee)
  ↓  Bookkeeper → PnLLedger → Reports
BacktestResult (summary JSON + artifacts)
```

`market_data_delay_ms` (default 0.0) controls observation lag (what state is seen).
`decision_compute_ms` (default 0.0) controls strategy-side compute delay (how long it takes to act).
Decision path uses effective delayed lookup (`market_data_delay_ms + decision_compute_ms`),
while fills continue to execute against `true_state`.
Venue latency semantics remain separate from decision-path lag:
- `latency.order_submit_ms`: order becomes venue-live only after arrival
- `latency.cancel_ms`: cancel becomes effective only after cancel-effective time
- `latency.order_ack_ms`: reporting/status aggregate only (not fill gating in current phase)
- replace path: intentional minimal exception (immediate cancel old child + create new child with fresh submit lifecycle); staged replace venue workflow is deferred

Flat `latency_ms` is a legacy compatibility alias only. It is applied only when nested `latency` is absent (`latency is None`) as `submit/ack/cancel = 0.3/0.7/0.2 × latency_ms`, and it never derives `market_data_delay_ms`.
Supported resample resolutions: `1s` (default baseline), `500ms` (realism-oriented).
At `500ms`, moderate lag (≥ 200ms) yields distinct `observed_state`; at `1s`,
small sub-second lag often collapses to the same state.
Result metadata exposes `configured_market_data_delay_ms`, `configured_decision_compute_ms`,
`effective_delay_ms`, `avg_observation_staleness_ms`, bounded history diagnostics, and latency lifecycle diagnostics for traceability.
Queue models are explicit interfaces in `queue_models/` (6 models, gate-only or gate+allocation).

### 5-2. Universe 백테스트 (다종목 × 다 latency)

**Shell**: `./scripts/internal/ops/submit_backtest_job.sh --strategy <name> --version <ver> --universe --start-date <date>`
**직접 실행**: `scripts/backtest_strategy_universe.py --spec <path> --start-date <date>`

```
전체 종목 발견 (DataIngester.list_symbols)
  ↓
종목별 × latency별 backtest (순차 실행)
  ↓
universe_results.csv 집계
```

기본값은 `conf/backtest_base.yaml` (core defaults) + `conf/backtest_worker.yaml` (latency sweep 등)에서 로드.

### 5-3. 평가 및 요약

**스크립트**: `scripts/summarize_universe_results.py`

```
universe_results.csv
  ↓  group by latency_ms (기본)
집계 메트릭:
  mean, median, std, min, max, win_rate
  → net_pnl, sharpe_ratio, max_drawdown, fill_rate
```

---

## 핵심 데이터 타입 — Block별 입출력

```
Data Block          Exec Planning Block                    Market Sim Block    Evaluation Block
MarketState ──▶ Signal ──▶ TargetPosition ──▶ ParentOrder ──▶ ChildOrder ──▶ FillEvent ──▶ Reports
  (LOB+피처)   (예측+신뢰도) (종목→목표수량)   (대량주문)     (개별주문)     (체결기록)    (평가결과)
```

---

## 스크립트 요약

| 스크립트 | Block | 용도 |
|---------|-------|------|
| `generate_strategy.py` | Strategy (Block 2) | 전략 사양 생성 (template / OpenAI multi-agent) |
| `review_strategy.py` | Strategy (Block 2) | 정적 규칙 기반 사양 검토 |
| `backtest.py` | Evaluation (Block 5) | 단일 종목 백테스트 |
| `backtest_strategy_universe.py` | Evaluation (Block 5) | 다종목 × 다 latency 백테스트 |
| `summarize_universe_results.py` | Evaluation (Block 5) | Universe 결과 집계 |
| `collect_data.py` | Data (Block 1) | 시장 데이터 수집 |
| `visualize.py` | Evaluation (Block 5) | 결과 시각화 |

---

## 내부 구현: Layer 0~7 상세

5-block 아키텍처의 각 블록은 내부적으로 Layer 0~7로 세분화되어 있다.
이 계층 구조는 구현 수준의 모듈 분리이며, 상위 블록 관점에서 이해한 뒤 필요 시 참조한다.

### Layer 0: Data — Block 1 (Data)

| 모듈 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `ingestion.py` | `DataIngester` | KIS CSV에서 틱 데이터 로드 |
| `cleaning.py` | `DataCleaner` | 비정상 틱 감지·제거 |
| `synchronization.py` | `DataSynchronizer` | 다종목 시간 정렬 |
| `feature_pipeline.py` | `MicrostructureFeatures` | 스프레드, 불균형, 깊이, 충격 등 피처 |
| `market_state.py` | `MarketState`, `LOBSnapshot` | 시장 상태 데이터 계약 |
| `state_builder.py` | `MarketStateBuilder` | 수집→정제→피처→상태 오케스트레이션 |

### Layer 1: Signal — Block 3 (Execution Planning)

| 모듈 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `signal.py` | `Signal` | 시그널 데이터 계약 (score, confidence, expected_return) |

### Layer 2: Position — Block 3 (Execution Planning)

| 모듈 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `target_builder.py` | `TargetBuilder`, `TargetPosition` | 시그널 → 목표 포지션 |
| `risk_caps.py` | `RiskCaps` | 총노출/순노출/레버리지/집중도 제한 |
| `turnover_budget.py` | `TurnoverBudget` | 거래비용 예산 관리 |

### Layer 3: Order — Block 3 (Execution Planning)

| 모듈 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `order_types.py` | `ParentOrder`, `ChildOrder` | 주문 데이터 타입 |
| `delta_compute.py` | `DeltaComputer` | 목표 vs 현재 → 델타 |
| `order_constraints.py` | `OrderConstraints` | 주문 크기/가격 범위 검증 |
| `order_scheduler.py` | `OrderScheduler` | 주문 제출 시점 스케줄링 |

### Layer 4: Execution — Block 3 (Execution Planning)

| 모듈 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `slicing_policy.py` | TWAP, VWAP, POV, Almgren-Chriss | 대량 주문 분할 |
| `placement_policy.py` | Aggressive, Passive, SpreadAdaptive | 주문 가격·유형 결정 |
| `cancel_replace.py` | `CancelReplace` | 미체결 주문 취소·재배치 |
| `safety_guardrails.py` | `SafetyGuardrails` | 최대 금액/레버리지 사전 검증 |

### Layer 5: Simulator — Block 4 (Market Simulation)

| 모듈 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `matching_engine.py` | `MatchingEngine` | LOB 기반 체결 시뮬레이션 |
| `impact_model.py` | `LinearImpact`, `SquareRootImpact` | 시장충격 모델링 |
| `fee_model.py` | `KRXFeeModel` | KRX 수수료·세금 |
| `latency_model.py` | `LatencyModel` | 주문 지연 시뮬레이션 |
| `bookkeeper.py` | `Bookkeeper`, `FillEvent` | 체결 기록·계좌 상태 |

### Layer 6: Evaluator — Block 5 (Evaluation & Orchestration)

| 모듈 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `pnl_ledger.py` | `PnLLedger`, `PnLReport` | 손익 추적 및 비용 분해 |
| `risk_metrics.py` | `RiskReport` | Sharpe, MDD, VaR 등 |
| `execution_metrics.py` | `ExecutionReport` | IS, VWAP 벤치마크 대비 |
| `attribution.py` | `AttributionReport` | 성과 귀인 분석 |

### Layer 7: Validation — Block 5 (Evaluation & Orchestration)

| 모듈 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `pipeline_runner.py` | `PipelineRunner` | 전체 백테스트 오케스트레이션 |
| `backtest_config.py` | `BacktestConfig`, `BacktestResult` | 백테스트 설정 및 결과 |
| `fill_simulator.py` | `FillSimulator` | 체결 시뮬레이션 위임 |
| `report_builder.py` | `ReportBuilder` | 리포트 생성 위임 |
| `reproducibility.py` | `ReproducibilityManager` | 실험 재현성 보장 |
