# 틱 데이터 전략 생성 및 검증 플랫폼

전략 사양(Strategy Spec)을 생성하고, 정적 검토 후 컴파일하여,
다종목/latency 조건에서 체계적으로 백테스트하는 연구 플랫폼.

---

## 핵심 설계 철학

1. **전략은 구조화된 JSON Spec으로 저장된다** — 자유 텍스트가 아닌 기계 소비 가능 형식
2. **Compiler가 Spec을 Strategy 객체로 변환한다** — 기존 백테스트 엔진과 연결
3. **모든 적용 가능 종목에 백테스트한다** — 단일 종목 결과로 결론 내지 않음
4. **Latency는 기본 실험 축이다** — 0ms, 50ms, 100ms, 500ms, 1000ms
5. **전략 생성과 실행은 분리될 수 있어야 한다** — registry와 orchestration layer를 통해 비동기 연결

---

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

---

## 프로젝트 구조

```
proj_rl_agent/
├── src/
│   ├── data/                    # ── Data Block ──
│   │   └── layer0_data/         #   데이터 수집·정제·동기화·피처
│   │
│   ├── strategy_block/          # ── Strategy Block ──
│   │   ├── strategy_generation/ #   전략 생성 (template / OpenAI multi-agent)
│   │   │   ├── templates.py     #     5개 v1 전략 템플릿 + 키워드 매칭
│   │   │   ├── generator.py     #     StrategyGenerator (backend 선택 + fallback)
│   │   │   ├── pipeline.py      #     MultiAgentPipeline (4-agent 오케스트레이션)
│   │   │   ├── agents.py        #     Researcher/FactorDesigner/RiskDesigner/LLMReviewer
│   │   │   ├── agent_schemas.py #     Pydantic 스키마
│   │   │   ├── assembler.py     #     Agent 출력 → StrategySpec 결정론적 변환
│   │   │   ├── openai_client.py #     OpenAI API 클라이언트 (live/replay/mock)
│   │   │   └── v2/              #     v2 전략 생성
│   │   │       ├── templates_v2.py  #   6개 v2 전략 템플릿 (Phase 1: 3, Phase 2: 3)
│   │   │       └── lowering.py      #   템플릿 → StrategySpecV2 lowering (regimes, execution_policy 포함)
│   │   ├── strategy_review/     #   정적 규칙 기반 전략 검토
│   │   │   └── v2/reviewer_v2.py #    v2 전략 검토기 (11개 카테고리)
│   │   ├── strategy_specs/      #   전략 사양 스키마
│   │   │   └── v2/              #     v2 스키마 + AST 노드
│   │   │       ├── ast_nodes.py #       Expression AST (Const/Feature/Comparison/All/Any/Not/Cross/Lag/Rolling/Persist)
│   │   │       └── schema_v2.py #       StrategySpecV2 (Entry/Exit/Risk/Regime/ExecutionPolicy)
│   │   ├── strategy_compiler/   #   Spec → Strategy 컴파일러
│   │   │   ├── __init__.py      #     compile_strategy() 디스패치 (v1/v2 자동 분기)
│   │   │   └── v2/              #     v2 컴파일러
│   │   │       ├── compiler_v2.py #     CompiledStrategyV2 (Strategy ABC 구현)
│   │   │       └── runtime_v2.py  #     AST evaluator + RuntimeStateV2
│   │   ├── strategy_registry/   #   전략 저장·관리 (v1/v2 공통, spec_format 필드)
│   │   └── strategy/            #   Strategy ABC (base.py)
│   │
│   ├── execution_planning/      # ── Execution Planning Block ──
│   │   ├── layer1_signal/       #   시그널 데이터 타입 (Signal dataclass)
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
│   │       ├── models.py        #     Job, JobType, JobStatus
│   │       ├── file_queue.py    #     FileQueue (atomic rename)
│   │       ├── manager.py       #     OrchestrationManager
│   │       ├── generation_worker.py # GenerationWorker
│   │       └── backtest_worker.py   # BacktestWorker
│   │
│   └── utils/
│       └── config.py            # YAML config loader (merge, profile, env)
│
├── scripts/
│   ├── run_generation_worker.sh      # Shell 런처 (권장 진입점)
│   ├── run_backtest_worker.sh
│   ├── submit_generation_job.sh
│   ├── submit_backtest_job.sh
│   ├── run_local_stack.sh            # 로컬 스택 (두 Worker 동시)
│   ├── run_generation_worker.py      # Python 실행기 (--config 기반)
│   ├── run_backtest_worker.py
│   ├── generate_strategy.py          # Generation (--direct or job queue)
│   ├── submit_backtest_job.py        # Backtest Job submitter
│   ├── run_generate_review_backtest.sh  # End-to-end launcher
│   ├── backtest.py                   # 단일 종목 백테스트 (직접 실행)
│   ├── backtest_strategy_universe.py # Universe 백테스트 (직접 실행)
│   ├── summarize_universe_results.py # 결과 집계
│   ├── collect_data.py               # 데이터 수집
│   └── visualize.py                  # 시각화
│
├── conf/                      # YAML 설정 — load_config()가 자동 merge하는 config stack
│   ├── backtest_core.yaml    # (config stack 미포함) BacktestConfig.from_yaml() 전용
│   └── profiles/              # 환경별 프로필 — --profile로 지정 시 config stack 위에 merge
├── strategies/                # 생성된 전략 사양 저장소 (registry)
├── jobs/                      # File-based job queue
├── tests/                     # pytest 테스트
└── docs/                      # 문서
```

