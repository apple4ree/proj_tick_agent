# layer7_validation/ — 백테스트 파이프라인 (Layer 7)

PipelineRunner가 Layer 0~6를 조립해 단일 종목 백테스트를 실행한다.

## Document Scope

이 문서는 Layer7의 **현재 canonical 동작(Tier 1)** 을 설명한다.
baseline/회귀 기준은 Tier 2 freeze 문서를 따른다.

- `docs/analysis/benchmark_freeze_protocol.md`
- `docs/analysis/benchmark_freeze_results.md`
- `docs/analysis/benchmark_freeze_baselines.md`

## 핵심 구성요소

- `pipeline_runner.py` (`PipelineRunner`)
- `backtest_config.py` (`BacktestConfig`, `BacktestResult`)
- `fill_simulator.py` (`FillSimulator`)
- `report_builder.py` (`ReportBuilder`)
- `component_factory.py` (`ComponentFactory`)
- `reproducibility.py` (`ReproducibilityManager`)

## Current Realism Stack (Canonical)

1. observation lag
- source of truth: `market_data_delay_ms`
- strategy가 보는 상태(`observed_state`)만 지연

2. decision latency
- source of truth: `decision_compute_ms`
- decision path lookup delay = `market_data_delay_ms + decision_compute_ms`

3. venue latency (nested `latency`)
- `order_submit_ms`: venue 도착 전 fill/queue 대상 아님
- `cancel_ms`: cancel effective 전까지 live 유지
- `order_ack_ms`: reporting/status용 (`order_ack_used_for_fill_gating=false`)

4. submit/cancel minimal lifecycle gating
- full event-driven venue simulator는 아님

5. queue-aware passive fill
- FillSimulator가 queue semantics owner
- queue gate 통과 후 matching path 진입

6. partial fill
- exchange model과 함께 부분 체결 지원

7. replace semantics
- 현재 intentional minimal-immediate model
- staged replace lifecycle은 deferred

8. bounded state-history retention
- delay + runtime lookback 기반으로 history pruning

## Time Semantics

- 공식 resample: `1s`, `500ms`
- canonical tick interval:
  - `1s -> 1000ms`
  - `500ms -> 500ms`
- `tick != latency`
- fill path는 true-state 기준 유지

`latency_ms`는 legacy shorthand:
- nested `latency`가 없을 때만 submit/ack/cancel 파생
- `market_data_delay_ms`를 파생하지 않음

## Artifact Contract

### summary.json (compact)
대표 핵심 필드:
- cadence/tick: `resample_interval`, `canonical_tick_interval_ms`
- delay: `configured_market_data_delay_ms`, `configured_decision_compute_ms`, `effective_delay_ms`, `avg_observation_staleness_ms`
- queue/history: `queue_model`, `queue_position_assumption`, `state_history_max_len`, `strategy_runtime_lookback_ticks`
- lifecycle: `signal_count`, `parent_order_count`, `child_order_count`, `cancel_rate`, `avg_child_lifetime_seconds`
- venue latency config: `configured_order_submit_ms`, `configured_order_ack_ms`, `configured_cancel_ms`, `latency_alias_applied`

### realism_diagnostics.json (detailed aggregate)
고정 섹션:
- `observation_lag`
- `decision_latency`
- `tick_time`
- `lifecycle`
- `queue`
- `latency`
- `cancel_reasons`
- `timings`
- `config_snapshot`

원칙:
- always-on aggregate 중심
- per-order full trace는 diagnostics 계약 범위 밖

## Visualization (Static Workflow)

현재 plot 산출물:
- `overview.png`
- `signal_analysis.png`
- `execution_quality.png`
- `dashboard.png`
- `intraday_cumulative_profit.png`
- `trade_timeline.png`
- `equity_risk.png`
- `realism_dashboard.png`

artifact 누락 시 degraded fallback plot을 저장하고 전체 생성은 유지한다.

## Freeze Reference

Phase 4 snapshot:
- `outputs/benchmarks/phase4_benchmark_freeze.json`
- `outputs/benchmarks/phase4_benchmark_freeze.md`

## Known Limitations / Deferred Scope

- full staged replace state machine deferred
- deeper queue instrumentation beyond aggregate deferred
- feedback loop는 aggregate-only
- full universe operational guarantee는 freeze scope 밖
