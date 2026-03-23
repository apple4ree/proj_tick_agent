# 비동기 전략 생성 및 실행 아키텍처

## 개요

본 프로젝트는 하나의 코드베이스 안에서 두 개의 독립 서브시스템을 운영한다.

1. **전략 생성 서브시스템 (Generation Plane)**
   - OpenAI API 기반 multi-agent 또는 deterministic generator로 `StrategySpec`을 생성한다.
   - 생성 과정은 느리고 비결정적이어도 허용된다.
   - 생성 결과는 즉시 실행되지 않고 registry에 저장된다.

2. **실행 서브시스템 (Execution Plane)**
   - registry에 저장된 승인된 `StrategySpec`을 읽어 backtest, universe evaluation, paper/live trading에 적용한다.
   - 실행은 재현 가능하고 안정적이어야 한다.
   - 실행기는 항상 특정 strategy version에 고정된 spec만 사용한다.

핵심 목표는 전략 생성과 실행을 직접 연결하지 않고, `registry + job queue + versioned spec` 구조로 느슨하게 결합하는 것이다.

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

### Block ↔ Plane 매핑

5개 블록은 Generation Plane과 Execution Plane에 다음과 같이 매핑된다.

```
┌─ Generation Plane (Control) ────────────────────────┐
│  Strategy Block: 전략 생성·검토·컴파일·레지스트리     │
│  Orchestration (Block 5 일부): Job Queue, Worker     │
└─────────────────────────────────────────────────────┘
          ↓ StrategySpec (versioned, approved)
┌─ Execution Plane ───────────────────────────────────┐
│  Data Block:               원시 데이터 → MarketState │
│  Execution Planning Block: Signal → Order            │
│  Market Simulation Block:  체결 시뮬레이션            │
│  Evaluation Block (Block 5 일부): PnL, 리스크, 요약  │
└─────────────────────────────────────────────────────┘
```

- **Generation Plane** = Strategy Block + Orchestration의 generation worker 부분
- **Execution Plane** = Data Block + Execution Planning Block + Market Simulation Block + Evaluation 부분
- `StrategySpec`이 두 plane 사이의 계약이다. Registry가 단일 진실 공급원.

---

## 설계 목표

- 전략 생성과 backtest/trading을 비동기적으로 수행한다.
- 생성 결과는 반드시 구조화된 `StrategySpec`으로 저장한다.
- 실행기는 승인된 spec만 사용한다.
- 모든 실행은 version-pinned spec을 기준으로 재현 가능해야 한다.
- generation failure와 execution failure를 별도 job 단위로 다룬다.

---

## 시스템 구성

### 1. Strategy Generation Plane

역할:
- research goal 수신
- strategy idea 생성
- signal/risk 규칙 초안 생성
- review 수행
- 최종 `StrategySpec` 조립 및 저장

특성:
- OpenAI API 호출 포함 가능
- 느린 작업 허용
- 재시도 가능
- 비동기 worker에 적합

대응 5-Block: **Strategy** (Block 2)

주요 모듈:
- `src/strategy_block/strategy_generation/`
- `src/strategy_block/strategy_review/`
- `src/strategy_block/strategy_specs/`

### 2. Strategy Registry

역할:
- 생성된 전략 명세의 단일 진실 공급원
- 버전 관리
- 상태 관리
- promotion 상태 관리

저장 대상:
- spec JSON
- generation trace
- review 결과
- static validation 결과
- deployment metadata

대응 5-Block: **Strategy** (Block 2)

주요 모듈:
- `src/strategy_block/strategy_registry/`

### 3. Execution / Evaluation Plane

역할:
- registry에서 approved spec 조회
- spec compile
- single-symbol backtest
- universe backtest
- 결과 요약
- 필요 시 paper/live trading

특성:
- 결정적이어야 함
- version-pinned spec만 사용
- latency / fee / impact 조건을 통제 가능해야 함

대응 5-Block: **Data** (Block 1) + **Execution Planning** (Block 3) + **Market Simulation** (Block 4) + **Evaluation** (Block 5)

주요 모듈:
- `src/strategy_block/strategy_compiler/`
- `src/data/layer0_data/` ~ `src/evaluation_orchestration/layer7_validation/`
- `scripts/backtest.py`
- `scripts/backtest_strategy_universe.py`
- `scripts/summarize_universe_results.py`