---

## 아키텍처 문서

- 5-block 아키텍처 및 세부 설계: `ARCHITECTURE.md`
- 파이프라인 상세 (5-block → Layer 0~7): `PIPELINE.md`
- generation plane와 execution plane 분리 원칙, spec lifecycle, registry/job queue 설계 포함

## 전략 생성 → 검토 → 컴파일

### Spec 형식: v1 vs v2

본 프로젝트는 두 가지 전략 사양 형식을 지원한다.

| | v1 (`StrategySpec`) | v2 (`StrategySpecV2`) |
|---|---|---|
| 구조 | flat rule 리스트 (signal_rules, filters, exit_rules) | 계층적 IR (entry/exit/risk policy + AST) |
| 조건 표현 | feature + operator + threshold 문자열 | Expression AST (10 노드 타입: const/feature/comparison/all/any/not/cross/lag/rolling/persist) |
| 컴파일러 | `StrategyCompiler` → `CompiledStrategy` | `StrategyCompilerV2` → `CompiledStrategyV2` |
| 검토기 | `StrategyReviewer` (7 카테고리) | `StrategyReviewerV2` (11 카테고리) |
| 생성 | template / OpenAI multi-agent | template + lowering (6 템플릿) |
| 추가 기능 | — | regimes (시장 상태별 정책 라우팅), execution_policy (hint-level) |
| 디스패치 | `compile_strategy(spec)` — spec 타입에 따라 자동 분기 |

### StrategyGenerator (v1)

`src/strategy_block/strategy_generation/` — 두 가지 backend를 병행 지원하는 전략 사양 생성기.

**Template backend** (기본, `--backend template`):
- `--goal` 키워드에서 관련 템플릿 자동 선택 (가장 적합한 1개 생성)
- 5개 내장 템플릿: imbalance_momentum, spread_mean_reversion, trade_flow_pressure, depth_divergence, micro_price_alpha

**OpenAI multi-agent backend** (`--backend openai`):
- 4-Agent 파이프라인: Researcher → FactorDesigner → RiskDesigner → LLMReviewer
- OpenAI Structured Outputs로 Pydantic 스키마 기반 응답 생성
- 결정론적 Assembler가 agent 출력을 StrategySpec으로 변환
- API 실패 시 자동으로 template backend fallback
- `--mode mock`으로 API 키 없이 agent fallback 테스트 가능

