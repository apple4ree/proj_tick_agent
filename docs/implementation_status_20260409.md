# proj_rl_agent 구현 상태 점검 (2026-04-09)

작성일: 2026-04-09
범위: 전략 생성 루프(`strategy_loop`) + 백테스트/리포트 레이어(`layer7_validation`)
기준 commit: 세션 시점 로컬 상태 (미커밋 변경사항 포함)

---

## 0. Executive Summary

| 항목 | 상태 |
|---|---|
| 전체 테스트 | 511개 전부 PASS (`pytest tests/ -q` 20.7s) |
| 두 실행 경로(`code` / `spec`) | 둘 다 CLI + 프로파일 + 런처 스크립트 완비 |
| 백테스트 산출물 | `outputs/backtests_code/`(≥50), `outputs/backtests_spec/`(10 runs) |
| HTML 리포트 자동 생성 | ReportBuilder에 hook, run_dir당 `report.html` |
| Strategy 내러티브 포함 | `strategy_info.json` → HTML "Strategy" / "LLM Feedback" 섹션 |
| Post-hoc 분석 섹션 | Fill Quality / Time-of-Day / Edge Analysis |
| Production path mock 오염 | 없음 (감사 완료, [§6](#6-프로덕션-격리-검증) 참고) |
| 알려진 기술 부채 | 테스트 mock fragility 1건 ([§7.1](#71-테스트-mock-fragility)) |

---

## 1. 실행 경로 개요

현재 두 가지 전략 생성 경로가 공존하며, 두 경로 모두 **실행 가능**한 상태이다.

### 1.1 Code-Centric (`--strategy-mode code`, 기본값)

```
Research Goal
    → LLM Code Generation (UPPER_CASE 상수 + generate_signal 함수)
    → Hard Gate (정적 분석)
    → Distribution Filter (entry frequency 0.1%~50%)
    → (선택) Optuna threshold 최적화
    → Backtest (PipelineRunner)
    → Feedback (Controller + LLM narrative)
    → Memory + RAG 저장
    → 반복
```

| 진입점 | 파일 |
|---|---|
| Shell launcher | [scripts/run_code_loop_live.sh](scripts/run_code_loop_live.sh) |
| CLI | [scripts/run_strategy_loop.py](scripts/run_strategy_loop.py) `--strategy-mode code` |
| Profile | [conf/profiles/code_loop.yaml](conf/profiles/code_loop.yaml) |
| 내부 메서드 | `LoopRunner.run()` ([src/strategy_loop/loop_runner.py](src/strategy_loop/loop_runner.py)) |

### 1.2 Spec-Centric (`--strategy-mode spec`)

```
Research Goal
    → Goal Decomposition
    → Planner LLM → strategy_text + StrategySpec (JSON)
    → Spec Review (structural validation)
    → Precode Eval (score > threshold이면 진행)
    → Implementer LLM → Python code
    → Hard Gate → Distribution Filter → Optuna
    → Backtest → Feedback → Memory + Plan record 저장
    → (parametric fail) 내부 code loop 재시도
    → (structural fail) 외부 plan loop 재시도
```

| 진입점 | 파일 |
|---|---|
| Shell launcher | [scripts/run_spec_loop_live.sh](scripts/run_spec_loop_live.sh) |
| CLI | [scripts/run_strategy_loop.py](scripts/run_strategy_loop.py) `--strategy-mode spec` |
| Profile | [conf/profiles/spec_loop.yaml](conf/profiles/spec_loop.yaml) |
| 내부 메서드 | `LoopRunner.run_spec_centric()` |

두 루프 모두 동일한 하위 구성요소(hard gate, distribution filter, PipelineRunner, FeedbackGenerator, MemoryStore)를 공유한다. 유일한 차이는 "코드를 어떻게 만드는가"(직접 생성 vs 스펙을 거쳐 생성)와 외부 loop 구조의 유무이다.

---

## 2. 백테스트 / 리포트 레이어 (`layer7_validation`)

### 2.1 파이프라인 구조

`PipelineRunner` ([src/evaluation_orchestration/layer7_validation/pipeline_runner.py](src/evaluation_orchestration/layer7_validation/pipeline_runner.py))는 다음 산출물을 `run_dir` 아래에 저장한다:

| 파일 | 용도 |
|---|---|
| `config.json` | 백테스트 설정 스냅샷 |
| `summary.json` | 집계 지표 (net_pnl, sharpe_ratio, max_drawdown, fill_rate, n_fills 등) |
| `realism_diagnostics.json` | queue/latency/cancel/slippage 진단 |
| `signals.csv` | 전략이 낸 원신호 |
| `orders.csv` | 부모 주문 |
| `fills.csv` | 실제 체결 (symbol/side/qty/price/fee/slippage_bps/latency_ms) |
| `market_quotes.csv` | 호가 스냅샷 (best_bid, best_ask, mid_price) |
| `pnl_series.csv` | 누적 PnL 시계열 |
| `pnl_entries.csv` | 포지션 단위 PnL 엔트리 |
| `plots/` | 기존 PNG 대시보드 (v1 이전 자산) |
| `report.html` | 자기완결형 인터랙티브 리포트 (§2.3) |
| `strategy_info.json` | 전략/피드백 메타 (spec-centric에서는 strategy_text 포함) |

저장 로직은 `ReportBuilder.save_results()`에 집중되어 있으며, HTML 리포트 생성 실패 시에도 backtest 자체는 warning 로그만 남기고 계속 진행한다(atomic `.tmp` → rename).

### 2.2 `strategy_info.json` 스키마 (이번 세션 추가)

```json
{
  "iteration": 3,
  "strategy_text": "Long when order-imbalance ...",
  "code": "TRADE_FLOW_IMBALANCE_EMA_THRESHOLD = ...",
  "feedback": {
    "verdict": "retry",
    "diagnosis_code": "fee_dominated",
    "severity": "parametric",
    "primary_issue": "...",
    "issues": [...],
    "suggestions": [...]
  }
}
```

기록 주체: [src/strategy_loop/loop_runner.py](src/strategy_loop/loop_runner.py) 모듈 레벨 `_write_strategy_info()`.
호출 위치:
- Code-centric IS feedback 직후 ([loop_runner.py:258](src/strategy_loop/loop_runner.py#L258))
- Spec-centric IS feedback 직후 ([loop_runner.py:664](src/strategy_loop/loop_runner.py#L664))

Code-centric은 `strategy_text=None`, spec-centric은 planner 출력의 `strategy_text`를 포함한다.

`_run_backtest_multi_code()`가 `tuple[dict, list[Path]]`로 반환하도록 변경되어 IS 백테스트의 run_dir 목록을 feedback 루프에서 활용할 수 있게 되었다. 4개 call site (code/spec × IS/OOS)는 모두 갱신되었으며 OOS는 `_` 로 무시한다.

### 2.3 HTML 리포트 (`html_report.py`)

자기완결형 단일 HTML 파일을 생성한다. CDN 없이 Plotly JS를 embed (`include_plotlyjs=True` for 메인 차트, `include_plotlyjs=False` for 하위 섹션).

**섹션 구성 (위에서 아래 순서):**

1. **Summary Cards** — net_pnl, sharpe_ratio, max_drawdown, fill_rate, n_fills
2. **Main Chart (2-row shared xaxis)**
   - Row 1: best_bid / best_ask 라인 + BUY/SELL fill 마커
   - Row 2: cumulative_net_pnl step 라인
   - 5000점 초과 시 `Scattergl`(WebGL) 전환, 10000점 초과 시 uniform downsample
3. **Strategy** — iteration 번호 + strategy_text + collapsible strategy code
4. **LLM Feedback** — verdict/diagnosis_code/severity/primary_issue 테이블 + issues/suggestions 리스트
5. **Fill Quality** — BUY/SELL slippage_bps box plot + 평균 slippage/latency/fee 카드
6. **Time-of-Day Performance** — 시간대(0~23)별 net_pnl 합산 막대 차트
7. **Edge Analysis** — 체결 후 T+1/5/10/30s 평균 mid price 이동 (bps, merge_asof 기반)
8. **Realism Diagnostics** — queue/latency/cancel 키-값 테이블

각 섹션은 데이터가 없거나 계산 실패 시 빈 문자열을 반환하고, 전체 리포트 생성이 실패하면 warning만 남기고 `None`을 반환한다.

---

## 3. Config / Profile 체계

### 3.1 프로파일 파일 ([conf/profiles/](conf/profiles/))

| 프로파일 | 용도 |
|---|---|
| `code_loop.yaml` | Code-centric 주력 |
| `spec_loop.yaml` | Spec-centric 주력 (이번 세션 신규) |
| `smoke.yaml` | 빠른 smoke 테스트 |
| `dev.yaml` / `prod.yaml` | 개발/배포 환경 기본값 |
| `walk_forward_smoke.yaml` / `walk_forward_stronger.yaml` | Walk-forward 백테스트 |
| `promotion_canary.yaml` | 카나리 배포 |

### 3.2 `spec_loop` 섹션 스키마

`spec_loop.yaml`은 `spec_loop:` 블록을 통해 CLI의 기본값을 주입한다:
- `model` — LLM 모델 (기본 `gpt-4o`)
- `max_plan_iterations` — 외부 plan loop 제한 (기본 10)
- `max_code_attempts` — 내부 code loop 제한 (기본 3)
- `precode_eval_threshold` — 진행 기준 (기본 0.50)
- `research_goal` — 기본 goal (shell 스크립트의 `GOAL` env로 override 가능)
- `symbols` — 기본 심볼 (쉼표 구분)

`scripts/run_strategy_loop.py`가 프로파일 파싱 후 CLI 인자가 없을 때 이 값을 폴백으로 사용한다.

---

## 4. 테스트 커버리지

### 4.1 전체 현황

```
pytest tests/ -q
→ 511 passed in 20.70s
```

### 4.2 주요 테스트 그룹

| 파일 | 테스트 대상 | 상태 |
|---|---|---|
| `test_html_report.py` | HTML 리포트 생성, 섹션 포함, downsampling, 누락 파일 resilience | 9 PASS |
| `test_loop_routing.py` | Spec-centric 라우팅 (pass / structural / parametric) | PASS |
| `test_loop_e2e_fake.py` | Fake LLM 클라이언트로 E2E 플로우 검증 | PASS |
| `test_feedback_orchestration_split.py` | Controller + Generator 분리 로직 | PASS |
| `test_pipeline_runner*.py` | 백테스트 파이프라인 | PASS |
| `test_report_builder*.py` | 산출물 저장 | PASS |
| `test_distribution_filter.py` | Entry frequency 분포 필터 | PASS |
| `test_hard_gate.py` | 정적 분석 gate | PASS |
| `test_spec_review.py` | Spec 구조 검증 | PASS |
| `test_threshold_optimizer.py` | Optuna 기반 상수 탐색 | PASS |

### 4.3 이번 세션에 수정된 테스트

`_run_backtest_multi_code`의 반환 타입이 `dict` → `tuple[dict, list[Path]]`로 변경되면서, 21곳의 monkeypatch mock이 새 튜플 포맷을 반환하도록 일괄 업데이트되었다:

| 파일 | 수정된 mock 수 |
|---|---|
| `tests/test_loop_routing.py` | 8곳 (lambda 5 + nested def 2 + 인라인 callable 1) |
| `tests/test_loop_e2e_fake.py` | 11곳 (전부 lambda) |
| `tests/test_feedback_orchestration_split.py` | 1곳 (`_fake_backtest`) |

---

## 5. 최근 세션에서의 주요 변경사항

시간 순서대로:

1. **HTML 리포트 v1** — [html_report.py](src/evaluation_orchestration/layer7_validation/html_report.py) 신규 작성, ReportBuilder에 훅 연결, unit tests 9개 추가
2. **Spec-Centric CLI 통합** — `run_strategy_loop.py`에 `--strategy-mode {code,spec}` + `--max-plan-iterations` / `--max-code-attempts` / `--precode-eval-threshold` 인자 추가
3. **`conf/profiles/spec_loop.yaml`** — spec loop 전용 프로파일 신규 작성
4. **`scripts/run_spec_loop_live.sh`** — spec loop용 라이브 런처 스크립트 신규 작성
5. **Post-hoc 분석 섹션** — html_report.py에 Fill Quality / Time-of-Day / Edge Analysis 3개 섹션 추가
6. **Strategy / Feedback 섹션** — `_write_strategy_info()` → `strategy_info.json` → HTML 2개 섹션 (이번 세션 마무리 작업)
7. **Mock 타입 업데이트** — 위 6단계에서 signature 변경으로 인한 테스트 mock 21곳 일괄 수정

---

## 6. 프로덕션 격리 검증

`scripts/run_spec_loop_live.sh` 실행 경로에 테스트 mock이 섞일 가능성이 있는지 감사한 결과:

| 검사 | 결과 |
|---|---|
| `src/` 내 `mock`/`fake`/`stub` 실제 구현 | **없음** (3건 매치 모두 docstring/comment) |
| `src/` 내 `unittest`/`monkeypatch`/`pytest` import | **없음** |
| `src/`에서 `tests.*` 패키지 import | **없음** |
| `scripts/`에서 `tests.*` 패키지 import | **없음** |
| `scripts/run_spec_loop_live.sh` 내 mock 키워드 | **없음** |
| `run_strategy_loop.py` 의 LLM 클라이언트 | [run_strategy_loop.py:94](scripts/run_strategy_loop.py#L94) 에서 실제 `OpenAIClient(model=args.model)` 인스턴스화 |

**결론**: 테스트의 `monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", ...)` 패치는 pytest 픽스처 범위 내에서만 유효하며, 프로덕션 실행 경로와 완전히 격리되어 있다. 실행 트레이스:

```
run_spec_loop_live.sh
  ↓ bash
run_strategy_loop.py
  ↓ 실제 OpenAIClient 생성
  ↓ LoopRunner.run_spec_centric()
  ↓ LoopRunner._run_backtest_multi_code()      ← 실제 메서드 (mock 없음)
  ↓ PipelineRunner(...).run(states)             ← 실제 시뮬레이터
```

---

## 7. 알려진 기술 부채 / 후속 작업 제안

### 7.1 테스트 Mock Fragility

현재 테스트 21곳이 `LoopRunner._run_backtest_multi_code` private 메서드를 직접 monkeypatch한다. 이번 세션에서 signature를 한 번 바꿨을 때 21곳을 전부 수정해야 했는데, 이 패턴 자체가 design smell이다.

**단기 대응**: `tests/conftest.py`에 헬퍼를 중앙화
```python
def fake_backtest_mock(summary: dict):
    return lambda *a, **kw: (summary, [])
```
→ 다음 signature 변경 시 수정 포인트 1곳으로 축소.

**장기 대응**: `LoopRunner` 생성자에 `BacktestExecutor` protocol 주입. public boundary에서 mock 하도록 리팩터링.

현재는 단기 대응 미적용 상태 (모든 mock이 새 tuple 포맷으로 직접 수정됨).

### 7.2 Run ID 이중 생성 (과거 세션에서 식별됨)

`loop_runner.py`의 8-char `run_id`(uuid4()[:8])와 `pipeline_runner.py`의 full UUID는 서로 다른 공간에서 생성된다. 이번 세션에서 `strategy_info.json`을 run_dir 내부에 직접 기록하는 방식을 택하면서 이 gap은 실용적으로는 우회되었지만, 두 ID의 직접 링크는 여전히 없다.

### 7.3 Code-Centric 경로의 strategy_text 부재

Spec-centric은 planner가 `strategy_text`(전략 설명)를 자연어로 생성하지만, code-centric은 이에 대응하는 필드가 없다. 현재는 `strategy_info.json`에 `strategy_text=None`으로 기록된다. 필요하다면 code-centric LLM prompt도 강제로 자연어 요약을 포함하도록 확장할 수 있다.

### 7.4 OOS run_dir는 `strategy_info.json` 미기록

구현 상 IS 백테스트의 run_dir에만 `strategy_info.json`을 쓰고, OOS run_dir에는 쓰지 않는다(`oos_summary, _ = ...`). OOS는 검증용이라 필요하지 않다는 판단. 필요 시 OOS에도 동일한 패턴으로 확장 가능.

### 7.5 실험 상태 (2026-04-06 meeting_note 기준)

- Code-centric에서 `fail 32 / retry 2 / pass 0`
- 현재까지 profitable strategy 확보 실패
- 1차 원인: signal edge 자체가 약함
- 2차 원인: execution cost 지배 (commission + slippage + child order churn)
- 3차 원인: feedback/monitoring 루프가 code loop 내부에서 완전히 닫히지 않음

이 상태는 [meeting_note_20260406.md](docs/meeting_note_20260406.md)에서 자세히 다루었다. 본 점검 파일은 "프레임워크 구현 상태"를 다루고, 실험 결과 해석은 그 문서를 참고한다.

---

## 8. 핵심 파일 인덱스

### 8.1 전략 루프
- [src/strategy_loop/loop_runner.py](src/strategy_loop/loop_runner.py) — 양쪽 루프 오케스트레이션 (1095 lines)
- [src/strategy_loop/feedback_generator.py](src/strategy_loop/feedback_generator.py) — LLM narrative 피드백
- [src/strategy_loop/feedback_controller.py](src/strategy_loop/feedback_controller.py) — deterministic verdict 분류
- [src/strategy_loop/distribution_filter.py](src/strategy_loop/distribution_filter.py) — entry frequency pre-filter
- [src/strategy_loop/hard_gate.py](src/strategy_loop/hard_gate.py) — 정적 코드 gate
- [src/strategy_loop/planner_prompt_builder.py](src/strategy_loop/planner_prompt_builder.py) — spec-centric planner 프롬프트
- [src/strategy_loop/implementer_prompt_builder.py](src/strategy_loop/implementer_prompt_builder.py) — spec-centric implementer 프롬프트
- [src/strategy_loop/spec_schema.py](src/strategy_loop/spec_schema.py) — StrategySpec 정의
- [src/strategy_loop/spec_review.py](src/strategy_loop/spec_review.py) — spec 구조 검증
- [src/strategy_loop/threshold_optimizer.py](src/strategy_loop/threshold_optimizer.py) — Optuna 기반 상수 탐색

### 8.2 백테스트 레이어
- [src/evaluation_orchestration/layer7_validation/pipeline_runner.py](src/evaluation_orchestration/layer7_validation/pipeline_runner.py) — 백테스트 오케스트레이터
- [src/evaluation_orchestration/layer7_validation/report_builder.py](src/evaluation_orchestration/layer7_validation/report_builder.py) — artifact 저장 (281 lines)
- [src/evaluation_orchestration/layer7_validation/html_report.py](src/evaluation_orchestration/layer7_validation/html_report.py) — 인터랙티브 리포트 생성 (415 lines)
- [src/evaluation_orchestration/layer7_validation/backtest_config.py](src/evaluation_orchestration/layer7_validation/backtest_config.py) — BacktestConfig / BacktestResult

### 8.3 진입점
- [scripts/run_strategy_loop.py](scripts/run_strategy_loop.py) — 공통 CLI
- [scripts/run_code_loop_live.sh](scripts/run_code_loop_live.sh) — Code loop 런처
- [scripts/run_spec_loop_live.sh](scripts/run_spec_loop_live.sh) — Spec loop 런처 (신규)
- [scripts/run_code_loop_smoke.sh](scripts/run_code_loop_smoke.sh) — Smoke 테스트

### 8.4 설정
- [conf/profiles/code_loop.yaml](conf/profiles/code_loop.yaml)
- [conf/profiles/spec_loop.yaml](conf/profiles/spec_loop.yaml) (신규)
- [conf/profiles/smoke.yaml](conf/profiles/smoke.yaml)