### 4. Orchestration / Job Layer

역할:
- generation과 execution을 직접 호출로 연결하지 않고 job/event로 연결
- 비동기 worker가 각 작업을 처리
- 실패/재시도/상태 추적

초기 구현 원칙:
- file-based queue (atomic rename) 기반
- 이후 필요 시 Redis/Celery/RQ 등으로 확장 가능

대응 5-Block: **Evaluation & Orchestration** (Block 5)

주요 모듈:
- `src/evaluation_orchestration/orchestration/`

---

## 전략 명세 생명주기

권장 상태:

- `draft`
- `reviewed`
- `approved`
- `rejected`
- `promoted_to_backtest`
- `promoted_to_live`
- `archived`

설명:
- `draft`: 생성 직후
- `reviewed`: LLM/static review 완료
- `approved`: 실행 가능 상태
- `rejected`: 실행 금지
- `promoted_to_backtest`: 배치 백테스트 대상으로 지정
- `promoted_to_live`: paper/live trading 대상으로 지정
- `archived`: 더 이상 사용하지 않음

중요 원칙:
- 실행기는 `approved` 이상 상태의 spec만 사용한다.
- live/paper trading은 `promoted_to_live` 상태만 사용한다.
- `latest` 자동 사용은 금지한다.

---

## 비동기 워크플로우

### 1. 전략 생성

1. 사용자가 research goal 제출
2. generation job 생성
3. generation worker가 goal 처리
4. `StrategySpec` 생성
5. review/static validation 수행
6. registry 저장
7. 상태 결정 (`approved` 또는 `rejected`)

### 2. 백테스트

1. approved spec 선택 또는 promotion
2. backtest job 생성
3. backtest worker가 spec version 고정 후 실행
4. 결과 저장
5. summary 및 artifact 생성

### 3. live/paper trading

1. `promoted_to_live` 상태 spec 선택
2. execution worker가 해당 version 로드
3. live/paper runtime에서 실행
4. 결과/로그 저장

---

## Registry 메타데이터 설계

전략별 최소 메타데이터:

- `strategy_id`
- `name`
- `version`
- `status`
- `created_at`
- `generation_backend`
- `generation_mode`
- `review_status`
- `static_review_passed`
- `approved_for_backtest`
- `approved_for_live`
- `trace_path`
- `spec_path`

실행 결과 메타데이터:

- `run_id`
- `strategy_id`
- `strategy_version`
- `run_type` (`single_backtest`, `universe_backtest`, `paper`, `live`)
- `start_time`
- `end_time`
- `config_hash`
- `result_path`

---

## 모듈 경계

### Generation Layer가 해야 하는 것

- 아이디어 생성
- 규칙 초안 생성
- risk/exit 초안 생성
- review 및 spec 조립
- trace 기록

### Generation Layer가 하면 안 되는 것

- 직접 backtest 실행
- 직접 live order 제출
- 실시간 state 처리

### Execution Layer가 해야 하는 것

- spec compile
- state sequence 처리
- signal/order/fill/pnl 계산
- latency-aware evaluation

### Execution Layer가 하면 안 되는 것

- 새로운 전략 생성
- spec 구조 수정
- registry 상태 임의 변경

---

## 실행 구조

### 설정 분리

설정은 YAML config, 실행 진입은 shell script, Python CLI는 얇은 실행기.

```text
conf/
│
│ ── 기본 config stack (load_config가 순서대로 deep-merge) ──
├── app.yaml              ← 1. 공통 앱 설정 (env, log_level)
├── paths.yaml            ← 2. 경로 설정 (data, registry, jobs, outputs)
├── generation.yaml       ← 3. 전략 생성 설정 (Generation Plane)
├── backtest_base.yaml    ← 4. 백테스트 공통 기본값 (initial_cash, seed, fee 등)
├── backtest_worker.yaml  ← 5. 백테스트 워커 전용 (latency sweep, review gate)
├── workers.yaml          ← 6. Worker 동작 설정 (poll interval, once)
│
│ ── 선택적 override (CLI 인자로 지정 시) ──
├── profiles/
│   ├── dev.yaml          ← 7. --profile dev (base 위에 merge)
│   ├── smoke.yaml        ← 7. --profile smoke
│   └── prod.yaml         ← 7. --profile prod
│
│ ── config stack에 포함되지 않는 별도 파일 ──
└── backtest_core.yaml    ← BacktestConfig.from_yaml() 전용 (qlib 스타일 상세 설정)
```