### V2 전략 생성 (v2)

`src/strategy_block/strategy_generation/v2/` — 템플릿 + lowering 파이프라인.

- 6개 내장 v2 템플릿:
  - Phase 1: `imbalance_persist_momentum`, `spread_absorption_reversal`, `cross_momentum`
  - Phase 2: `regime_filtered_persist_momentum` (regimes + persist), `rolling_mean_reversion` (rolling), `adaptive_execution_imbalance` (execution_policy)
- `lower_to_spec_v2(template)`: 템플릿 dict → `StrategySpecV2` 변환 (regimes, execution_policy 포함)
- 생성된 v2 spec은 v1과 동일하게 registry에 저장 가능 (`spec_format="v2"`)

공통:
- Static reviewer는 양쪽 backend 모두 필수 hard gate
- 백테스트 엔진(Execution Planning → Market Simulation → Evaluation)은 backend에 무관하게 동일
- 생성된 Spec은 `StrategyRegistry`에 저장, trace JSON도 별도 저장

### StrategyReviewer (v1)

`src/strategy_block/strategy_review/` — 정적 규칙 기반 검토기.

7개 검증 카테고리:
| 카테고리 | 검토 내용 |
|----------|----------|
| schema | StrategySpec.validate() 통과 여부 |
| signal | 규칙 존재, 과다(>10), 단방향만 존재 |
| filter | 과다(>5), 비현실적 threshold |
| risk | stop_loss/time_exit 부재, 비현실적 설정 |
| position | max_position/inventory_cap 유효성 |
| redundancy | 동일 규칙 중복 |
| feature | 미지원 피처 사용 |

### StrategyReviewerV2 (v2)

`src/strategy_block/strategy_review/v2/reviewer_v2.py` — v2 전략 검토기.

11개 검증 카테고리:
| 카테고리 | 검토 내용 |
|----------|----------|
| schema | StrategySpecV2.validate() 통과 여부 |
| expression_safety | AST 트리 깊이 제한 (≤20) |
| feature_availability | 지원 피처만 사용하는지 확인 |
| logical_contradiction | AllExpr 내 동일 피처 모순 조건 탐지 |
| unreachable_entry | 과도한 cooldown (>10000 tick) |
| risk_inconsistency | inventory_cap < max_position, base_size > max_size |
| exit_completeness | close_all 액션 부재 경고 |
| dead_regime | 모순 조건(feature > X AND feature < X)인 regime 탐지 |
| regime_reference_integrity | regime의 entry/exit policy 참조 유효성 검증 |
| execution_risk_mismatch | passive_only + 대형 포지션, 음수 cancel_after_ticks 등 |
| latency_structure_warning | rolling/persist window > 200 또는 lag steps > 200 경고 |

### StrategyCompiler

`src/strategy_block/strategy_compiler/` — Spec → CompiledStrategy 변환.

**v1**: 20+ 내장 피처, 7개 비교 연산자, 5개 exit 타입
**v2**: AST 기반 조건 평가 (lag/rolling/persist 포함), entry/exit policy, cooldown/precondition, regime 선택, execution policy hint 지원

`compile_strategy(spec)` 함수로 v1/v2 자동 디스패치.

---

## Strategy Spec 형식

### v1 (StrategySpec)

```json
{
  "name": "strategy_name",
  "version": "1.0",
  "description": "...",
  "signal_rules": [
    {"feature": "...", "operator": ">|<|>=|<=|==|cross_above|cross_below",
     "threshold": 0.0, "score_contribution": 0.0, "description": "..."}
  ],
  "filters": [
    {"feature": "...", "operator": "...", "threshold": 0.0,
     "action": "block|reduce", "description": "..."}
  ],
  "position_rule": {
    "max_position": 1000, "sizing_mode": "signal_proportional|fixed|kelly",
    "fixed_size": 100, "holding_period_ticks": 0, "inventory_cap": 1000
  },
  "exit_rules": [
    {"exit_type": "stop_loss|take_profit|trailing_stop|time_exit|signal_reversal",
     "threshold_bps": 0.0, "timeout_ticks": 0, "description": "..."}
  ],
  "metadata": {}
}
```

