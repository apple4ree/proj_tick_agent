# PIPELINE.md — Current Canonical Pipeline (Phase 4 Freeze)

이 문서는 **현재 동작 기준(Tier 1)** 파이프라인 계약을 설명한다. 과거 실험 기록은 historical analysis로 분리한다.

## Authoritative Hierarchy

### Tier 1 — Current Canonical Behavior
아래 문서들이 현재 구현/테스트 기준의 상위 계약이다.

- `PIPELINE.md`
- `scripts/README.md`
- `src/strategy_block/strategy_generation/README.md`
- `src/strategy_block/strategy_review/README.md`
- `src/evaluation_orchestration/layer7_validation/README.md`

### Tier 2 — Freeze / Baseline Contracts
현재 baseline 고정값과 회귀 기준은 아래 문서를 따른다.

- `docs/analysis/benchmark_freeze_protocol.md`
- `docs/analysis/benchmark_freeze_results.md`
- `docs/analysis/benchmark_freeze_baselines.md`
- `outputs/benchmarks/phase4_benchmark_freeze.json`
- `outputs/benchmarks/phase4_benchmark_freeze.md`

### Tier 3 — Historical Analysis
`docs/analysis/*.md`의 나머지 문서는 과거 실험/분석 맥락이다. 현재 canonical behavior의 1차 출처가 아니다.

## End-to-End Flow (Current)

```
generation -> review/repair -> backtest -> reporting/plots
```

### 1) Generation
진입점: `scripts/generate_strategy.py`

- backend: `template | openai`
- output: `StrategySpecV2` + generation trace metadata
- OpenAI 경로는 중간 `StrategyPlan`을 생성하고 deterministic lowering으로 spec 생성
- generation prompt에 canonical backtest constraint summary 주입
  - cadence (`tick = resample step`)
  - observation/decision delay
  - venue latency
  - queue semantics
  - replace minimal-immediate note
  - low-churn execution preference
- short-horizon 전략은 execution policy 명시를 강하게 유도

주의:
- generation은 environment-aware이지만 backtest semantics owner는 아니다.
- runtime semantics ownership은 backtest layer에 있다.

### 2) Review / Repair
진입점: `scripts/review_strategy.py`

모드:
- `static`
- `llm-review`
- `auto-repair`

핵심 계약:
- final hard gate는 항상 static reviewer
- LLM review는 semantic critique 전용
- repair는 constrained `RepairPlan` + deterministic patcher로만 적용
- auto-repair 이후 static re-review로 최종 통과 여부 결정

env/feedback awareness:
- static reviewer는 optional `backtest_environment`를 받아 wall-clock-aware gate 수행
- review/repair LLM prompt에는 canonical environment summary + optional feedback summary 주입
- post-backtest feedback는 aggregate-only (`summary.json`, `realism_diagnostics.json`)를 사용

review artifact (llm-review/auto-repair 기본 저장):
- `static_review.json`
- `llm_review.json`
- `repair_plan.json`
- `repaired_spec.json`
- `final_static_review.json`

### 3) Backtest (Layer7 Validation)
진입점:
- `scripts/backtest.py` (single symbol)
- `scripts/backtest_strategy_universe.py` (universe)

현행 realism stack:
- observation lag: `market_data_delay_ms`
- decision latency: `decision_compute_ms`
- venue latency: `latency.order_submit_ms`, `latency.order_ack_ms`, `latency.cancel_ms`
- submit/cancel minimal lifecycle gating
- queue-aware passive fill + partial fill
- replace model: minimal immediate (staged replace deferred)
- bounded state-history retention

시간 의미:
- decision path는 delayed observed-state 기준
- fill/matching path는 true-state 기준
- `latency_ms` flat field는 legacy shorthand (nested `latency`가 없을 때만)

공식 resample:
- `1s`
- `500ms`

### 4) Reporting / Visualization
ReportBuilder 산출물:
- `summary.json` (compact 핵심 지표)
- `realism_diagnostics.json` (상세 aggregate)
- `signals.csv`, `orders.csv`, `fills.csv`, `pnl_series.csv`, `market_quotes.csv`
- `plots/` static workflow

현재 static plot set:
- `overview.png`
- `signal_analysis.png`
- `execution_quality.png`
- `dashboard.png`
- `intraday_cumulative_profit.png`
- `trade_timeline.png`
- `equity_risk.png`
- `realism_dashboard.png`

일부 artifact 누락 시 degraded/fallback plot을 저장하고 전체 생성은 유지한다.

## Public CLI Surface (Current)

- `scripts/generate_strategy.py`
- `scripts/review_strategy.py`
- `scripts/backtest.py`
- `scripts/backtest_strategy_universe.py`
- `scripts/run_generate_review_backtest.sh`

세부 옵션은 `scripts/README.md`를 canonical source로 본다.

## Freeze and Regression Anchors

Phase 4에서 아래 계약을 baseline으로 고정했다.

- prompt contract (generation/review/repair)
- review pipeline result contract
- `summary.json` core field presence
- `realism_diagnostics.json` core section/nested key presence
- 핵심 plot 생성 계약 (`overview.png`, `trade_timeline.png`, `equity_risk.png`, `realism_dashboard.png`)

참조:
- `docs/analysis/benchmark_freeze_protocol.md`
- `docs/analysis/benchmark_freeze_results.md`
- `docs/analysis/benchmark_freeze_baselines.md`
- `outputs/benchmarks/phase4_benchmark_freeze.json`

## Known Limitations / Deferred Scope

- full staged replace state machine은 deferred
- deeper queue instrumentation(aggregate beyond)은 deferred
- post-backtest feedback loop는 aggregate-only (raw CSV trace 주입 없음)
- full universe operational guarantee는 freeze scope 밖
- live/replay LLM runtime variance 존재 (mock mode가 deterministic baseline)

## Related Docs

- `scripts/README.md`
- `src/strategy_block/strategy_generation/README.md`
- `src/strategy_block/strategy_review/README.md`
- `src/evaluation_orchestration/layer7_validation/README.md`
- `docs/README.md`
