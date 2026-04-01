# layer7_validation/ — 백테스트 파이프라인 (Layer 7)

`PipelineRunner`가 Layer 0~6를 조립해 단일 종목 백테스트를 실행한다.

## 핵심 구성요소

| 파일 | 역할 |
|------|------|
| `pipeline_runner.py` | `PipelineRunner` — 전체 백테스트 실행 |
| `backtest_config.py` | `BacktestConfig`, `BacktestResult` |
| `fill_simulator.py` | `FillSimulator` — ChildOrder → FillEvent |
| `report_builder.py` | `ReportBuilder` — 산출물 생성 |
| `component_factory.py` | `ComponentFactory` — 설정 기반 컴포넌트 조립 |
| `reproducibility.py` | seed, config hash 관리 |
| `queue_models/` | 5종 대기열 모델 |

## PipelineRunner 실행 흐름

```
PipelineRunner.run(config, strategy, states)
  for each MarketState (true_state):
    1. observed_state = states[bisect(timestamps, t - delay)]
    2. signal = strategy.generate_signal(observed_state)
    3. target = ExposureController(signal)
    4. orders = OrderScheduler(target, current_positions)
    5. fills = FillSimulator(orders, true_state)
    6. Bookkeeper.record(fills)
    7. if t % 60 == 0: positions_history.append(...)  # 성능 샘플링
  → ReportBuilder.build() → summary.json / CSVs / plots
```

## Realism 파라미터

| 파라미터 | 기본값 | 역할 |
|---------|--------|------|
| `market_data_delay_ms` | 0 | observation lag |
| `decision_compute_ms` | 0 | 결정 지연 |
| `latency.order_submit_ms` | 0 | 주문 제출 지연 |
| `latency.order_ack_ms` | 0 | 주문 확인 지연 |
| `latency.cancel_ms` | 0 | 취소 지연 |

## 산출물

`outputs/backtests/{run_id}/`:
- `summary.json` — net_pnl, sharpe, mdd, fill_rate 등
- `realism_diagnostics.json` — 상세 aggregate
- `signals.csv`, `orders.csv`, `fills.csv`, `pnl_series.csv`, `market_quotes.csv`
- `plots/dashboard.png`, `intraday_cumulative_profit.png`, `trade_timeline.png`

plot은 `visualize.py`가 없으면 생성을 건너뛴다.