### v2 (StrategySpecV2)

```json
{
  "spec_format": "v2",
  "name": "strategy_name",
  "version": "1.0",
  "preconditions": [
    {"name": "spread_ok", "condition": {"type": "comparison", "feature": "spread_bps", "op": "<", "threshold": 30.0}}
  ],
  "entry_policies": [
    {
      "name": "long_entry", "side": "long",
      "trigger": {"type": "all", "children": [
        {"type": "comparison", "feature": "order_imbalance", "op": ">", "threshold": 0.3}
      ]},
      "strength": {"type": "const", "value": 0.5},
      "constraints": {"cooldown_ticks": 50, "no_reentry_until_flat": false}
    }
  ],
  "exit_policies": [
    {"name": "exits", "rules": [
      {"name": "stop", "priority": 1,
       "condition": {"type": "comparison", "feature": "order_imbalance", "op": "<", "threshold": -0.2},
       "action": {"type": "close_all"}}
    ]}
  ],
  "risk_policy": {
    "max_position": 500, "inventory_cap": 1000,
    "position_sizing": {"mode": "fixed", "base_size": 100, "max_size": 500}
  }
}
```

**AST 노드 타입**: `const`, `feature`, `comparison`, `all` (AND), `any` (OR), `not`, `cross` (cross_above/below), `lag` (N-tick 지연 참조), `rolling` (구간 집계: mean/min/max), `persist` (조건 지속 확인: window 내 min_true 충족)

---

## Universe 평가 프로토콜

모든 적용 가능 종목에 적용 후:
- **Mean** net_pnl, sharpe_ratio
- **Median** net_pnl, sharpe_ratio
- **Std** (종목 간 분산)
- **Win rate** (수익 종목 비율)

### Latency 실험 축

| Latency | 프로필 | 대상 |
|---------|--------|------|
| 0ms | 이론적 최적 | 순수 알파 측정 |
| 50ms | Co-location | 기관 HFT |
| 100ms | 일반 기관 | 현실적 기관 |
| 500ms | 느린 기관 | API 기반 기관 |
| 1000ms | 리테일 | 개인 투자자 |

---

## 내부 구현: Layer 0~7

5-block 아키텍처의 각 블록은 내부적으로 Layer 0~7로 세분화되어 있다.
이 계층 구조는 구현 수준의 모듈 분리이며, 상위 블록 관점에서 이해한 뒤 필요 시 참조한다.

```
Block 1 (Data)                 → Layer 0: Data       — 수집, 정제, 동기화, 피처
Block 3 (Execution Planning)   → Layer 1: Signal     — 시그널 인터페이스
                                 Layer 2: Position   — 포지션 타겟, 리스크
                                 Layer 3: Order      — 주문 타입, 델타
                                 Layer 4: Execution  — 슬라이싱, 배치
Block 4 (Market Simulation)    → Layer 5: Simulator  — 체결, 수수료, 충격, latency
Block 5 (Evaluation)           → Layer 6: Evaluator  — PnL, 리스크, 실행 품질
                                 Layer 7: Validation — 백테스트 오케스트레이션
```

> 각 Layer의 모듈별 상세는 `PIPELINE.md`를 참조.

---

## Legacy / Archive

`archive/` 디렉토리에는 비활성 코드와 문서가 보관되어 있다.
이들은 런타임에서 참조되지 않으며, 히스토리 참고용이다.

| 경로 | 내용 |
|------|------|
| `archive/legacy_baselines/` | MicroAlphaStrategy, micro_alpha.py 등 |
| `archive/legacy_agents/` | llm_agents/ (LLM 기반 4-Agent 파이프라인) |
| `archive/docs/` | 과거 연구 제안서, 모델 명세, Agent 역할 명세 |