- `--config path/to/override.yaml`은 위 스택의 가장 마지막(8번)에 merge된다.
  단독 설정 파일이 아니라, 기존 스택의 특정 값만 덮어쓰는 override이다.

원칙:
- **정책/환경/경로** → YAML config
- **실행 대상** (research goal, symbol, version) → CLI 인자 또는 job payload
- API 키는 환경 변수로 주입 (`${OPENAI_API_KEY}`)

### 디렉토리 구조

```text
proj_rl_agent/
├── src/
│   ├── data/                    ← Data Block
│   │   └── layer0_data/
│   ├── strategy_block/          ← Strategy Block
│   │   ├── strategy_generation/
│   │   ├── strategy_review/
│   │   ├── strategy_specs/
│   │   ├── strategy_compiler/
│   │   ├── strategy_registry/
│   │   └── strategy/
│   ├── execution_planning/      ← Execution Planning Block
│   │   ├── layer1_signal/ ~ layer4_execution/
│   ├── market_simulation/       ← Market Simulation Block
│   │   └── layer5_simulator/
│   ├── evaluation_orchestration/ ← Evaluation & Orchestration Block
│   │   ├── layer6_evaluator/
│   │   ├── layer7_validation/
│   │   └── orchestration/
│   └── utils/config.py        ← YAML config loader
├── conf/                       ← YAML 설정
├── scripts/
│   ├── run_generation_worker.sh    ← Shell 런처
│   ├── run_backtest_worker.sh
│   ├── submit_generation_job.sh
│   ├── submit_backtest_job.sh
│   ├── run_local_stack.sh          ← 로컬 스택 (두 Worker 동시)
│   ├── run_generation_worker.py    ← 얇은 Python 실행기
│   ├── run_backtest_worker.py
│   ├── generate_strategy.py        ← Job submitter
│   └── submit_backtest_job.py
├── jobs/                       ← File-based job queue
├── strategies/                 ← Strategy registry
└── outputs/                    ← 결과 artifacts
```

---

## 구현 현황

### Phase 1 — ✅ 완료
- registry 상태 필드 확장 (StrategyMetadata, StrategyStatus)
- generation과 execution 경계 명문화
- spec lifecycle 정의 (draft → reviewed → approved → promoted)

### Phase 2 — ✅ 완료
- file-based job queue (atomic rename)
- generation worker / backtest worker 분리

### Phase 3 — ✅ 완료
- approved spec만 backtest 가능하도록 execution gate 강제
- version-pinned execution 강제
- static reviewer hard gate

### Phase 3.5 — ✅ 완료
- YAML config 기반 설정 관리
- Shell 런처 기반 실행 구조
- Profile-based 환경 분리 (dev/smoke/prod)

### Phase 4 — 예정
- paper/live trading worker 분리
- promotion workflow 추가

### Phase 5 — 예정
- SQLite/Redis 기반 orchestration 고도화
- monitoring / alerting 추가

---

## 핵심 설계 원칙 요약

- generation과 execution은 분리한다.
- `StrategySpec`가 두 서브시스템 사이의 계약이다.
- registry가 단일 진실 공급원이다.
- 실행은 항상 version-pinned다.
- live/paper는 승인 및 promotion된 spec만 사용한다.
- generation failure와 execution failure는 별도 job으로 관리한다.

---

## 결론

본 프로젝트는 5-block 아키텍처(Data → Strategy → Execution Planning → Market Simulation → Evaluation & Orchestration)를 상위 설계로 삼고,
내부적으로는 Generation Plane(전략 생성)과 Execution Plane(전략 실행)으로 분리된 구조를 갖는다.

이 구조를 통해
- OpenAI 기반 전략 생성의 유연성
- 백테스트 및 trading 실행의 안정성
- 실험 재현성과 버전 통제

를 동시에 확보할 수 있다.

> 내부 구현은 Layer 0~7로 세분화되어 있다. 상세는 `PIPELINE.md`를 참조.
